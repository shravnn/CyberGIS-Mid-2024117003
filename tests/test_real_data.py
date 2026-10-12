"""Acceptance tests against the real Bhoonidhi data (skipped when it is not on this machine).

Chain under test: original zips -> cube -> GeoTIFF mosaic. GeoServer's output is compared with the same cube by
scripts/check_live.py, so together they tie WCS pixels back to the files Bhoonidhi distributes.
"""
import os
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
import xarray as xr

from chlwcs import geotiff, ingest

ROOT = Path(__file__).resolve().parents[1]
CUBE = ROOT / "data" / "processed" / "ocm_gac_chl_8day.nc"
MOSAIC = ROOT / "data" / "wcs" / "mosaic"
RAW = Path(os.environ.get("CHLWCS_RAW", ROOT / "Datasets"))      # the downloaded zips (kept out of git)

pytestmark = pytest.mark.real
needs_cube = pytest.mark.skipif(not CUBE.exists(), reason="processed cube not built")
needs_raw = pytest.mark.skipif(not (CUBE.exists() and RAW.exists()), reason="raw Bhoonidhi zips not available")

# (name, lon, lat): offshore, coastal upwelling, bay, gulf, land (no data)
POINTS = [("Arabian Sea", 65.0, 15.0), ("off Kerala", 75.5, 10.0), ("off Oman", 58.5, 18.0),
          ("Bay of Bengal", 87.0, 14.0), ("Gulf of Mannar", 79.0, 8.5), ("Hyderabad (land)", 78.5, 17.4)]


@pytest.fixture(scope="module")
def cube():
    with xr.open_dataset(CUBE) as ds:
        yield ds.load()


@needs_cube
def test_cube_time_axis_comes_from_product_names_not_the_bogus_Time_variable(cube):
    t = cube.time.values.astype("datetime64[D]")
    assert str(t[0]) == "2025-06-22" and str(t[-1]) == "2025-09-27"
    assert (np.diff(t) > np.timedelta64(0, "D")).all()                       # strictly increasing, unique
    assert ((cube.time_end.values - cube.time.values).astype("timedelta64[D]") == 8).all()


@needs_cube
def test_cube_grid_is_the_exact_cropped_global_grid(cube):
    assert cube.chl.shape == (cube.sizes["time"], 961, 1441)
    assert cube.lat.values[0] == pytest.approx(-10.0, abs=1e-9) and cube.lat.values[-1] == pytest.approx(30.0, abs=1e-9)
    assert cube.lon.values[0] == pytest.approx(40.0, abs=1e-9) and cube.lon.values[-1] == pytest.approx(100.0, abs=1e-9)
    assert np.allclose(np.diff(cube.lon.values), 360 / 8640, atol=1e-12)    # no float32 drift


@needs_cube
def test_cube_values_are_physical(cube):
    v = cube.chl.values
    assert np.nanmin(v) >= 0.001 and np.nanmax(v) <= 10.0
    assert 0.3 < float(np.isnan(v).mean()) < 0.8                              # land + cloud, not empty and not full


@needs_raw
def test_every_window_matches_its_source_zip_at_sample_points(cube):
    """Independent decode path: open each original zip with plain xarray and compare nearest cells."""
    zips = {ingest.parse_product_id(p.name).product_id: p for p in ingest.find_products(RAW)}
    for i, pid in enumerate(cube.source_product.values):
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(zips[str(pid)]) as zf:
            member = next(n for n in zf.namelist() if n.endswith("BAND.nc"))
            zf.extract(member, tmp)
            with xr.open_dataset(Path(tmp) / member, decode_times=False) as src:
                for name, lon, lat in POINTS:
                    expected = float(src.chl.sel(Latitude=lat, Longitude=lon, method="nearest").values.squeeze())
                    got = float(cube.chl.isel(time=i).sel(lat=lat, lon=lon, method="nearest"))
                    if np.isnan(expected):
                        assert np.isnan(got), (pid, name)
                    else:
                        assert got == pytest.approx(expected, abs=1e-6), (pid, name, got, expected)


@needs_cube
def test_geotiffs_served_by_geoserver_match_the_cube_everywhere(cube, tmp_path):
    """Whole-grid comparison of the GeoTIFF mosaic (rebuilt from the cube) against the cube itself."""
    files = geotiff.write_geotiffs(CUBE, tmp_path)
    assert len(files) == cube.sizes["time"]
    for i, f in enumerate(files):
        with rasterio.open(f) as src:
            data = src.read(1)
        expected = np.flipud(np.where(np.isnan(cube.chl.isel(time=i).values), geotiff.NODATA, cube.chl.isel(time=i).values))
        np.testing.assert_allclose(data, expected, atol=1e-6, err_msg=f.name)
