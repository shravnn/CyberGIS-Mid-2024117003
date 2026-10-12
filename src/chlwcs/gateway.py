"""Allow-list reverse proxy in front of GeoServer, plus the web UI.

GeoServer ships an admin UI and REST API behind a well-known default login, plus WFS/WMS/WPS/CSW services this project
does not offer. A public URL must reach none of that, so only WCS requests are forwarded and everything else is refused.
The gateway also serves the browser UI (`/`) and the list of composite windows the UI needs (`/windows.json`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs

import requests

UPSTREAM_TIMEOUT = 120
MAX_BODY = 64 * 1024
# exact paths only (no prefixes): nothing is reachable by path tricks such as /geoserver/ows/../rest
OWS_PATHS = {"/geoserver/ows", "/geoserver/wcs", "/geoserver/ocean/wcs", "/geoserver/ocean/ows"}
STATIC_PREFIX = "/geoserver/schemas/"          # XML schemas that WCS responses reference via xsi:schemaLocation
FORWARD_REQUEST_HEADERS = ("Accept", "Accept-Language", "Content-Type", "User-Agent")
FORWARD_RESPONSE_HEADERS = ("Content-Type", "Content-Disposition", "Cache-Control", "Last-Modified")
_WCS_BODY = re.compile(rb"<\s*(?:[\w-]+:)?(GetCapabilities|DescribeCoverage|GetCoverage)\b")
_WCS_SERVICE = re.compile(rb"""\bservice\s*=\s*["']WCS["']""", re.IGNORECASE)
_GRANULE = re.compile(r"^chl_(\d{8}T\d{6})_(\d{8}T\d{6})\.tif$")
UI_FILE = Path(__file__).with_name("web") / "index.html"


def _iso(stamp: str) -> str:
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[9:11]}:{stamp[11:13]}:{stamp[13:15]}Z"


def composite_windows(mosaic_dir) -> list[dict]:
    """The served composite windows (start, inclusive end), read from the mosaic's granule names.

    WCS DescribeCoverage merges overlapping windows into one time period, so the UI cannot recover the
    individual windows (needed to label the bands of a time-range response) from the WCS itself.
    """
    if mosaic_dir is None or not Path(mosaic_dir).is_dir():
        return []
    found = [_GRANULE.match(p.name) for p in Path(mosaic_dir).glob("chl_*.tif")]
    return sorted(({"start": _iso(m[1]), "end": _iso(m[2])} for m in found if m), key=lambda w: w["start"])


def _respond(start_response, status: str, body: bytes, content_type: str, method: str = "GET"):
    start_response(status, [("Content-Type", content_type), ("Content-Length", str(len(body)))])
    return [body] if method != "HEAD" else []


def _refuse(start_response, status: str, message: str):
    return _respond(start_response, status, message.encode(), "text/plain; charset=utf-8")


def make_app(upstream: str, mosaic_dir=None):
    """WSGI app: the UI, the window list, and WCS requests forwarded to `upstream` (e.g. http://127.0.0.1:8080)."""
    upstream = upstream.rstrip("/")
    session = requests.Session()

    def app(environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "")
        if method not in ("GET", "HEAD", "POST"):
            return _refuse(start_response, "405 Method Not Allowed", "only GET, HEAD and POST are supported")
        if path in ("", "/", "/index.html"):
            return _respond(start_response, "200 OK", UI_FILE.read_bytes(), "text/html; charset=utf-8", method)
        if path == "/windows.json":
            body = json.dumps(composite_windows(mosaic_dir)).encode()
            return _respond(start_response, "200 OK", body, "application/json", method)

        query = environ.get("QUERY_STRING", "")
        body = b""
        if path in OWS_PATHS:
            if method == "POST":
                length = int(environ.get("CONTENT_LENGTH") or 0)
                if length > MAX_BODY:
                    return _refuse(start_response, "413 Payload Too Large", f"request body over {MAX_BODY} bytes")
                body = environ["wsgi.input"].read(length)
                if not (_WCS_BODY.search(body) and _WCS_SERVICE.search(body)):
                    return _refuse(start_response, "403 Forbidden", "only WCS requests are served here")
            else:
                params = {k.lower(): [v.lower() for v in vs] for k, vs in parse_qs(query, keep_blank_values=True).items()}
                if params.get("service") != ["wcs"]:
                    return _refuse(start_response, "403 Forbidden", "only service=WCS is served here")
        elif not path.startswith(STATIC_PREFIX) or ".." in path or method == "POST":
            return _refuse(start_response, "403 Forbidden", "not available through this gateway")

        headers = {h: environ["HTTP_" + h.upper().replace("-", "_")] for h in FORWARD_REQUEST_HEADERS
                   if "HTTP_" + h.upper().replace("-", "_") in environ}
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        try:
            r = session.request(method, upstream + path + (f"?{query}" if query else ""), data=body or None,
                                headers=headers, timeout=UPSTREAM_TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException:
            return _refuse(start_response, "502 Bad Gateway", "the coverage service is not reachable")
        out = [(h, r.headers[h]) for h in FORWARD_RESPONSE_HEADERS if h in r.headers]
        start_response(f"{r.status_code} {r.reason or ''}".strip(), out)
        return r.iter_content(chunk_size=64 * 1024) if method != "HEAD" else []

    return app
