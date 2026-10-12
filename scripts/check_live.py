"""Run requirement-mapped WCS checks against a running service (local or public) and compare pixels with the cube.

    python scripts/check_live.py http://127.0.0.1:8080/geoserver
    python scripts/check_live.py https://my-tunnel.example/geoserver --public

--public additionally asserts that the admin UI and REST API are NOT reachable through that URL.
Exit status 1 if any check fails, so it can gate a demo.
"""
import argparse
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chlwcs  # noqa: E402,F401  (fixes the PROJ environment before rasterio is imported)
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
import requests  # noqa: E402
import xarray as xr  # noqa: E402

COVERAGE = "ocean__chl"
NODATA = -9999.0
NS = {"wcs": "http://www.opengis.net/wcs/2.0", "ows": "http://www.opengis.net/ows/2.0",
      "gml": "http://www.opengis.net/gml/3.2", "swe": "http://www.opengis.net/swe/2.0"}
TIMEOUT = 120
RESULTS = []
SITES = {"off Kerala": (75.5, 10.0), "Arabian Sea": (65.0, 15.0), "Bay of Bengal": (87.0, 14.0), "off Oman": (58.5, 18.0)}


def check(name):
    """Register and immediately run a check; one failure never stops the others."""

    def deco(fn):
        t0 = time.perf_counter()
        try:
            fn()
            RESULTS.append((name, True, "", time.perf_counter() - t0))
        except Exception as err:  # noqa: BLE001 - report every failure instead of aborting
            RESULTS.append((name, False, f"{type(err).__name__}: {err}"[:300], time.perf_counter() - t0))
        return fn

    return deco


def need(cond, msg=""):
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base_url", help="GeoServer base URL, ending in /geoserver")
    ap.add_argument("--cube", type=Path, default=ROOT / "data" / "processed" / "ocm_gac_chl_8day.nc")
    ap.add_argument("--public", action="store_true", help="also check that admin/REST are not exposed")
    ap.add_argument("--max-seconds", type=float, default=5.0, help="latency budget for a small GetCoverage")
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    ows_url = f"{base}/ows"
    session = requests.Session()

    with xr.open_dataset(args.cube) as ds:
        cube = ds.load()
    lon, lat = cube.lon.values, cube.lat.values
    step = float(lon[1] - lon[0])
    starts = cube.time.values.astype("datetime64[s]")
    ends = cube.time_end.values.astype("datetime64[s]") - np.timedelta64(1, "s")      # inclusive, as served

    def get(**params):
        t0 = time.perf_counter()
        r = session.get(ows_url, params=params, timeout=TIMEOUT)
        r.elapsed_s = time.perf_counter() - t0
        return r

    def gc(*subsets, **extra):
        params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageId", COVERAGE),
                  ("format", "image/tiff")] + [("subset", s) for s in subsets] + list(extra.items())
        t0 = time.perf_counter()
        r = session.get(ows_url, params=params, timeout=TIMEOUT)
        r.elapsed_s = time.perf_counter() - t0
        return r

    def tiff(r):
        need(r.status_code == 200 and r.content[:2] in (b"II", b"MM"), f"{r.status_code} {r.text[:200]!r}")
        with rasterio.MemoryFile(r.content) as mem, mem.open() as d:
            return d.read(), d.profile, d.bounds

    def block(site, half=2):
        """Cube cells around a site: (west, south, east, north) edges and expected arrays per window, north-up."""
        i = int(np.abs(lon - site[0]).argmin())
        j = int(np.abs(lat - site[1]).argmin())
        sl = (slice(j - half, j + half + 1), slice(i - half, i + half + 1))
        edges = (lon[i - half] - step / 2, lat[j - half] - step / 2, lon[i + half] + step / 2, lat[j + half] + step / 2)
        truth = np.where(np.isnan(cube.chl.values[:, sl[0], sl[1]]), NODATA, cube.chl.values[:, sl[0], sl[1]])
        return edges, truth[:, ::-1, :]

    def subsets(edges):
        w, s, e, n = edges
        return f"Long({w:.9f},{e:.9f})", f"Lat({s:.9f},{n:.9f})"

    def iso(t):
        return np.datetime_as_string(t, unit="s") + ".000Z"

    def windows_containing(instant):
        return [k for k in range(len(starts)) if starts[k] <= instant <= ends[k]]

    def matching(band, truth):
        """Windows whose cube values equal a returned band: says what a mismatching band actually holds."""
        return [w for w in range(truth.shape[0]) if np.allclose(band, truth[w], atol=1e-4)]

    # ---- discovery ----------------------------------------------------------------------------
    @check("WCS 2.0.1 GetCapabilities: service identification, operations, coverage listed")
    def _():
        r = get(service="WCS", request="GetCapabilities", version="2.0.1")
        need(r.status_code == 200, r.status_code)
        root = ET.fromstring(r.text)
        need(root.get("version") == "2.0.1", root.get("version"))
        title = root.findtext("ows:ServiceIdentification/ows:Title", namespaces=NS)
        need(title and "Chlorophyll" in title, title)
        ops = {o.get("name") for o in root.findall("ows:OperationsMetadata/ows:Operation", NS)}
        need({"GetCapabilities", "DescribeCoverage", "GetCoverage"} <= ops, ops)
        need([s.findtext("wcs:CoverageId", namespaces=NS) for s in root.findall(".//wcs:CoverageSummary", NS)] == [COVERAGE], "coverage id")

    @check("operation URLs in the capabilities point at the URL being tested")
    def _():
        root = ET.fromstring(get(service="WCS", request="GetCapabilities", version="2.0.1").text)
        hrefs = [e.get("{http://www.w3.org/1999/xlink}href") for e in root.iter() if e.get("{http://www.w3.org/1999/xlink}href")]
        need(hrefs, "no DCP links found")
        bad = [h for h in hrefs if not h.startswith(base.split("/geoserver")[0])]
        need(not bad, f"links not under {base}: {bad[:2]} (GeoServer builds them from the request Host header)")

    @check("version negotiation + WCS 1.1.1 and 1.0.0 capabilities")
    def _():
        need(ET.fromstring(get(service="WCS", request="GetCapabilities", version="9.9.9").text).get("version") == "2.0.1", "negotiation")
        for v in ("1.1.1", "1.0.0"):
            r = get(service="WCS", request="GetCapabilities", version=v)
            need(r.status_code == 200 and ET.fromstring(r.text).get("version") == v, v)

    @check("DescribeCoverage: CRS, time period = first window start .. last window end (UTC), nodata, band + unit")
    def _():
        root = ET.fromstring(get(service="WCS", request="DescribeCoverage", version="2.0.1", coverageId=COVERAGE).text)
        env = root.find(".//gml:EnvelopeWithTimePeriod", NS)
        need(env.get("srsName").endswith("/EPSG/0/4326") and env.get("axisLabels") == "Lat Long time", env.attrib)
        need(env.findtext("gml:beginPosition", namespaces=NS) == iso(starts[0]), env.findtext("gml:beginPosition", namespaces=NS))
        need(env.findtext("gml:endPosition", namespaces=NS) == iso(ends[-1]), env.findtext("gml:endPosition", namespaces=NS))
        need([f.get("name") for f in root.findall(".//swe:field", NS)] == ["chlorophyll_a"], "band name")
        need(root.find(".//swe:uom", NS).get("code") == "mg m-3", "unit")
        need(root.findtext(".//swe:nilValue", namespaces=NS) == "-9999.0", "nodata")
        need(root.findtext(".//gml:GridEnvelope/gml:high", namespaces=NS) == f"{len(lon) - 1} {len(lat) - 1}", "grid size")

    # ---- values: every window against the cube ----------------------------------------------------
    @check("every window, 4 sites: GetCoverage pixels equal the cube (incl. no-data) at mid-window instants")
    def _():
        checked = 0
        for name, site in SITES.items():
            edges, truth = block(site)
            for k in range(len(starts)):
                instant = starts[k] + np.timedelta64(3, "D") + np.timedelta64(12, "h")
                bands = windows_containing(instant)
                data, profile, bounds = tiff(gc(*subsets(edges), f'time("{iso(instant)}")'))
                need(data.shape[0] == len(bands), f"{name} window {k}: {data.shape[0]} bands, expected {len(bands)}")
                for b, w in enumerate(bands):
                    need(np.allclose(data[b], truth[w], atol=1e-4),
                         f"{name} window {w} differs from the cube (at {iso(instant)} band {b} of {bands} "
                         f"matches windows {matching(data[b], truth)})")
                need(profile["nodata"] == NODATA and profile["crs"].to_epsg() == 4326, "profile")
                checked += 1
        need(checked == len(SITES) * len(starts), checked)

    @check("window boundaries: first second, last second, and the first second of the next window")
    def _():
        edges, truth = block(SITES["off Kerala"])
        for k in range(len(starts) - 1):
            for instant, expect in ((starts[k], k), (ends[k], k)):
                bands = windows_containing(instant)
                data, _, _ = tiff(gc(*subsets(edges), f'time("{iso(instant)}")'))
                need(data.shape[0] == len(bands) and all(np.allclose(data[b], truth[w], atol=1e-4) for b, w in enumerate(bands)),
                     f"boundary {iso(instant)}: expected windows {bands}, bands match "
                     f"{[matching(band, truth) for band in data]}")

    @check("time series at a point (one cell, whole season): one band per window, ascending, equal to the cube")
    def _():
        for name, (x, y) in SITES.items():
            i, j = int(np.abs(lon - x).argmin()), int(np.abs(lat - y).argmin())
            data, _, _ = tiff(gc(f"Long({lon[i]:.9f})", f"Lat({lat[j]:.9f})", f'time("{iso(starts[0])}","{iso(ends[-1])}")'))
            expect = np.where(np.isnan(cube.chl.values[:, j, i]), NODATA, cube.chl.values[:, j, i])
            need(data.shape == (len(starts), 1, 1), f"{name}: {data.shape}")
            bad = [k for k in range(len(starts)) if not np.isclose(data[k, 0, 0], expect[k], atol=1e-4)]
            need(not bad, f"{name}: series differs from the cube at windows {bad}: "
                          f"{[(float(data[k, 0, 0]), float(expect[k])) for k in bad]}")

    @check("a date in the one-day hole between windows is an error, never silent data")
    def _():
        hole = [(ends[k] + np.timedelta64(1, "s")) for k in range(len(starts) - 1)
                if starts[k + 1] > ends[k] + np.timedelta64(1, "s")]
        if not hole:
            return
        r = gc(*subsets(block(SITES["off Kerala"])[0]), f'time("{iso(hole[0] + np.timedelta64(12, "h"))}")')
        need(r.status_code >= 400 and r.content[:2] not in (b"II", b"MM"), r.status_code)

    @check("whole grid GetCoverage: size, CRS, nodata, speed")
    def _():
        data, profile, bounds = tiff(gc(f'time("{iso(starts[0] + np.timedelta64(1, "D"))}")'))
        need(data.shape[1:] == (len(lat), len(lon)), data.shape)
        need(profile["crs"].to_epsg() == 4326 and profile["nodata"] == NODATA, profile)

    @check("small GetCoverage is fast")
    def _():
        r = gc(*subsets(block(SITES["off Kerala"])[0]), f'time("{iso(starts[3])}")')
        tiff(r)
        need(r.elapsed_s < args.max_seconds, f"{r.elapsed_s:.2f}s")

    # ---- other request parameters / versions ----------------------------------------------------
    @check("scalefactor, scalesize (i/j), rangesubset, outputcrs")
    def _():
        edges, _ = block(SITES["off Kerala"], half=10)
        t = f'time("{iso(starts[3])}")'
        data, _, _ = tiff(gc(*subsets(edges), t))
        n = data.shape[1]
        need(tiff(gc(*subsets(edges), t, scalefactor="0.5"))[0].shape[1] in (n // 2, n // 2 + 1), "scalefactor")
        need(tiff(gc(*subsets(edges), t, scalesize="i(7),j(5)"))[0].shape[1:] == (5, 7), "scalesize")
        need(tiff(gc(*subsets(edges), t, rangesubset="chlorophyll_a"))[0].shape[0] == 1, "rangesubset")
        need(tiff(gc(*subsets(edges), t, outputcrs="http://www.opengis.net/def/crs/EPSG/0/3857"))[1]["crs"].to_epsg() == 3857, "outputcrs")

    @check("WCS 1.0.0 GetCoverage returns the same pixels")
    def _():
        edges, truth = block(SITES["off Kerala"])
        instant = starts[3] + np.timedelta64(1, "D")
        w, s, e, n = edges
        r = get(service="WCS", version="1.0.0", request="GetCoverage", coverage="ocean:chl", crs="EPSG:4326", format="GeoTIFF",
                bbox=f"{w},{s},{e},{n}", width="5", height="5", time=iso(instant))
        data, _, _ = tiff(r)
        bands = windows_containing(instant)
        need(data.shape[0] == len(bands) and np.allclose(data[0], truth[bands[0]], atol=1e-4), "pixels differ")

    @check("exceptions: NoSuchCoverage, InvalidAxisLabel, InvalidSubsetting, bad time, bad format, bad operation")
    def _():
        def code(r):
            return ET.fromstring(r.text).find("ows:Exception", NS).get("exceptionCode")

        need(code(get(service="WCS", version="2.0.1", request="GetCoverage", coverageId="ocean__nope")) == "NoSuchCoverage", "NoSuchCoverage")
        need(code(gc("Foo(1,2)")) == "InvalidAxisLabel", "InvalidAxisLabel")
        need(code(gc("Long(0,10)", "Lat(0,10)")) == "InvalidSubsetting", "InvalidSubsetting")
        need(code(gc('time("2030-01-01T00:00:00.000Z")')) == "InvalidSubsetting", "time outside range")
        r = gc('time("garbage")')
        need(r.status_code == 400 and code(r) == "InvalidEncodingSyntax", "bad time")
        need(get(service="WCS", version="2.0.1", request="GetCoverage", coverageId=COVERAGE, format="image/nope").status_code == 400, "format")
        need(get(service="WCS", version="2.0.1", request="Frobnicate").status_code == 501, "operation")

    if args.public:
        origin = base.split("/geoserver")[0]

        @check("PUBLIC: admin UI, REST API, other OGC services and static docs are not reachable")
        def _():
            blocked = ["/geoserver/web/", "/geoserver/rest/about/version.json", "/geoserver/rest/workspaces",
                       "/geoserver/wfs?service=WFS&request=GetCapabilities", "/geoserver/wps?service=WPS&request=GetCapabilities",
                       "/geoserver/csw?service=CSW&request=GetCapabilities", "/geoserver/j_spring_security_check"]
            leaks = [p for p in blocked if session.get(origin + p, timeout=30, allow_redirects=False).status_code < 400]
            need(not leaks, f"reachable from outside: {leaks}")

        @check("PUBLIC: default admin credentials do not work")
        def _():
            r = session.get(f"{origin}/geoserver/rest/about/version.json", auth=("admin", "geoserver"), timeout=30)
            need(r.status_code in (401, 403, 404), r.status_code)

    width = max(len(n) for n, *_ in RESULTS)
    for name, ok, msg, dt in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {dt * 1000:7.0f} ms  {msg}")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed against {base}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
