import tempfile
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from chlwcs import ingest
from tests.helpers import chl_value, make_product, product_id

NAME = "E06_OCM_GAC_01AUG2025_08AUG2025_8_T000000_MGG0CLHND_4km"


def test_parse_product_id_reads_window_from_file_name():
    w = ingest.parse_product_id(NAME + ".zip")
    assert (w.start, w.end, w.end_exclusive) == (date(2025, 8, 1), date(2025, 8, 8), date(2025, 8, 9))
    assert w.product_id == NAME


@pytest.mark.parametrize("bad", [
    "chl.nc",
    "E06_OCM_GAC_xx",
    "E06_OCM_GAC_08AUG2025_01AUG2025_8_T000000_MGG0CLHND_4km",
])
def test_parse_product_id_rejects_unknown_or_reversed_names(bad):
    with pytest.raises(ValueError):
        ingest.parse_product_id(bad)


def test_find_products_sorts_by_window_start_and_ignores_other_zips(tmp_path):
    make_product(tmp_path, date(2025, 8, 9), 1)
    make_product(tmp_path, date(2025, 7, 8), 0)
    (tmp_path / "unrelated.zip").write_bytes(b"x")
    names = [p.name for p in ingest.find_products(tmp_path)]
    assert names == [product_id(date(2025, 7, 8)) + ".zip", product_id(date(2025, 8, 9)) + ".zip"]


def test_read_window_crops_and_keeps_raw_int16(tmp_path):
    z = make_product(tmp_path, date(2025, 7, 8), 0)
    lat, lon, raw = ingest.read_window(z, (40, -10, 100, 30))
    assert raw.dtype == np.int16 and raw.shape == (41, 61)
    assert lat[0] == -10 and lat[-1] == 30 and lon[0] == 40 and lon[-1] == 100
    assert raw[0, 0] == ingest.FILL_VALUE                       # lon 40 < 45 -> no data
    assert raw[0, 5] == round(chl_value(0, -10, 45) * 1000)     # lon 45 -> data


def test_read_window_rejects_non_global_grid(tmp_path):
    z = make_product(tmp_path, date(2025, 7, 8), 0)
    bad = tmp_path / "bad.zip"
    with tempfile.TemporaryDirectory() as t, zipfile.ZipFile(z) as zf:
        zf.extractall(t)
        src = next(Path(t).rglob("BAND.nc"))
        with xr.open_dataset(src) as opened:
            ds = opened.load()
        ds = ds.assign_coords(Longitude=ds.Longitude.values + 0.2)
        shifted = Path(t) / "shifted.nc"
        ds.to_netcdf(shifted)
        with zipfile.ZipFile(bad, "w") as out:
            out.write(shifted, f"{product_id(date(2025, 7, 8))}/BAND.nc")
    with pytest.raises(ValueError, match="regular global grid"):
        ingest.read_window(bad)


def test_build_cube_stacks_sorted_windows_with_real_time_axis(tmp_path):
    zips = [make_product(tmp_path, date(2025, 7, 16), 1), make_product(tmp_path, date(2025, 7, 8), 0)]
    out = ingest.build_cube(zips, tmp_path / "cube.nc")
    with xr.open_dataset(out) as ds:
        assert ds.chl.dims == ("time", "lat", "lon")
        assert [str(t)[:10] for t in ds.time.values] == ["2025-07-08", "2025-07-16"]        # sorted + real dates
        assert [str(t)[:10] for t in ds.time_end.values] == ["2025-07-16", "2025-07-24"]    # end exclusive
        assert ds.chl.attrs["units"] == "mg m-3"
        assert ds.chl.attrs["standard_name"] == "mass_concentration_of_chlorophyll_in_sea_water"
        assert float(ds.chl.sel(time="2025-07-16", lat=0, lon=50)) == pytest.approx(chl_value(1, 0, 50), abs=1e-9)
        assert np.isnan(ds.chl.sel(time="2025-07-08", lat=0, lon=40))                       # fill -> NaN
        assert list(ds.source_product.values) == [product_id(date(2025, 7, 8)), product_id(date(2025, 7, 16))]


def test_build_cube_stores_packed_int16_not_float(tmp_path):
    out = ingest.build_cube([make_product(tmp_path, date(2025, 7, 8), 0)], tmp_path / "c.nc")
    with xr.open_dataset(out, mask_and_scale=False) as raw:
        assert raw.chl.dtype == np.int16 and raw.chl.attrs["scale_factor"] == 0.001
        assert int(raw.chl.attrs["_FillValue"]) == -32768


def test_build_cube_rejects_duplicate_windows_and_empty_input(tmp_path):
    z = make_product(tmp_path, date(2025, 7, 8), 0)
    with pytest.raises(ValueError, match="duplicate"):
        ingest.build_cube([z, z], tmp_path / "c.nc")
    with pytest.raises(ValueError, match="no products"):
        ingest.build_cube([], tmp_path / "c.nc")
