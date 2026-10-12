"""Start, configure and stop a project-local GeoServer that serves the chlorophyll mosaic over WCS.

The data directory lives inside the project, so any other GeoServer data on the machine is left alone,
and the server binds to 127.0.0.1 only (its default admin password is never reachable from the network).
"""
from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import requests

WORKSPACE = "ocean"
STORE = "chl"
COVERAGE = "chl"
DEFAULT_PORT = 8080
DEFAULT_ADMIN = ("admin", "geoserver")
TIMEOUT = 30
MAX_REQUEST_KB = 400_000
ADVERTISED_CRS = ("4326", "3857", "32643", "32644")   # native lon/lat, web mercator, UTM 43N/44N (peninsular India)
WCS_TITLE = "Bhoonidhi EOS-06 Chlorophyll WCS (unofficial)"
WCS_ABSTRACT = (
    "Web Coverage Service over the EOS-06 (Oceansat-3) OCM-3 chlorophyll-a 8-day composites distributed by "
    "ISRO/NRSC Bhoonidhi. Bhoonidhi offers these only as whole-file downloads (plus STAC search and WMS pictures); "
    "this service returns pixel values for a bounding box, a time and an output format. "
    "Source data (c) NRSC/ISRO. Student coursework, not affiliated with ISRO/NRSC.")


class GeoServerError(RuntimeError):
    pass


def find_home() -> Path:
    """GEOSERVER_HOME if it holds a binary distribution, else ~/geoserver (the variable can be stale)."""
    candidates = [Path(p) for p in (os.environ.get("GEOSERVER_HOME"), Path.home() / "geoserver") if p]
    for home in candidates:
        if (home / "start.jar").exists():
            return home
    raise GeoServerError("no GeoServer binary distribution (needs start.jar) found; tried "
                         + ", ".join(map(str, candidates)) + ". Set GEOSERVER_HOME.")


def base_url(port: int = DEFAULT_PORT) -> str:
    return f"http://127.0.0.1:{port}/geoserver"


def _java() -> str:
    java_home = os.environ.get("JAVA_HOME")
    return str(Path(java_home) / "bin" / "java") if java_home else "java"


def pid_file(data_dir: Path, port: int) -> Path:
    return Path(data_dir).parent / f"geoserver-{port}.pid"


def start(data_dir: Path, port: int = DEFAULT_PORT, log_path: Path | None = None, memory_mb: int = 1024,
          home: Path | None = None) -> subprocess.Popen:
    """Launch GeoServer (does not wait for it to be ready) and record its process id."""
    home = home or find_home()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_path or data_dir.parent / f"geoserver-{port}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _java(), "-Xms256m", f"-Xmx{memory_mb}m", f"-DGEOSERVER_DATA_DIR={data_dir}", "-Djava.awt.headless=true",
        "-Duser.timezone=UTC",  # the machine's local zone otherwise shifts the time envelope (IST: -5:30)
        # start.ini fixes the port and host, so -D system properties are ignored: pass them as arguments
        "-jar", "start.jar", f"jetty.http.port={port}", "jetty.http.host=127.0.0.1",
    ]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(cmd, cwd=home, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    pid_file(data_dir, port).write_text(str(proc.pid))
    return proc


def wait_ready(port: int = DEFAULT_PORT, timeout: float = 180, proc: subprocess.Popen | None = None) -> None:
    url = f"{base_url(port)}/ows?service=WCS&request=GetCapabilities&version=2.0.1"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise GeoServerError(f"GeoServer exited early with code {proc.returncode}; see its log")
        try:
            if requests.get(url, timeout=5).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise GeoServerError(f"GeoServer did not answer on port {port} within {timeout:.0f}s")


def stop(data_dir: Path, port: int = DEFAULT_PORT, proc: subprocess.Popen | None = None) -> bool:
    """Terminate the GeoServer we started (by handle, else by the recorded pid). True if one was stopped.

    Jetty's own `--stop` is not used: it needs an extra listening port and did not stop this GeoServer.
    Configuration is written when it is changed, so terminating loses nothing.
    """
    pid = proc.pid if proc is not None else None
    pf = pid_file(data_dir, port)
    if pid is None and pf.exists():
        pid = int(pf.read_text().strip())
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pf.unlink(missing_ok=True)
        return False
    deadline = time.time() + 20
    while time.time() < deadline and _port_open(port):
        time.sleep(0.5)
    pf.unlink(missing_ok=True)
    return True


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---- configuration over REST -------------------------------------------------------------------
class Rest:
    def __init__(self, port: int, auth: tuple[str, str]):
        self.root = f"{base_url(port)}/rest"
        self.s = requests.Session()
        self.s.auth = auth

    def call(self, method: str, path: str, ok=(200, 201), **kw) -> requests.Response:
        r = self.s.request(method, f"{self.root}{path}", timeout=TIMEOUT, **kw)
        if r.status_code not in ok:
            raise GeoServerError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r


def _admin_file(data_dir: Path) -> Path:
    return Path(data_dir).parent / "geoserver-admin-password.txt"


def _authenticate(port: int, data_dir: Path) -> tuple[str, str]:
    """The stored password if we already changed it, else GeoServer's factory default."""
    f = _admin_file(data_dir)
    candidates = [("admin", f.read_text().strip())] if f.exists() else []
    for auth in candidates + [DEFAULT_ADMIN]:
        if requests.get(f"{base_url(port)}/rest/about/version.json", auth=auth, timeout=TIMEOUT).status_code == 200:
            return auth
    raise GeoServerError("cannot authenticate to GeoServer REST with the stored or default admin password")


def configure(mosaic_dir: Path, data_dir: Path, port: int = DEFAULT_PORT, secure: bool = True) -> None:
    """Idempotent: create workspace, mosaic store, coverage, time dimension, WCS metadata; then lock the admin password."""
    mosaic_dir = Path(mosaic_dir).resolve()
    if not list(mosaic_dir.glob("chl_*.tif")) or not (mosaic_dir / "indexer.properties").exists():
        raise GeoServerError(f"{mosaic_dir} has no chl_*.tif / indexer.properties: run scripts/prep_data.py first")
    rest = Rest(port, _authenticate(port, data_dir))

    if rest.call("GET", f"/workspaces/{WORKSPACE}.json", ok=(200, 404)).status_code == 404:
        rest.call("POST", "/workspaces", json={"workspace": {"name": WORKSPACE}})
    # drop a previous store so re-running picks up added/removed granules and indexer changes.
    # No `purge`: purge=all could delete the GeoTIFFs themselves.
    if rest.call("GET", f"/workspaces/{WORKSPACE}/coveragestores/{STORE}.json", ok=(200, 404)).status_code == 200:
        rest.call("DELETE", f"/workspaces/{WORKSPACE}/coveragestores/{STORE}?recurse=true", ok=(200,))
    # files GeoServer generates next to the GeoTIFFs; inputs (indexer/regex/datastore .properties) are kept
    for pattern in ("chl.*", "chl_index.gpkg*", "mosaic.properties", "sample_image*", ".*"):
        for stale in mosaic_dir.glob(pattern):
            if stale.is_file():
                stale.unlink()
    rest.call("PUT", f"/workspaces/{WORKSPACE}/coveragestores/{STORE}/external.imagemosaic",
              data=mosaic_dir.as_posix(), headers={"Content-Type": "text/plain"})

    coverage = {"coverage": {
        "name": COVERAGE, "nativeName": COVERAGE, "enabled": True,
        "title": "EOS-06 OCM-3 GAC chlorophyll-a, 8-day composites (4 km)",
        "abstract": ("Chlorophyll-a concentration (mg m-3), level-3 8-day composites on the 4 km GAC grid, cropped to "
                     "40-100 E, 10 S-30 N. Each time step is a composite window; the time dimension holds the "
                     "window interval (start-end, UTC). No-data cells (cloud, land, no coverage) carry the nodata value -9999."),
        "keywords": {"string": ["chlorophyll-a", "ocean colour", "EOS-06", "Oceansat-3", "Bhoonidhi"]},
        # GeoServer's default band is "GRAY_INDEX" with radiance units; describe the real quantity
        "dimensions": {"coverageDimension": [{
            "name": "chlorophyll_a", "description": "Chlorophyll-a concentration", "unit": "mg m-3",
            "range": {"min": 0.0, "max": 10.0}, "nullValues": {"double": [-9999.0]},
            "dimensionType": {"name": "REAL_32BITS"}}]},
        "metadata": {"entry": [{"@key": "time", "dimensionInfo": {
            "enabled": True, "presentation": "LIST", "units": "ISO8601", "defaultValue": {"strategy": "MAXIMUM"}}}]},
    }}
    # FLAT (the default) paints overlapping granules into ONE image, so a time range would silently mix windows.
    # STACK returns one band per granule instead: time range -> time series, one band per window, ascending in time.
    current = rest.call("GET", f"/workspaces/{WORKSPACE}/coveragestores/{STORE}/coverages/{COVERAGE}.json").json()
    entries = [e for e in current["coverage"]["parameters"]["entry"] if e["string"][0] != "MergeBehavior"]
    coverage["coverage"]["parameters"] = {"entry": entries + [{"string": ["MergeBehavior", "STACK"]}]}
    rest.call("PUT", f"/workspaces/{WORKSPACE}/coveragestores/{STORE}/coverages/{COVERAGE}.json", json=coverage)

    # read-modify-write: a PUT replaces the whole settings object, so send back every field GeoServer gave us
    wcs_settings = rest.call("GET", "/services/wcs/settings.json").json()
    wcs_settings["wcs"].update({
        "enabled": True, "name": "WCS", "title": WCS_TITLE, "abstrct": WCS_ABSTRACT,  # sic: GeoServer's own spelling
        "maintainer": "CyberGIS mid-semester coursework", "fees": "NONE",
        # KB. The whole 17-window grid is ~94 MB; the limits only stop absurd requests (e.g. scalefactor=5000)
        "maxInputMemory": MAX_REQUEST_KB, "maxOutputMemory": MAX_REQUEST_KB,
        "accessConstraints": "Source data (c) NRSC/ISRO, Bhoonidhi open data; attribution required.",
        "keywords": {"string": ["WCS", "chlorophyll", "ocean colour", "EOS-06", "Bhoonidhi"]},
        "versions": {"org.geotools.util.Version": [{"version": "1.0.0"}, {"version": "1.1.1"}, {"version": "2.0.1"}]},
        # Unset, GeoServer advertises every CRS it knows: a 637 KB capabilities document that took 57 s through the
        # public tunnel. Other CRSs still work as outputcrs; they are just not listed.
        "srs": {"string": list(ADVERTISED_CRS)},
    })
    rest.call("PUT", "/services/wcs/settings.json", json=wcs_settings)

    if secure and rest.s.auth == DEFAULT_ADMIN:
        password = secrets.token_urlsafe(18)
        rest.call("PUT", "/security/self/password", json={"newPassword": password}, ok=(200, 201, 204))
        _admin_file(data_dir).write_text(password)
        _wait_for_password(port, ("admin", password))


def set_proxy_base_url(data_dir: Path, url: str | None, port: int = DEFAULT_PORT) -> None:
    """Make GeoServer write `url` (the public tunnel/gateway address) into capabilities links; None clears it.

    Without it GeoServer builds links from the Host header it receives, and behind a TLS-terminating tunnel it
    would advertise http:// for an https:// service.
    """
    rest = Rest(port, _authenticate(port, data_dir))
    # PUT replaces the whole settings object: sending only proxyBaseUrl wipes the charset and the rest, and GeoServer
    # then fails every request with "Null charset name" after its next restart. Read, change one field, send it all back.
    settings = rest.call("GET", "/settings.json").json()
    settings["global"]["settings"]["proxyBaseUrl"] = url
    rest.call("PUT", "/settings.json", json=settings)


def _wait_for_password(port: int, auth: tuple[str, str], timeout: float = 60) -> None:
    """GeoServer reloads its security files asynchronously, so a new password is not valid for a few seconds."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if requests.get(f"{base_url(port)}/rest/about/version.json", auth=auth, timeout=TIMEOUT).status_code == 200:
            return
        time.sleep(0.5)
    raise GeoServerError("the new admin password did not become active in time")
