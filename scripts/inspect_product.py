"""Print the facts about a downloaded Bhoonidhi OCM product that motivate this service.

    python scripts/inspect_product.py path/to/E06_OCM_GAC_..._4km.zip
"""
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import xarray as xr


def main(zip_path: str) -> None:
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zip_path) as zf:
        print(f"zip: {Path(zip_path).name}  ({Path(zip_path).stat().st_size / 1e6:.1f} MB)")
        for info in zf.infolist():
            print(f"  member {info.filename.split('/')[-1]:<62} {info.file_size / 1e6:8.1f} MB")
        member = next(n for n in zf.namelist() if n.endswith("BAND.nc"))
        zf.extract(member, tmp)
        with xr.open_dataset(Path(tmp) / member, decode_times=False) as ds:
            tvals = ds["Time"].values
            print(f"\nTime values      : {tvals.tolist()}  units: {ds['Time'].attrs['units']!r}")
            decoded = np.datetime64("1993-01-01") + tvals.astype("timedelta64[D]")
            print(f"  decoded literally -> {decoded.tolist()}   (the product covers a window in 2025)")
            print(f"dims             : {dict(ds.sizes)}   (length-1 Time and Depth dims)")
            print(f"global attrs     : {dict(ds.attrs) or 'none'}")
            print(f"chl attrs        : {dict(ds['chl'].attrs)}   (no standard_name / long_name)")
            enc = ds["chl"].encoding
            print(f"chl packing      : dtype={enc.get('dtype')} scale_factor={enc.get('scale_factor')} "
                  f"_FillValue={enc.get('_FillValue')}")
            lon, lat = ds["Longitude"].values.astype("float64"), ds["Latitude"].values.astype("float64")
            exact = 360 / (len(lon) - 1)
            print(f"grid             : {len(lat)} x {len(lon)}, nominal step {exact:.8f} deg; observed lon steps "
                  f"{np.diff(lon)[:3].round(8).tolist()} (float32 drift), last lon {lon[-1]!r}")
            raw = ds["chl"].values.squeeze()
            print(f"decoded size     : {ds['chl'].size * 4 / 1e6:.0f} MB as float32; no-data share {100 * np.isnan(raw).mean():.1f}%")


if __name__ == "__main__":
    main(sys.argv[1])
