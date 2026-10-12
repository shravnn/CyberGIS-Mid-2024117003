"""Turn Bhoonidhi EOS-06 OCM-3 L3 GAC zips into one time-indexed, CF-style NetCDF cube.

The files are not self-describing: `Time` is the constant 1 ("days since 1993-01-01"), so the
real period is parsed from the product name instead.
"""
from __future__ import annotations

import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import xarray as xr

PRODUCT_RE = re.compile(
    r"E06_OCM_GAC_(?P<start>\d{2}[A-Z]{3}\d{4})_(?P<end>\d{2}[A-Z]{3}\d{4})_8_T000000_MGG0CLHND_4km"
)
FILL_VALUE = -32768
SCALE_FACTOR = 0.001
DEFAULT_BBOX = (40.0, -10.0, 100.0, 30.0)  # lon_min, lat_min, lon_max, lat_max
_EDGE_EPS = 1e-6
_AXIS_TOLERANCE_CELLS = 0.05


@dataclass(frozen=True)
class Window:
    product_id: str
    start: date
    end: date  # inclusive, as printed in the product name

    @property
    def end_exclusive(self) -> date:
        return self.end + timedelta(days=1)


def _parse_day(text: str) -> date:
    return datetime.strptime(text, "%d%b%Y").date()


def parse_product_id(name: str) -> Window:
    m = PRODUCT_RE.search(name)
    if not m:
        raise ValueError(f"not an EOS-06 OCM GAC chlorophyll product name: {name!r}")
    start, end = _parse_day(m["start"]), _parse_day(m["end"])
    if end < start:
        raise ValueError(f"window ends before it starts: {name!r}")
    return Window(m.group(0), start, end)


def find_products(raw_dir: Path) -> list[Path]:
    zips = [p for p in Path(raw_dir).glob("*.zip") if PRODUCT_RE.search(p.name)]
    return sorted(zips, key=lambda p: (parse_product_id(p.name).start, p.name))


def _regular_axis(observed: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Rebuild the exact global axis; the files store float32 coordinates whose steps drift."""
    n = len(observed)
    step = (hi - lo) / (n - 1)
    exact = lo + step * np.arange(n)
    if np.abs(exact - observed).max() > _AXIS_TOLERANCE_CELLS * step:
        raise ValueError("axis is not the expected regular global grid")
    return exact


def read_window(zip_path, bbox=DEFAULT_BBOX):
    """Return (lat, lon, raw int16 [lat, lon]) for one product, cropped to bbox."""
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("BAND.nc"))
        zf.extract(member, tmp)
        with xr.open_dataset(Path(tmp) / member, decode_times=False, mask_and_scale=False) as ds:
            lat = _regular_axis(ds["Latitude"].values.astype("float64"), -90.0, 90.0)
            lon = _regular_axis(ds["Longitude"].values.astype("float64"), -180.0, 180.0)
            ix = np.where((lon >= bbox[0] - _EDGE_EPS) & (lon <= bbox[2] + _EDGE_EPS))[0]
            iy = np.where((lat >= bbox[1] - _EDGE_EPS) & (lat <= bbox[3] + _EDGE_EPS))[0]
            raw = ds["chl"].isel(
                Time=0, Depth=0,
                Latitude=slice(iy[0], iy[-1] + 1), Longitude=slice(ix[0], ix[-1] + 1),
            ).values
    return lat[iy[0]: iy[-1] + 1], lon[ix[0]: ix[-1] + 1], raw.astype("int16")


def build_cube(zip_paths, out_path, bbox=DEFAULT_BBOX) -> Path:
    zip_paths = [Path(p) for p in zip_paths]
    if not zip_paths:
        raise ValueError("no products given")
    windows = [parse_product_id(p.name) for p in zip_paths]
    if len({w.start for w in windows}) != len(windows):
        raise ValueError("duplicate window start in input products")

    layers, lat, lon = [], None, None
    for zp in zip_paths:
        la, lo, raw = read_window(zp, bbox)
        if lat is None:
            lat, lon = la, lo
        elif not (np.array_equal(la, lat) and np.array_equal(lo, lon)):
            raise ValueError(f"{zp.name} is on a different grid from the other products")
        layers.append(raw)

    order = sorted(range(len(windows)), key=lambda i: windows[i].start)
    stack = np.stack([layers[i] for i in order])
    ws = [windows[i] for i in order]

    chl = xr.DataArray(
        stack, dims=("time", "lat", "lon"),
        attrs={
            "standard_name": "mass_concentration_of_chlorophyll_in_sea_water",
            "long_name": "Chlorophyll-a concentration",
            "units": "mg m-3",
            "scale_factor": SCALE_FACTOR,
            "_FillValue": np.int16(FILL_VALUE),
        },
    )
    built = datetime.now(timezone.utc)
    ds = xr.Dataset(
        {"chl": chl},
        coords={
            "time": ("time", np.array([np.datetime64(w.start, "ns") for w in ws]),
                     {"long_name": "composite window start (UTC, inclusive)"}),
            "time_end": ("time", np.array([np.datetime64(w.end_exclusive, "ns") for w in ws]),
                         {"long_name": "composite window end (UTC, exclusive)"}),
            "source_product": ("time", np.array([w.product_id for w in ws])),
            "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude", "axis": "Y"}),
            "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude", "axis": "X"}),
        },
        attrs={
            "title": "EOS-06 OCM-3 GAC chlorophyll-a 8-day composites (4 km), cropped",
            "institution": "NRSC / ISRO (data); repackaged unofficially",
            "source": "Bhoonidhi (https://bhoonidhi.nrsc.gov.in) product MGG0CLHND, level L3",
            "Conventions": "CF-1.8",
            "history": f"built {built:%Y-%m-%dT%H:%M:%SZ} by chlwcs.ingest; crop {bbox}",
        },
    )
    enc = {
        "chl": {"zlib": True, "complevel": 4, "chunksizes": (1, min(240, len(lat)), min(360, len(lon)))},
        "time": {"units": "days since 1970-01-01 00:00:00", "dtype": "int64"},
        "time_end": {"units": "days since 1970-01-01 00:00:00", "dtype": "int64"},
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out_path, format="NETCDF4", encoding=enc)
    return out_path
