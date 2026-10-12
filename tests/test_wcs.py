"""WCS conformance of the deployed service: boots a real GeoServer on fabricated data with known pixel values."""
import re
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import rasterio
import requests

from chlwcs import geoserver, geotiff
from tests.helpers import GS_PORT, chl_value

pytestmark = pytest.mark.geoserver

PORT = GS_PORT
BASE = f"http://127.0.0.1:{PORT}/geoserver"
OWS = f"{BASE}/ows"
COVERAGE = "ocean__chl"
NODATA = geotiff.NODATA
NS = {"wcs": "http://www.opengis.net/wcs/2.0", "ows": "http://www.opengis.net/ows/2.0",
      "gml": "http://www.opengis.net/gml/3.2", "gmlcov": "http://www.opengis.net/gmlcov/1.0",
      "swe": "http://www.opengis.net/swe/2.0"}


def ows(**params):
    return requests.get(OWS, params=params, timeout=120)


def gc(*subsets, **extra):
    params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageId", COVERAGE),
              ("format", "image/tiff")] + [("subset", s) for s in subsets] + list(extra.items())
    return requests.get(OWS, params=params, timeout=120)


def read_tiff(response):
    assert response.status_code == 200 and response.content[:2] in (b"II", b"MM"), response.text[:300]
    with rasterio.MemoryFile(response.content) as mem, mem.open() as ds:
        return ds.read(), ds.profile, ds.bounds


def read_multipart_tiff(response):
    """WCS 1.1.x wraps the coverage in a MIME multipart message: part 1 = XML description, part 2 = the GeoTIFF
    (declared Content-Transfer-Encoding: base64)."""
    import base64
    from email import message_from_bytes

    assert response.status_code == 200 and response.headers["Content-Type"].startswith("multipart/"), response.text[:300]
    message = message_from_bytes(b"Content-Type: " + response.headers["Content-Type"].encode() + b"\r\n\r\n" + response.content)
    parts = {part.get_content_type(): part for part in message.walk() if not part.is_multipart()}
    assert set(parts) == {"text/xml", "image/tiff"}, list(parts)
    assert parts["image/tiff"]["Content-Transfer-Encoding"] == "base64"
    payload = base64.b64decode(parts["image/tiff"].get_payload())
    with rasterio.MemoryFile(payload) as mem, mem.open() as ds:
        return ds.read(), ds.profile


def exception(response):
    root = ET.fromstring(response.text)
    exc = root.find("ows:Exception", NS)
    return exc.get("exceptionCode"), exc.get("locator"), "".join(exc.itertext()).strip()


def grid(window, lons, lats):
    """Expected pixels [north to south, west to east] for a fabricated window."""
    return np.array([[chl_value(window, lat, lon) if lon >= 45 else NODATA for lon in lons] for lat in lats[::-1]])


# ---- GetCapabilities --------------------------------------------------------------------------
def test_wcs201_capabilities_identify_service_and_operations(service):
    r = ows(service="WCS", request="GetCapabilities", version="2.0.1")
    assert r.status_code == 200
    root = ET.fromstring(r.text)
    assert root.tag == f"{{{NS['wcs']}}}Capabilities" and root.get("version") == "2.0.1"
    assert root.findtext("ows:ServiceIdentification/ows:Title", namespaces=NS).startswith("Bhoonidhi EOS-06 Chlorophyll WCS")
    assert "NRSC/ISRO" in root.findtext("ows:ServiceIdentification/ows:Abstract", namespaces=NS)
    assert root.findtext("ows:ServiceIdentification/ows:ServiceType", namespaces=NS) == "urn:ogc:service:wcs"
    assert "2.0.1" in [e.text for e in root.findall("ows:ServiceIdentification/ows:ServiceTypeVersion", NS)]
    operations = {o.get("name") for o in root.findall("ows:OperationsMetadata/ows:Operation", NS)}
    assert {"GetCapabilities", "DescribeCoverage", "GetCoverage"} <= operations
    profiles = [p.text for p in root.findall("ows:ServiceIdentification/ows:Profile", NS)]
    assert "http://www.opengis.net/spec/WCS/2.0/conf/core" in profiles


def test_wcs201_capabilities_list_the_coverage_with_metadata(service):
    root = ET.fromstring(ows(service="WCS", request="GetCapabilities", version="2.0.1").text)
    summaries = root.findall("wcs:Contents/wcs:CoverageSummary", NS)
    assert [s.findtext("wcs:CoverageId", namespaces=NS) for s in summaries] == [COVERAGE]
    s = summaries[0]
    assert s.findtext("ows:Title", namespaces=NS).startswith("EOS-06 OCM-3 GAC chlorophyll-a")
    assert "chlorophyll-a" in [k.text for k in s.findall("ows:Keywords/ows:Keyword", NS)]
    assert s.findtext("wcs:CoverageSubtype", namespaces=NS) == "RectifiedGridCoverage"
    assert s.find("ows:BoundingBox", NS) is not None


def test_version_negotiation_returns_the_highest_supported_version(service):
    r = ows(service="WCS", request="GetCapabilities", version="9.9.9")
    assert r.status_code == 200 and ET.fromstring(r.text).get("version") == "2.0.1"


def test_wcs111_and_wcs100_capabilities(service):
    r = ows(service="WCS", request="GetCapabilities", version="1.1.1")
    assert r.status_code == 200 and "Capabilities" in ET.fromstring(r.text).tag and ET.fromstring(r.text).get("version") == "1.1.1"
    r = ows(service="WCS", request="GetCapabilities", version="1.0.0")
    assert r.status_code == 200 and ET.fromstring(r.text).get("version") == "1.0.0"
    assert "ocean:chl" in r.text


# ---- DescribeCoverage -------------------------------------------------------------------------
def test_wcs201_describe_coverage_grid_crs_and_axes(service):
    r = ows(service="WCS", request="DescribeCoverage", version="2.0.1", coverageId=COVERAGE)
    assert r.status_code == 200
    root = ET.fromstring(r.text)
    env = root.find(".//gml:boundedBy/gml:EnvelopeWithTimePeriod", NS)
    assert env.get("srsName").endswith("/EPSG/0/4326") and env.get("axisLabels") == "Lat Long time"
    lower = [float(v) for v in env.findtext("gml:lowerCorner", namespaces=NS).split()]
    upper = [float(v) for v in env.findtext("gml:upperCorner", namespaces=NS).split()]
    assert lower == pytest.approx([-10.5, 39.5]) and upper == pytest.approx([30.5, 100.5])      # outer cell edges
    assert root.findtext(".//gml:GridEnvelope/gml:high", namespaces=NS) == "60 40"               # 61 x 41 cells
    offsets = [[float(v) for v in o.text.split()] for o in root.findall(".//gml:offsetVector", NS)]
    assert offsets == [pytest.approx([0.0, 1.0]), pytest.approx([-1.0, 0.0])]
    assert root.findtext(".//wcs:nativeFormat", namespaces=NS) == "image/tiff"


def test_wcs201_describe_coverage_time_periods_are_the_windows_in_utc(service):
    root = ET.fromstring(ows(service="WCS", request="DescribeCoverage", version="2.0.1", coverageId=COVERAGE).text)
    periods = [(p.findtext("gml:beginPosition", namespaces=NS), p.findtext("gml:endPosition", namespaces=NS))
               for p in root.findall(".//gml:TimePeriod", NS)]
    assert periods == [("2025-07-08T00:00:00.000Z", "2025-07-15T23:59:59.000Z"),
                       ("2025-07-16T00:00:00.000Z", "2025-07-23T23:59:59.000Z"),
                       ("2025-07-24T00:00:00.000Z", "2025-07-31T23:59:59.000Z"),
                       ("2025-08-09T00:00:00.000Z", "2025-08-16T23:59:59.000Z")]
    env = root.find(".//gml:EnvelopeWithTimePeriod", NS)
    assert env.findtext("gml:beginPosition", namespaces=NS) == "2025-07-08T00:00:00.000Z"
    assert env.findtext("gml:endPosition", namespaces=NS) == "2025-08-16T23:59:59.000Z"


def test_wcs201_describe_coverage_range_type_names_the_real_quantity(service):
    root = ET.fromstring(ows(service="WCS", request="DescribeCoverage", version="2.0.1", coverageId=COVERAGE).text)
    fields = root.findall(".//swe:field", NS)
    assert [f.get("name") for f in fields] == ["chlorophyll_a"]
    assert fields[0].find(".//swe:uom", NS).get("code") == "mg m-3"
    assert fields[0].findtext(".//swe:nilValue", namespaces=NS) == "-9999.0"


# Subsets below use cell EDGES (x.5): the cube's nodes are cell centres, and GeoServer decides which partial cells
# a bound that falls inside a cell includes by rounding, which is implementation-defined.
# ---- GetCoverage: subsetting, time, values ----------------------------------------------------
def test_get_coverage_without_subsets_returns_the_whole_grid(service):
    data, profile, bounds = read_tiff(gc())
    assert data.shape[1:] == (41, 61)
    assert profile["crs"].to_epsg() == 4326 and profile["nodata"] == NODATA and profile["dtype"] == "float32"
    assert (bounds.left, bounds.bottom, bounds.right, bounds.top) == pytest.approx((39.5, -10.5, 100.5, 30.5))


def test_get_coverage_spatial_trim_returns_exactly_the_requested_cells(service):
    data, profile, bounds = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-07-10T00:00:00.000Z")'))
    assert data.shape == (1, 3, 3)
    np.testing.assert_allclose(data[0], grid(0, [50, 51, 52], [0, 1, 2]), atol=1e-6)
    assert bounds.left == pytest.approx(49.5) and bounds.right == pytest.approx(52.5)
    assert bounds.bottom == pytest.approx(-0.5) and bounds.top == pytest.approx(2.5)


@pytest.mark.parametrize("instant,window", [
    ("2025-07-08T00:00:00.000Z", 0), ("2025-07-12T13:45:00.000Z", 0), ("2025-07-15T23:59:59.000Z", 0),
    ("2025-07-16T00:00:00.000Z", 1), ("2025-07-23T23:59:59.000Z", 1), ("2025-07-24T00:00:00.000Z", 2),
    ("2025-08-16T23:59:59.000Z", 3),
])
def test_time_slice_returns_the_window_containing_the_instant(service, instant, window):
    data, _, _ = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', f'time("{instant}")'))
    assert data.shape == (1, 3, 3)
    np.testing.assert_allclose(data[0], grid(window, [50, 51, 52], [0, 1, 2]), atol=1e-6)


def test_time_slice_outside_the_declared_range_is_invalid_subsetting(service):
    r = gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2026-01-01T00:00:00.000Z")')
    code, locator, text = exception(r)
    assert r.status_code in (400, 404) and code == "InvalidSubsetting" and "does not intersect" in text


def test_time_slice_between_windows_is_an_error_not_silent_data(service):
    r = gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-08-02T12:00:00.000Z")')
    assert r.status_code >= 400 and r.content[:2] not in (b"II", b"MM")
    assert "ExceptionReport" in r.text


def test_time_trim_returns_one_band_per_window_in_time_order(service):
    data, _, _ = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-07-08T00:00:00.000Z","2025-07-31T23:59:59.000Z")'))
    assert data.shape == (3, 3, 3)
    for k in range(3):
        np.testing.assert_allclose(data[k], grid(k, [50, 51, 52], [0, 1, 2]), atol=1e-6)


def test_time_series_at_a_point_is_a_time_range_over_one_cell(service):
    data, _, _ = read_tiff(gc('Long(60)', 'Lat(10)', 'time("2025-07-08T00:00:00.000Z","2025-08-16T23:59:59.000Z")'))
    assert data.shape == (4, 1, 1)
    np.testing.assert_allclose(data[:, 0, 0], [chl_value(k, 10, 60) for k in range(4)], atol=1e-6)


def test_nodata_cells_carry_the_declared_nodata_value(service):
    data, profile, _ = read_tiff(gc('Long(39.5,47.5)', 'Lat(-0.5,0.5)', 'time("2025-07-10T00:00:00.000Z")'))
    assert (data[0, 0, :5] == NODATA).all() and (data[0, 0, 5:] != NODATA).all()        # lon < 45 has no data
    assert not np.isnan(data).any()


def test_without_a_time_subset_geoserver_returns_the_first_window(service):
    """Pinned so a GeoServer upgrade that changes it is noticed. Clients should always pass a time subset."""
    data, _, _ = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)'))
    assert data.shape == (1, 3, 3)
    np.testing.assert_allclose(data[0], grid(0, [50, 51, 52], [0, 1, 2]), atol=1e-6)


# ---- GetCoverage: other WCS 2.0 request parameters --------------------------------------------
def test_scalefactor_and_scalesize(service):
    data, _, _ = read_tiff(gc('Long(39.5,60.5)', 'Lat(-0.5,20.5)', 'time("2025-07-10T00:00:00.000Z")', scalefactor="0.5"))
    assert data.shape[1:] == (10, 10) or data.shape[1:] == (11, 11)
    data, _, _ = read_tiff(gc('Long(39.5,60.5)', 'Lat(-0.5,20.5)', 'time("2025-07-10T00:00:00.000Z")', scalesize="i(7),j(5)"))
    assert data.shape[1:] == (5, 7)


def test_rangesubset_by_band_name(service):
    data, _, _ = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-07-10T00:00:00.000Z")', rangesubset="chlorophyll_a"))
    assert data.shape == (1, 3, 3)
    code, locator, _ = exception(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-07-10T00:00:00.000Z")', rangesubset="nope"))
    assert code == "NoSuchField" and locator == "nope"


def test_outputcrs_reprojects(service):
    _, profile, _ = read_tiff(gc('Long(49.5,52.5)', 'Lat(-0.5,2.5)', 'time("2025-07-10T00:00:00.000Z")',
                                 outputcrs="http://www.opengis.net/def/crs/EPSG/0/3857"))
    assert profile["crs"].to_epsg() == 3857


def test_gml_coverage_encoding(service):
    r = requests.get(OWS, params=[("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"),
                                  ("coverageId", COVERAGE), ("format", "application/gml+xml"),
                                  ("subset", "Long(49.5,52.5)"), ("subset", "Lat(-0.5,2.5)"),
                                  ("subset", 'time("2025-07-10T00:00:00.000Z")')], timeout=120)
    root = ET.fromstring(r.text)
    assert root.tag == f"{{{NS['gml']}}}RectifiedGridCoverage"
    assert len((root.findtext(".//tupleList") or root.findtext(".//gml:tupleList", namespaces=NS)).split()) == 9


# ---- exception reports ------------------------------------------------------------------------
def test_exception_no_such_coverage(service):
    r = ows(service="WCS", version="2.0.1", request="GetCoverage", coverageId="ocean__nope")
    assert r.status_code == 404 and exception(r)[:2] == ("NoSuchCoverage", "coverageId")


def test_exception_missing_coverage_id(service):
    r = ows(service="WCS", version="2.0.1", request="GetCoverage")
    assert r.status_code in (400, 404) and exception(r)[1] == "coverageId"


def test_exception_invalid_axis_label(service):
    r = gc('Foo(1,2)')
    assert exception(r)[:2] == ("InvalidAxisLabel", "Foo")


def test_exception_subset_outside_the_coverage_is_invalid_subsetting(service):
    r = gc('Long(0,10)', 'Lat(0,10)')
    assert exception(r)[0] == "InvalidSubsetting" and "Empty intersection" in exception(r)[2]


def test_exception_unparseable_time(service):
    r = gc('time("garbage")')
    assert r.status_code == 400 and exception(r)[0] == "InvalidEncodingSyntax"


def test_exception_unsupported_format(service):
    r = requests.get(OWS, params=dict(service="WCS", version="2.0.1", request="GetCoverage", coverageId=COVERAGE,
                                      format="image/nope"), timeout=60)
    assert r.status_code == 400 and exception(r)[:2] == ("InvalidParameterValue", "format")


def test_exception_unknown_operation(service):
    r = ows(service="WCS", version="2.0.1", request="Frobnicate")
    assert r.status_code == 501 and exception(r)[0] == "OperationNotSupported"


# ---- WCS 1.1.1 and 1.0.0 ----------------------------------------------------------------------
def test_wcs111_describe_and_get_coverage(service):
    r = ows(service="WCS", version="1.1.1", request="DescribeCoverage", identifiers="ocean:chl")
    assert r.status_code == 200
    assert ET.fromstring(r.text).findtext(".//{http://www.opengis.net/wcs/1.1.1}Identifier") == "ocean:chl"
    # EPSG:4326 in its short form is lon/lat; gridoffsets (x step, y step) define the output grid
    r = requests.get(OWS, params=dict(
        service="WCS", version="1.1.1", request="GetCoverage", identifier="ocean:chl", format="image/tiff",
        boundingbox="49.5,-0.5,52.5,2.5,EPSG:4326", gridbasecrs="EPSG:4326", gridoffsets="1,-1",
        timesequence="2025-07-18T00:00:00.000Z"), timeout=120)
    data, profile = read_multipart_tiff(r)
    assert profile["crs"].to_epsg() == 4326
    np.testing.assert_allclose(data[0], grid(1, [50, 51, 52], [0, 1, 2]), atol=1e-6)


@pytest.mark.xfail(strict=False, reason="GeoServer WCS 1.1.1 returns a TRANSPOSED raster (georeference unchanged) when "
                   "the lat-first URN form of EPSG:4326 is used; found with a non-square window. The README usage notes give the EPSG:4326 (lon/lat) form.")
def test_wcs111_urn_latlon_axis_order_known_geoserver_defect(service):
    r = requests.get(OWS, params=dict(
        service="WCS", version="1.1.1", request="GetCoverage", identifier="ocean:chl", format="image/tiff",
        boundingbox="-0.5,39.5,1.5,44.5,urn:ogc:def:crs:EPSG::4326", gridbasecrs="urn:ogc:def:crs:EPSG::4326",
        gridoffsets="-1,1", timesequence="2025-07-18T00:00:00.000Z"), timeout=120)
    data, _ = read_multipart_tiff(r)
    assert data.shape[1:] == (2, 5), "2 rows (lat) x 5 columns (lon) expected; a transposed raster is 5 x 2"


def test_wcs100_describe_and_get_coverage(service):
    r = ows(service="WCS", version="1.0.0", request="DescribeCoverage", coverage="ocean:chl")
    assert r.status_code == 200 and "CoverageDescription" in r.text
    r = requests.get(OWS, params=dict(service="WCS", version="1.0.0", request="GetCoverage", coverage="ocean:chl",
                                      bbox="49.5,-0.5,52.5,2.5", crs="EPSG:4326", width="3", height="3",
                                      format="GeoTIFF", time="2025-07-18T00:00:00.000Z"), timeout=120)
    data, _, _ = read_tiff(r)
    np.testing.assert_allclose(data[0], grid(1, [50, 51, 52], [0, 1, 2]), atol=1e-6)


# ---- the deployment itself --------------------------------------------------------------------
def test_server_listens_on_loopback_only(service):
    import socket

    external = [a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                if not a[4][0].startswith("127.")]
    for ip in external:
        with socket.socket() as sock:
            sock.settimeout(1)
            assert sock.connect_ex((ip, PORT)) != 0, f"GeoServer is reachable on {ip}:{PORT}"


# ---- operating limits and public URL ----------------------------------------------------------
def test_absurdly_large_requests_are_refused_not_attempted(service):
    r = gc('time("2025-07-10T00:00:00.000Z")', scalefactor="5000")
    assert r.status_code >= 400 and r.content[:2] not in (b"II", b"MM")
    assert "ExceptionReport" in r.text


def test_proxy_base_url_is_written_into_capabilities_links(service):
    def hrefs():
        root = ET.fromstring(ows(service="WCS", request="GetCapabilities", version="2.0.1").text)
        return {e.get("{http://www.w3.org/1999/xlink}href") for e in root.iter() if e.get("{http://www.w3.org/1999/xlink}href")}

    assert all(h.startswith(f"http://127.0.0.1:{PORT}/geoserver/") for h in hrefs())
    geoserver.set_proxy_base_url(service, "https://example.invalid/geoserver", PORT)
    try:
        assert all(h.startswith("https://example.invalid/geoserver/") for h in hrefs())
    finally:
        geoserver.set_proxy_base_url(service, None, PORT)
    assert all(h.startswith(f"http://127.0.0.1:{PORT}/geoserver/") for h in hrefs())


def test_capabilities_advertise_a_short_crs_list_and_stay_small(service):
    r = ows(service="WCS", request="GetCapabilities", version="2.0.1")
    advertised = re.findall(r"crsSupported>([^<]+)<", r.text)
    assert advertised == [f"http://www.opengis.net/def/crs/EPSG/0/{c}" for c in geoserver.ADVERTISED_CRS]
    assert len(r.content) < 50_000, f"{len(r.content)} bytes"


def test_zz_default_admin_password_is_rejected_by_a_fresh_server_process(service):
    """GeoServer caches logins, so prove it with a restart: no cache, the factory password must fail.

    Named test_zz_ so it runs last: it replaces the server process the other tests use.
    """
    data_dir = service
    assert geoserver.stop(data_dir, PORT)
    proc = geoserver.start(data_dir, PORT)
    try:
        geoserver.wait_ready(PORT, proc=proc)
        version = f"{BASE}/rest/about/version.json"
        assert requests.get(version, timeout=30).status_code == 401
        assert requests.get(version, auth=geoserver.DEFAULT_ADMIN, timeout=30).status_code == 401
        stored = (data_dir.parent / "geoserver-admin-password.txt").read_text().strip()
        assert requests.get(version, auth=("admin", stored), timeout=30).status_code == 200
        assert ows(service="WCS", request="GetCapabilities", version="2.0.1").status_code == 200   # WCS stays public
    finally:
        geoserver.stop(data_dir, PORT, proc)
