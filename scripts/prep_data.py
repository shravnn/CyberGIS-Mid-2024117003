"""Build the data GeoServer serves, from the downloaded Bhoonidhi zips.

    python scripts/prep_data.py --raw path/to/Datasets

zips -> data/processed/ocm_gac_chl_8day.nc (one CF cube)  -> data/wcs/mosaic/chl_*.tif + indexer files
If the cube already exists and --raw is not given, only the GeoTIFF mosaic is rebuilt from it.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chlwcs import geotiff, ingest  # noqa: E402  (importing chlwcs fixes PROJ before rasterio loads)

CUBE = ROOT / "data" / "processed" / "ocm_gac_chl_8day.nc"
MOSAIC = ROOT / "data" / "wcs" / "mosaic"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, help="folder with the EOS-06 OCM GAC chlorophyll zips")
    ap.add_argument("--cube", type=Path, default=CUBE)
    ap.add_argument("--mosaic", type=Path, default=MOSAIC)
    args = ap.parse_args()

    if args.raw:
        zips = ingest.find_products(args.raw)
        if not zips:
            raise SystemExit(f"no EOS-06 OCM GAC chlorophyll zips found in {args.raw}")
        print(f"{len(zips)} products found in {args.raw}")
        ingest.build_cube(zips, args.cube)
    elif not args.cube.exists():
        raise SystemExit(f"{args.cube} does not exist; pass --raw <folder with the zips>")

    files = geotiff.write_geotiffs(args.cube, args.mosaic)
    geotiff.write_mosaic_config(args.mosaic)

    with xr.open_dataset(args.cube) as ds:
        print(f"\n{'start':<12}{'end (excl.)':<14}{'valid %':>8}  file")
        for i, f in enumerate(files):
            valid = 100 * float(np.isfinite(ds.chl.isel(time=i)).mean())
            print(f"{str(ds.time.values[i])[:10]:<12}{str(ds.time_end.values[i])[:10]:<14}{valid:>8.1f}  {f.name}")
        print(f"\ngrid {ds.sizes['lat']} x {ds.sizes['lon']}")
    total = sum(f.stat().st_size for f in files) / 1e6
    print(f"cube {args.cube.stat().st_size / 1e6:.1f} MB; {len(files)} GeoTIFFs, {total:.1f} MB -> {args.mosaic}")


if __name__ == "__main__":
    main()
