"""Run the whole service: GeoServer (WCS) + the gateway/web UI, optionally on a public URL.

    python scripts/run.py              # local:  http://127.0.0.1:8090  (UI)  and  .../geoserver/ows  (WCS)
    python scripts/run.py --public     # also a public https URL through an SSH tunnel (localhost.run)

Ctrl+C stops the gateway and the tunnel (GeoServer keeps running; `python scripts/geoserver.py stop` stops it).
The public URL is written to data/public_url.txt. Free localhost.run tunnels get a NEW random address when they
reconnect; this script notices, points GeoServer's capabilities links at the new address and records it.
"""
import argparse
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402
import waitress  # noqa: E402

from chlwcs import gateway, geoserver as gs, geotiff  # noqa: E402

DATA_DIR = ROOT / "data" / "geoserver" / "data_dir"
MOSAIC = ROOT / "data" / "wcs" / "mosaic"
CUBE = ROOT / "data" / "processed" / "ocm_gac_chl_8day.nc"
LOG = ROOT / "data" / "geoserver" / "geoserver.log"
URL_FILE = ROOT / "data" / "public_url.txt"
TUNNEL_LINE = re.compile(r"tunneled with tls termination, (https://[A-Za-z0-9.-]+)")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def ensure_mosaic() -> None:
    if list(MOSAIC.glob("chl_*.tif")) and (MOSAIC / "indexer.properties").exists():
        return
    if not CUBE.exists():
        raise SystemExit(f"{CUBE} is missing: run scripts/prep_data.py --raw <folder with the Bhoonidhi zips>")
    log("building the GeoTIFF mosaic from the cube ...")
    geotiff.write_geotiffs(CUBE, MOSAIC)
    geotiff.write_mosaic_config(MOSAIC)


def wcs_up(port: int) -> bool:
    try:
        return requests.get(f"{gs.base_url(port)}/ows?service=WCS&request=GetCapabilities&version=2.0.1",
                            timeout=5).status_code == 200
    except requests.RequestException:
        return False


def ensure_geoserver(port: int) -> None:
    if wcs_up(port):
        log(f"GeoServer already running on 127.0.0.1:{port}")
        return
    first_run = not (DATA_DIR / "workspaces" / gs.WORKSPACE).exists()
    proc = gs.start(DATA_DIR, port, LOG)
    log(f"starting GeoServer on 127.0.0.1:{port} (log: {LOG}) ...")
    gs.wait_ready(port, proc=proc)
    if first_run:
        log("first run: configuring workspace, mosaic, time dimension, WCS metadata ...")
        gs.configure(MOSAIC, DATA_DIR, port)
    log("GeoServer ready")


class Tunnel:
    """SSH reverse tunnel to localhost.run that restarts itself and follows the address it is given."""

    def __init__(self, local_port: int, geoserver_port: int):
        self.local_port, self.geoserver_port = local_port, geoserver_port
        self.url = None
        self.proc = None
        self.stopping = False

    def _announce(self, url: str) -> None:
        if url == self.url:
            return
        self.url = url
        gs.set_proxy_base_url(DATA_DIR, f"{url}/geoserver", self.geoserver_port)
        URL_FILE.write_text(url + "\n", encoding="utf8")
        log("=" * 72)
        log(f"PUBLIC URL:  {url}")
        log(f"  web UI:    {url}/")
        log(f"  WCS:       {url}/geoserver/ows?service=WCS&request=GetCapabilities&version=2.0.1")
        log("=" * 72)

    def _pump(self, stream) -> None:
        for line in iter(stream.readline, ""):
            m = TUNNEL_LINE.search(line)
            if m:
                self._announce(m.group(1))

    def run(self) -> None:
        delay = 2
        while not self.stopping:
            started = time.time()
            self.proc = subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30",
                 "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes",
                 "-R", f"80:127.0.0.1:{self.local_port}", "nokey@localhost.run"],
                stdin=subprocess.PIPE,                       # kept open: an EOF on stdin would end the session
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            self._pump(self.proc.stdout)
            self.proc.wait()
            if self.stopping:
                break
            delay = 2 if time.time() - started > 60 else min(delay * 2, 60)
            log(f"tunnel closed (exit {self.proc.returncode}); reconnecting in {delay}s ...")
            time.sleep(delay)

    def stop(self) -> None:
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--public", action="store_true", help="also publish through an SSH tunnel (localhost.run)")
    ap.add_argument("--port", type=int, default=8090, help="gateway / web UI port (default 8090)")
    ap.add_argument("--geoserver-port", type=int, default=gs.DEFAULT_PORT)
    args = ap.parse_args()

    ensure_mosaic()
    ensure_geoserver(args.geoserver_port)
    app = gateway.make_app(f"http://127.0.0.1:{args.geoserver_port}", MOSAIC)
    server = waitress.create_server(app, listen=f"127.0.0.1:{args.port}", threads=16)
    threading.Thread(target=server.run, daemon=True).start()
    log(f"web UI and WCS gateway: http://127.0.0.1:{args.port}/  (WCS only; GeoServer admin stays private)")

    tunnel = None
    if args.public:
        tunnel = Tunnel(args.port, args.geoserver_port)
        threading.Thread(target=tunnel.run, daemon=True).start()
        log("opening the public tunnel ...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("stopping ...")
    finally:
        if tunnel:
            tunnel.stop()
            try:
                gs.set_proxy_base_url(DATA_DIR, None, args.geoserver_port)
            except gs.GeoServerError:
                pass
            URL_FILE.unlink(missing_ok=True)
        server.close()


if __name__ == "__main__":
    main()
