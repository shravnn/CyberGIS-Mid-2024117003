# Gap evidence

**Claim (Step 2, first bullet).** The EOS-06 (Oceansat-3) OCM-3 chlorophyll-a composites on ISRO/NRSC **Bhoonidhi** are
*a dataset that currently requires manual download but would be better served via WCS*. They can be searched (STAC),
looked at (Vista, a WMS-based viewer) and downloaded as whole global files. There is no **Web Coverage Service**, so a client
cannot ask for the pixel values of a bounding box, a point, a time, a band or an output format.

Everything below is reproducible. Probe log: [`gap-probes.json`](gap-probes.json), taken **2026-10-05, 21:33–21:34 UTC** by
[`scripts/probe_gap.py`](../scripts/probe_gap.py) (one GET per URL, self-identifying, unauthenticated, nothing brute-forced).

## 1. What Bhoonidhi offers today

| Interface | Source | Returns pixel values for an area/time? |
|---|---|---|
| Portal (cart download) | <https://bhoonidhi.nrsc.gov.in> | whole-file zip only |
| **STAC API** (JWT login; 20 auth requests/h, 3 searches/s) | [API specification](https://bhoonidhi.nrsc.gov.in/bhoonidhi-api/) | no: item metadata + download links |
| **Vista** viewer | [bhoonidhi.nrsc.gov.in/vista](https://bhoonidhi.nrsc.gov.in/vista) | WMS-based visualisation (see §2) |
| Chlorophyll collection | `EOS-06_OCM-GAC_Chlorophyll_8day_4km` | a *product to download* |

**Measured, not assumed.** `probe_gap.py` fetches the official API specification and counts OGC service terms in its text:

| `STAC` | `WMS` | `WCS` | `WFS` | `CSW` | `WPS` | `SOS` | `SensorThings` | `OGC API` |
|---|---|---|---|---|---|---|---|---|
| **12** | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

The only chlorophyll collection it lists is the 8-day 4 km download product named above.

## 2. Probes for a WCS (or other OGC endpoint) that could already answer the question

HTTP status alone is misleading here, so the script also records whether the body is an OGC capabilities document
(`is_ogc_capabilities_xml`) and the page title.

| Probe (2026-10-05) | Status | What actually came back |
|---|---|---|
| `bhoonidhi-api…/?service=WCS&request=GetCapabilities` | 200 | **not capabilities XML**: the 60 660-byte *"Bhoonidhi API Specification"* HTML page, byte-identical to the plain landing page |
| `bhoonidhi-api…/?service=WMS&request=GetCapabilities` | 200 | same 60 660-byte docs page |
| `bhoonidhi.nrsc.gov.in/?service=WCS…` and `…service=CSW…` | 200 | **not capabilities XML**: the 36 515-byte portal home page |
| `bhoonidhi-api…/conformance`, `…/collections` (OGC API) | 404 | 36-byte plain-text not-found |
| `bhoonidhi-api…/data/collections` (STAC) | 401 | `Invalid session detected! Please login again.` (login-gated) |
| **Unofficial STAC mirror** `bhoonidhi-stac.ecotrakr.in/conformance` | 200 | 23 conformance classes: STAC 1.0/1.1, OGC API – Features 1/3, CQL2. **No coverages / WCS** |
| `mosdac.gov.in/conformance` | 404 | HTML not-found page |
| `mosdac.gov.in/?service=WCS…` | 403 | access restricted (MOSDAC is a separate, registration-based ISRO portal; this analysis concerns Bhoonidhi) |

Reading: the 200 responses to WCS/WMS/CSW requests are a catch-all page that ignores the query (same byte length for
different services), so no such service exists behind them.

**Vista.** Its client code was inspected as well (the probe counts operation names in the page and in `js/vista.js`):

| | `WMS` | `GetMap` | `GetFeatureInfo` | `WCS` | `GetCoverage` | `GetCapabilities` |
|---|---|---|---|---|---|---|
| Vista page (35 062 bytes) | 2 | 0 | 0 | **0** | **0** | 0 |
| `vista.js` (45 307 bytes) | 1 | 0 | 0 | **0** | **0** | 0 |

Vista is a map viewer: it draws layers through a Leaflet WMS layer (`L.TileLayer.featWMS`, `layers=<workspace>:<name>`) for
display. A WCS serves the data values themselves: bounding-box extraction, time ranges, band/CRS/format choice and a
machine-readable coverage description. That is the capability this project adds.

## 3. Why the downloaded files cannot stand in for a service

Output of [`scripts/inspect_product.py`](../scripts/inspect_product.py) on the real product
`E06_OCM_GAC_01AUG2025_08AUG2025_8_T000000_MGG0CLHND_4km.zip`:

```text
Time values      : [1]  units: 'days since 1993-01-01 00:00:00'
  decoded literally -> [datetime.date(1993, 1, 2)]   (the product covers a window in 2025)
dims             : {'Time': 1, 'Depth': 1, 'Latitude': 4321, 'Longitude': 8641}
global attrs     : none
chl attrs        : {'units': 'mg/m3'}   (no standard_name / long_name)
chl packing      : dtype=int16 scale_factor=0.001 _FillValue=-32768
grid             : 4321 x 8641, nominal step 0.04166667 deg; observed lon steps [0.04167175, 0.04165649, ...] (float32 drift)
decoded size     : 149 MB as float32; no-data share 63.0%
```

1. **The time axis is wrong.** Every file says `Time = 1` ("2 January 1993"). The real period exists only in the file name and a
   `.meta` sidecar, so software cannot stack files by time without parsing names.
2. **Products overlap.** Windows are published for every start day (01–08 Aug, 02–09 Aug …). A "series" is ambiguous unless
   each window's interval is explicit; this service's time dimension carries each window as a period (start–end, UTC).
3. **Nothing is self-describing:** int16 packing, no CF `standard_name`, capitalised dimensions, a pointless length-1 `Depth`,
   drifting float32 coordinates.
4. **Mostly empty:** 63 % of a global composite is no-data; over the monsoon Arabian Sea the valid share is far lower (cloud).
   WCS declares a nil value (`-9999`) in `DescribeCoverage`, so clients know what "no data" looks like.

## 4. Worked example: chlorophyll off the Kerala coast (10 °N, 75.5 °E), 22 Jun – 4 Oct 2025

| | Today with Bhoonidhi | With this WCS |
|---|---|---|
| Requests | log in → STAC search → 17 downloads | **one** `GetCoverage`: `subset=Long(75.5)&subset=Lat(10)&subset=time("2025-06-22…","2025-10-04…")` |
| Data moved | **407 MB** (17 zips, measured) | **17.9 KB** (a 17-band GeoTIFF, one band per window) |
| Client work | unzip, parse dates from names, decode int16 + fill, regrid/stack, pick the nearest cell | read a GeoTIFF |
| Server time | n/a | 0.63 s for the 17-window series; 32 ms for a 48 × 48 cell map of one window |

The values returned (mg m⁻³, `null` = no data; the five consecutive August windows share one observation because they overlap):

```text
22Jun 1.417 | 30Jun null | 08Jul null | 16Jul null | 24Jul null | 01-05Aug 6.758 ×5 | 09Aug null |
17Aug 6.904 | 25Aug 1.417 | 03Sep 3.914 | 11Sep 5.669 | 19Sep 1.062 | 27Sep 0.428
```

## 5. Why WCS rather than the other standards

| Candidate | Verdict |
|---|---|
| STAC | already exists (official API; unofficial public mirror): search + links, not values |
| WMS | already offered as Vista, and a map picture is not a coverage |
| WFS | vector features; this is a raster grid |
| WPS / CSW | would compute or catalogue something; the missing capability is plain access to the data |
| **WCS** | purpose-built: subset a coverage by space, time, band, CRS, scale and format, with a machine-readable description |

## 6. Method and reproducibility

* **Sources.** NRSC's public API specification and workshop material, the Bhoonidhi portal and Vista pages, and direct
  probes of the documented endpoints and the standard OGC request paths (one GET each), dated 2026-10-05 UTC.
* **Bhoonidhi STAC API.** It requires credentials; its capabilities are taken from its public specification.
* **Scope.** The analysis concerns Bhoonidhi, the distributor of these composites. MOSDAC is a separate, registration-based
  ISRO portal.
* **Refresh.** `python scripts/probe_gap.py` re-runs every probe and rewrites `docs/gap-probes.json`.
