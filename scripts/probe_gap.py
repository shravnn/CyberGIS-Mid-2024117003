"""Record dated, reproducible HTTP probes that back the gap argument in docs/gap-evidence.md.

    python scripts/probe_gap.py            # writes docs/gap-probes.json and prints a table

One polite GET per URL (20 s timeout), identifying itself. Nothing is authenticated or brute-forced.
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path(__file__).resolve().parents[1] / "docs" / "gap-probes.json"
HEADERS = {"User-Agent": "chlwcs-gap-probe/0.1 (student coursework; one request per URL)"}
TIMEOUT = 20

BH, BHAPI = "https://bhoonidhi.nrsc.gov.in", "https://bhoonidhi-api.nrsc.gov.in"
MIRROR, MOSDAC = "https://bhoonidhi-stac.ecotrakr.in", "https://mosdac.gov.in"

PROBES = [
    # (what we are checking, url)
    ("Bhoonidhi API documentation page", f"{BH}/bhoonidhi-api/"),
    ("Bhoonidhi API: STAC landing page", f"{BHAPI}/"),
    ("Bhoonidhi API: OGC conformance declaration", f"{BHAPI}/conformance"),
    ("Bhoonidhi API: OGC API collections", f"{BHAPI}/collections"),
    ("Bhoonidhi API: STAC collections (needs login)", f"{BHAPI}/data/collections"),
    ("Bhoonidhi API: WCS GetCapabilities", f"{BHAPI}/?service=WCS&request=GetCapabilities"),
    ("Bhoonidhi API: WMS GetCapabilities", f"{BHAPI}/?service=WMS&request=GetCapabilities"),
    ("Bhoonidhi portal: WCS GetCapabilities", f"{BH}/?service=WCS&request=GetCapabilities"),
    ("Bhoonidhi portal: CSW GetCapabilities", f"{BH}/?service=CSW&request=GetCapabilities"),
    ("Unofficial STAC mirror: landing page", f"{MIRROR}/"),
    ("Unofficial STAC mirror: conformance", f"{MIRROR}/conformance"),
    ("MOSDAC: home", f"{MOSDAC}/"),
    ("MOSDAC: OGC conformance declaration", f"{MOSDAC}/conformance"),
    ("MOSDAC: WCS GetCapabilities", f"{MOSDAC}/?service=WCS&request=GetCapabilities"),
]


CAPABILITIES_XML = re.compile(r"<(\w+:)?(WCS_Capabilities|Capabilities|WMS_Capabilities|WMT_MS_Capabilities)")
OGC_TERMS = ["WMS", "WCS", "WFS", "CSW", "WPS", "SOS", "SensorThings", "OGC API", "STAC"]
SPEC_URL = f"{BH}/bhoonidhi-api/"


def probe(label, url):
    row = {"check": label, "url": url, "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        body = r.text[:300].replace("\n", " ").replace("\r", " ")
        title = re.search(r"<title[^>]*>(.*?)</title>", r.text, re.S | re.I)
        row.update(status=r.status_code, final_url=r.url, content_type=r.headers.get("Content-Type", ""),
                   bytes=len(r.content), title=title.group(1).strip() if title else None,
                   is_ogc_capabilities_xml=bool(CAPABILITIES_XML.search(r.text)), snippet=body)
        if "json" in row["content_type"] and url.endswith("/conformance"):
            row["conformsTo"] = r.json().get("conformsTo", [])
            row["declares_edr"] = any("ogcapi-edr" in c for c in row["conformsTo"])
    except requests.RequestException as err:
        row.update(status=None, error=f"{type(err).__name__}: {err}"[:200])
    return row


def spec_keyword_counts():
    """Count OGC service terms in the official Bhoonidhi API specification text."""
    r = requests.get(SPEC_URL, headers=HEADERS, timeout=TIMEOUT)
    text = re.sub(r"<[^>]+>", " ", r.text)
    counts = {t: len(re.findall(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", text)) for t in OGC_TERMS}
    chl = sorted(set(re.findall(r"EOS-06_OCM[-\w]*Chlorophyll[-\w]*", text)))
    return {"check": "Official API spec: OGC keyword counts", "url": SPEC_URL,
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts,
            "chlorophyll_collections_listed": chl}


def vista_terms():
    """Count OGC operation names in Bhoonidhi Vista's own page and script (its WMS visualisation client)."""
    terms = ["WMS", "WCS", "GetMap", "GetFeatureInfo", "GetCoverage", "GetCapabilities"]
    counts = {}
    for name, url in (("page", f"{BH}/vista"), ("vista.js", f"{BH}/vista/js/vista.js")):
        text = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
        # plain substring counts: Vista's "WMS" sits inside a longer identifier (an OpenLayers tile-source name)
        counts[name] = {"bytes": len(text), **{k: text.count(k) for k in terms}}
    return {"check": "Vista web app: OGC operation names in its page and script", "url": f"{BH}/vista",
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "counts": counts}


def main():
    rows = [probe(*p) for p in PROBES]
    rows.append(vista_terms())
    rows.append(spec_keyword_counts())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf8")
    for r in rows[:-2]:
        what = r.get("error") or f"{r.get('content_type', '')[:26]:<26} ogc-capabilities-xml={r.get('is_ogc_capabilities_xml')} bytes={r.get('bytes')}"
        print(f"{str(r.get('status')):>5}  {r['check']:<48} {what}")
    print("\nVista client code:", rows[-2]["counts"])
    print("spec keyword counts:", rows[-1]["counts"])
    print("chlorophyll collections:", rows[-1]["chlorophyll_collections_listed"])
    print(f"\nwrote {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
