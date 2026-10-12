"""Export the cube as one GeoTIFF per composite window, plus the GeoServer ImageMosaic indexer files.

A GeoServer ImageMosaic turns the folder into a single WCS coverage with a time dimension; each granule's
validity interval comes from its file name, so the name carries both window start and (inclusive) end.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
import xarray as xr
from rasterio.transform import from_origin

NODATA = -9999.0
_TIME_FORMAT = "yyyyMMdd'T'HHmmss"
_LAST_SECOND = np.timedelta64(1, "s")
_BLOCK = 256


def _stamp(t) -> str:
    return np.datetime_as_string(np.datetime64(t, "s"), unit="s").replace("-", "").replace(":", "")


def window_file_name(start, end_exclusive) -> str:
    """`chl_<start>_<last second of the window>.tif`; the cube stores the end as exclusive."""
    return f"chl_{_stamp(start)}_{_stamp(np.datetime64(end_exclusive, 's') - _LAST_SECOND)}.tif"


def _iso_z(t) -> str:
    return np.datetime_as_string(np.datetime64(t, "s"), unit="s") + "Z"


def write_geotiffs(cube_path, out_dir) -> list[Path]:
    """Write chl_*.tif into out_dir, removing any chl_*.tif left over from an earlier run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("chl_*.tif"):
        stale.unlink()
    written = []
    with xr.open_dataset(cube_path) as ds:
        lon, lat = ds.lon.values.astype("float64"), ds.lat.values.astype("float64")
        step_x, step_y = float(lon[1] - lon[0]), float(lat[1] - lat[0])
        # cube nodes are cell centres, so the raster's outer edges sit half a cell beyond them
        transform = from_origin(lon[0] - step_x / 2, lat[-1] + step_y / 2, step_x, step_y)
        for i in range(ds.sizes["time"]):
            start, end = ds.time.values[i], ds.time_end.values[i]
            # cube rows run south to north; GeoTIFF rows must run north to south
            data = np.flipud(ds.chl.isel(time=i).values.astype("float32"))
            data = np.where(np.isnan(data), np.float32(NODATA), data)
            path = out_dir / window_file_name(start, end)
            with rasterio.open(
                path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1, dtype="float32",
                crs="EPSG:4326", transform=transform, nodata=NODATA, compress="deflate", predictor=3,
                tiled=True, blockxsize=_BLOCK, blockysize=_BLOCK,
            ) as dst:
                dst.write(data, 1)
                dst.set_band_description(1, "chlorophyll_a")
                dst.units = ("mg m-3",)
                dst.update_tags(
                    window_start=_iso_z(start), window_end_exclusive=_iso_z(end),
                    source_product=str(ds.source_product.values[i]),
                    source="EOS-06 OCM-3 GAC chlorophyll-a, Bhoonidhi (NRSC/ISRO); repackaged unofficially",
                )
            written.append(path)
    return written


def write_mosaic_config(mosaic_dir) -> None:
    """GeoServer ImageMosaic indexer: time interval per granule read from the file name."""
    mosaic_dir = Path(mosaic_dir).resolve()
    # The default shapefile index keeps DATES only (DBF), which silently turns every window end 23:59:59 into
    # 00:00 and drops the last day of each window. A GeoPackage index keeps full timestamps.
    (mosaic_dir / "datastore.properties").write_text(
        "SPI=org.geotools.geopkg.GeoPkgDataStoreFactory\n"
        "dbtype=geopkg\n"
        f"database={(mosaic_dir / 'chl_index.gpkg').as_posix()}\n")
    (mosaic_dir / "indexer.properties").write_text(
        "Name=chl\n"
        "Schema=*the_geom:Polygon,location:String,time:java.sql.Timestamp,endtime:java.sql.Timestamp\n"
        "TimeAttribute=time;endtime\n"
        "PropertyCollectors=TimestampFileNameExtractorSPI[timeregex](time),"
        "TimestampFileNameExtractorSPI[timeregexend](endtime)\n")
    # GeoServer wants `regex=...,format=...` on ONE line, and matches against the name without its extension.
    # start = the first timestamp in the name; end = the last one (nothing timestamp-like after it).
    stamp = "[0-9]{8}T[0-9]{6}"
    (mosaic_dir / "timeregex.properties").write_text(f"regex={stamp},format={_TIME_FORMAT}\n")
    (mosaic_dir / "timeregexend.properties").write_text(f"regex={stamp}(?!.*{stamp}),format={_TIME_FORMAT}\n")
