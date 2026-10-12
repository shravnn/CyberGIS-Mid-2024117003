"""Run the WCS: a project-local GeoServer serving the chlorophyll mosaic.

    python scripts/geoserver.py start          # start (127.0.0.1:8080), configure on first run, wait until ready
    python scripts/geoserver.py configure      # re-create the layer after changing data/wcs/mosaic
    python scripts/geoserver.py status
    python scripts/geoserver.py stop
    python scripts/geoserver.py proxy-url https://my.public.host   # or --clear: public URL in capabilities links

GEOSERVER_HOME (default ~/geoserver) must point at a GeoServer *binary* distribution (Java 17+).
The data directory is data/geoserver/data_dir inside this project; no other GeoServer data is touched.
"""
import argparse
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chlwcs import geoserver as gs  # noqa: E402

DATA_DIR = ROOT / "data" / "geoserver" / "data_dir"
MOSAIC = ROOT / "data" / "wcs" / "mosaic"
LOG = ROOT / "data" / "geoserver" / "geoserver.log"


def is_up(port):
    try:
        return requests.get(f"{gs.base_url(port)}/ows?service=WCS&request=GetCapabilities&version=2.0.1",
                            timeout=3).status_code == 200
    except requests.RequestException:
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["start", "configure", "status", "stop", "proxy-url"])
    ap.add_argument("url", nargs="?", help="proxy-url: the public base URL to advertise in capabilities links")
    ap.add_argument("--clear", action="store_true", help="proxy-url: remove the public URL again")
    ap.add_argument("--port", type=int, default=gs.DEFAULT_PORT)
    args = ap.parse_args()

    if args.command == "proxy-url":
        if bool(args.url) == args.clear:
            ap.error("proxy-url needs exactly one of: a URL, or --clear")
        gs.set_proxy_base_url(DATA_DIR, None if args.clear else args.url.rstrip("/") + "/geoserver", args.port)
        print("proxy base URL cleared" if args.clear else f"proxy base URL set to {args.url}/geoserver")
    elif args.command == "status":
        print("up" if is_up(args.port) else "down", gs.base_url(args.port))
    elif args.command == "stop":
        print("stopped" if gs.stop(DATA_DIR, args.port) else "no GeoServer recorded for this port")
    elif args.command == "configure":
        gs.configure(MOSAIC, DATA_DIR, args.port)
        print("configured")
    else:
        if is_up(args.port):
            print(f"already running on port {args.port}")
            return
        first_run = not (DATA_DIR / "workspaces" / gs.WORKSPACE).exists()
        proc = gs.start(DATA_DIR, args.port, LOG)
        print(f"starting GeoServer (log: {LOG}) ...", flush=True)
        gs.wait_ready(args.port, proc=proc)
        if first_run:
            print("first run: configuring workspace, mosaic, time dimension, WCS metadata ...", flush=True)
            gs.configure(MOSAIC, DATA_DIR, args.port)
        print(f"ready: {gs.base_url(args.port)}/ows?service=WCS&request=GetCapabilities&version=2.0.1")


if __name__ == "__main__":
    main()
