"""The public gateway must pass WCS and nothing else (GeoServer's admin UI, REST and other services stay private)."""
import threading

import pytest
import requests
import waitress

from chlwcs import gateway
from tests.helpers import GS_PORT

pytestmark = pytest.mark.geoserver

GW_PORT = 18090
GW = f"http://127.0.0.1:{GW_PORT}"
DIRECT = f"http://127.0.0.1:{GS_PORT}"
CAPS = "/geoserver/ows?service=WCS&request=GetCapabilities&version=2.0.1"
GETCOV = ("/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=ocean__chl&format=image/tiff"
          "&subset=Long(49.5,52.5)&subset=Lat(-0.5,2.5)&subset=time(%222025-07-10T00:00:00.000Z%22)")


@pytest.fixture(scope="module")
def gw(service):
    mosaic = service.parents[1] / "mosaic"            # conftest layout: <root>/gs/data_dir and <root>/mosaic
    server = waitress.create_server(gateway.make_app(f"http://127.0.0.1:{GS_PORT}", mosaic), listen=f"127.0.0.1:{GW_PORT}")
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    yield GW
    server.close()
    thread.join(timeout=10)


def test_wcs_requests_pass_through_unchanged(gw):
    via, direct = requests.get(gw + GETCOV, timeout=60), requests.get(DIRECT + GETCOV, timeout=60)
    assert via.status_code == 200 and via.content[:2] in (b"II", b"MM")
    assert via.content == direct.content
    assert via.headers["Content-Type"].startswith("image/tiff")


# parameter KEYS are case-insensitive (the last path); the value of service must be exactly WCS
@pytest.mark.parametrize("path", [CAPS, "/geoserver/wcs?service=WCS&request=GetCapabilities&version=2.0.1",
                                  "/geoserver/ocean/wcs?service=WCS&request=GetCapabilities&version=2.0.1",
                                  "/geoserver/ows?SERVICE=WCS&REQUEST=GetCapabilities&VERSION=2.0.1"])
def test_capabilities_via_all_allowed_wcs_endpoints(gw, path):
    r = requests.get(gw + path, timeout=60)
    assert r.status_code == 200 and "Capabilities" in r.text


def test_wcs_exceptions_are_forwarded_with_their_status(gw):
    r = requests.get(gw + "/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=ocean__nope", timeout=60)
    assert r.status_code == 404 and "NoSuchCoverage" in r.text


@pytest.mark.parametrize("path", [
    "/geoserver/web/", "/geoserver/web", "/geoserver/rest/about/version.json", "/geoserver/rest/workspaces",
    "/geoserver/rest/security/self/password", "/geoserver/j_spring_security_check", "/geoserver/index.html",
    "/geoserver/ows/../rest/workspaces", "/geoserver/ows%2f..%2frest", "/geoserver/ows/", "/geoserver/gwc/rest",
    "/geoserver/ocean/wms?service=WMS&request=GetCapabilities", "/geoserver/schemas/../rest/workspaces",
    "/geoserver/wfs?service=WFS&request=GetCapabilities", "/geoserver/wps?service=WPS&request=GetCapabilities",
    "/geoserver/csw?service=CSW&request=GetCapabilities", "/geoserver/wmts", "/etc/passwd", "/robots.txt",
])
def test_everything_but_wcs_is_refused(gw, path):
    r = requests.get(gw + path, timeout=30, allow_redirects=False)
    assert r.status_code in (403, 404), (path, r.status_code)
    assert "<html" not in r.text.lower() and "geoserver" not in r.text.lower().replace("/geoserver", ""), "no GeoServer page may leak"


@pytest.mark.parametrize("svc", ["WFS", "WMS", "WPS", "CSW", "", "wcs,wfs", "WCS%00"])
def test_other_ogc_services_on_the_shared_ows_endpoint_are_refused(gw, svc):
    r = requests.get(f"{gw}/geoserver/ows?service={svc}&request=GetCapabilities", timeout=30)
    assert r.status_code == 403, (svc, r.status_code)


def test_ows_without_a_service_parameter_is_refused(gw):
    assert requests.get(f"{gw}/geoserver/ows?request=GetCapabilities", timeout=30).status_code == 403


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS", "TRACE"])
def test_only_get_head_post_are_allowed(gw, method):
    r = requests.request(method, gw + "/geoserver/ows?service=WCS&request=GetCapabilities", timeout=30)
    assert r.status_code == 405


def test_rest_is_unreachable_even_with_the_right_credentials(gw, service):
    stored = (service.parent / "geoserver-admin-password.txt").read_text().strip()
    r = requests.get(gw + "/geoserver/rest/about/version.json", auth=("admin", stored), timeout=30)
    assert r.status_code in (403, 404)
    assert requests.get(DIRECT + "/geoserver/rest/about/version.json", auth=("admin", stored), timeout=30).status_code == 200


POST_CAPS = b'<?xml version="1.0"?><wcs:GetCapabilities service="WCS" xmlns:wcs="http://www.opengis.net/wcs/2.0"/>'


def test_wcs_post_xml_is_allowed_and_non_wcs_post_is_not(gw):
    r = requests.post(gw + "/geoserver/ows", data=POST_CAPS, headers={"Content-Type": "application/xml"}, timeout=60)
    assert r.status_code == 200 and "Capabilities" in r.text
    wfs = b'<wfs:GetCapabilities service="WFS" xmlns:wfs="http://www.opengis.net/wfs"/>'
    assert requests.post(gw + "/geoserver/ows", data=wfs, headers={"Content-Type": "application/xml"}, timeout=30).status_code == 403
    assert requests.post(gw + "/geoserver/ows", data=b"<junk/>", timeout=30).status_code == 403
    assert requests.post(gw + "/geoserver/rest/workspaces", data=POST_CAPS, timeout=30).status_code == 403


def test_oversized_post_body_is_rejected(gw):
    r = requests.post(gw + "/geoserver/ows", data=POST_CAPS + b" " * (gateway.MAX_BODY + 1), timeout=30)
    assert r.status_code == 413


def test_root_serves_the_web_ui_which_uses_only_relative_wcs_urls(gw):
    r = requests.get(gw + "/", timeout=30)
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("text/html")
    assert "<title>Bhoonidhi Chlorophyll WCS</title>" in r.text and "unofficial" in r.text.lower()
    assert 'const OWS = "geoserver/ows"' in r.text and 'fetch("windows.json")' in r.text
    # no hard-coded host: the same page works on localhost and behind any public URL
    assert "http://127.0.0.1" not in r.text and "http://localhost" not in r.text
    assert requests.get(gw + "/index.html", timeout=30).text == r.text


def test_windows_json_lists_the_served_composite_windows(gw):
    r = requests.get(gw + "/windows.json", timeout=30)
    assert r.status_code == 200 and r.headers["Content-Type"] == "application/json"
    assert r.json() == [
        {"start": "2025-07-08T00:00:00Z", "end": "2025-07-15T23:59:59Z"},
        {"start": "2025-07-16T00:00:00Z", "end": "2025-07-23T23:59:59Z"},
        {"start": "2025-07-24T00:00:00Z", "end": "2025-07-31T23:59:59Z"},
        {"start": "2025-08-09T00:00:00Z", "end": "2025-08-16T23:59:59Z"},
    ]


def test_windows_json_is_empty_when_there_is_no_mosaic(tmp_path):
    assert gateway.composite_windows(tmp_path / "missing") == [] and gateway.composite_windows(None) == []


def test_ui_requests_work_through_the_gateway(gw):
    """The exact request shapes the UI builds (URLSearchParams encoding, DEFLATE GeoTIFF, scalesize, point series)."""
    base = (f"{gw}/geoserver/ows?service=WCS&version=2.0.1&request=GetCoverage&coverageId=ocean__chl"
            "&format=image%2Ftiff&geotiff%3Acompression=DEFLATE")
    view = requests.get(base + "&subset=Long%2845.000000%2C60.000000%29&subset=Lat%280.000000%2C10.000000%29"
                        "&subset=time%28%222025-07-16T00%3A00%3A00.000Z%22%29&scalesize=i%2815%29%2Cj%2810%29", timeout=60)
    assert view.status_code == 200 and view.headers["Content-Type"].startswith("image/tiff")
    series = requests.get(base + "&subset=Long%2850.300000%29&subset=Lat%280.200000%29"
                          "&subset=time%28%222025-07-08T00%3A00%3A00.000Z%22%2C%222025-08-16T23%3A59%3A59.000Z%22%29", timeout=60)
    assert series.status_code == 200 and series.headers["Content-Type"].startswith("image/tiff")


def test_ui_javascript_is_syntactically_valid():
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    page = gateway.UI_FILE.read_text(encoding="utf8")
    script = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
    result = subprocess.run([node, "--check", "-"], input=script, capture_output=True, text=True, encoding="utf-8",
                            timeout=60)
    assert result.returncode == 0, result.stderr


def test_unreachable_upstream_gives_502_not_a_traceback(service):
    app = gateway.make_app("http://127.0.0.1:1")
    server = waitress.create_server(app, listen="127.0.0.1:18091")
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        r = requests.get("http://127.0.0.1:18091" + CAPS, timeout=30)
        assert r.status_code == 502 and "Traceback" not in r.text
    finally:
        server.close()
        t.join(timeout=10)
