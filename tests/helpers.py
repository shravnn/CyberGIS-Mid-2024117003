"""Fabricate tiny Bhoonidhi-shaped EOS-06 products (1-degree global grid) for tests."""
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import xarray as xr

FILL = -32768
GS_PORT = 18080  # port of the GeoServer the integration tests boot


def product_id(start: date) -> str:
    end = start + timedelta(days=7)
    # real names upper-case the month but keep the lower-case "km" suffix
    return f"E06_OCM_GAC_{start:%d%b%Y}_{end:%d%b%Y}".upper() + "_8_T000000_MGG0CLHND_4km"


def chl_value(k: int, lat: float, lon: float) -> float:
    """Deterministic mg m-3 field: every cell/window has a unique, recomputable value."""
    return round(0.1 + 0.5 * k + 0.01 * (lat + 10) + 0.001 * (lon - 40), 3)


def make_product(out_dir: Path, start: date, k: int, nlat: int = 181, nlon: int = 361) -> Path:
    """Write `<out_dir>/<id>.zip` shaped like the real download (BAND.nc + .meta + .jpg).

    k is the window index used by chl_value. Cells with lon < 45 are no-data (cloud/land stand-in).
    """
    lat = np.linspace(-90, 90, nlat, dtype="float32")
    lon = np.linspace(-180, 180, nlon, dtype="float32")
    raw = np.full((1, 1, nlat, nlon), FILL, dtype="int16")
    for i, la in enumerate(lat):
        for j, lo in enumerate(lon):
            if lo >= 45:
                raw[0, 0, i, j] = round(chl_value(k, float(la), float(lo)) * 1000)
    ds = xr.Dataset(
        {"chl": (("Time", "Depth", "Latitude", "Longitude"), raw,
                 {"units": "mg/m3", "scale_factor": np.float32(0.001), "_FillValue": np.int16(FILL)})},
        coords={
            "Time": ("Time", np.array([1], dtype="int32"), {"units": "days since 1993-01-01 00:00:00"}),
            "Depth": ("Depth", np.array([0], dtype="int32"), {"units": "meters"}),
            "Latitude": ("Latitude", lat, {"units": "degrees_north"}),
            "Longitude": ("Longitude", lon, {"units": "degrees_east"}),
        },
    )
    pid = product_id(start)
    out_dir.mkdir(parents=True, exist_ok=True)
    nc = out_dir / f"{pid}.BAND.nc"
    ds.to_netcdf(nc)
    zpath = out_dir / f"{pid}.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(nc, f"{pid}/BAND.nc")
        zf.writestr(f"{pid}/{pid}.meta", f"ProductID= {pid}\nunits= mg/m3\n")
        zf.writestr(f"{pid}/{pid}.jpg", b"jpg")
    nc.unlink()
    return zpath
