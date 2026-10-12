"""Bhoonidhi EOS-06 chlorophyll composites -> GeoTIFF mosaic served by GeoServer WCS."""
import importlib.util
import os
from pathlib import Path


def _use_bundled_geodata() -> None:
    """Point PROJ/GDAL at the copies shipped inside the rasterio wheel, for this process only.

    A machine-wide PROJ_LIB (e.g. from a PostgreSQL/PostGIS install) otherwise wins and rasterio fails with
    "proj.db contains DATABASE.LAYOUT.VERSION.MINOR = 2 whereas a number >= 5 is expected".
    Runs on `import chlwcs`, which must happen before rasterio is first used.
    """
    spec = importlib.util.find_spec("rasterio")
    if spec is None or spec.origin is None:
        return
    base = Path(spec.origin).parent
    for variable, folder in (("PROJ_DATA", "proj_data"), ("PROJ_LIB", "proj_data"), ("GDAL_DATA", "gdal_data")):
        if (base / folder).is_dir():
            os.environ[variable] = str(base / folder)


_use_bundled_geodata()
