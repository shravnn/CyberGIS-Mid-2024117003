---
title: Bhoonidhi Chlorophyll WCS
emoji: 🌊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: OGC WCS over ISRO Bhoonidhi EOS-06 chlorophyll-a data
---

# Bhoonidhi Chlorophyll WCS

An **OGC Web Coverage Service (WCS 2.0.1)** over ISRO/NRSC Bhoonidhi's EOS-06 (Oceansat-3) OCM-3 chlorophyll-a 8-day
composites, with an interactive web map.

* **Web UI:** this Space's page: pick a composite, see the map, click the sea for a time series.
* **WCS endpoint:** `/geoserver/ows?service=WCS&request=GetCapabilities&version=2.0.1` on this Space's URL. Works with
  any WCS client (QGIS, GDAL, `curl`).

Source code, documentation and verification: https://github.com/shravnn/CyberGIS-Mid-2024117003

Source data © National Remote Sensing Centre, ISRO (Bhoonidhi open data). Independent coursework, not affiliated with ISRO/NRSC.
