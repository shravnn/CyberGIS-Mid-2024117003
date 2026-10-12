import re
from datetime import date

import numpy as np
import pytest
import rasterio
import xarray as xr

from chlwcs import geotiff
from tests.helpers import chl_value

STEP = 1.0  # fabricated cube is on a 1-degree grid


def test_window_file_name_carries_start_and_last_second_of_the_inclusive_end():
    name = geotiff.window_file_name(np.datetime64("2025-07-08"), np.datetime64("2025-07-16"))
    assert name == "chl_20250708T000000_20250715T235959.tif"      # end is exclusive in the cube, inclusive in the name


def test_write_geotiffs_one_file_per_window(cube_path, tmp_path):
    files = geotiff.write_geotiffs(cube_path, tmp_path)
    assert [f.name for f in files] == [
        "chl_20250708T000000_20250715T235959.tif", "chl_20250716T000000_20250723T235959.tif",
        "chl_20250724T000000_20250731T235959.tif", "chl_20250809T000000_20250816T235959.tif"]


def test_geotiff_is_georeferenced_north_up_cell_centres(cube_path, tmp_path):
    f = geotiff.write_geotiffs(cube_path, tmp_path)[0]
    with rasterio.open(f) as src:
        assert src.crs.to_epsg() == 4326
        assert (src.width, src.height) == (61, 41)
        # cube nodes are cell centres: lon 40..100, lat -10..30 -> outer edges are half a cell further out
        assert src.bounds.left == pytest.approx(39.5) and src.bounds.right == pytest.approx(100.5)
        assert src.bounds.bottom == pytest.approx(-10.5) and src.bounds.top == pytest.approx(30.5)
        assert src.transform.e < 0, "GeoTIFF rows must run north to south"
        assert src.dtypes[0] == "float32" and src.nodata == geotiff.NODATA


@pytest.mark.parametrize("lon,lat", [(50, 0), (45, -10), (100, 30), (60.0, 12.0)])
def test_pixel_at_lonlat_holds_the_cube_value(cube_path, tmp_path, lon, lat):
    files = geotiff.write_geotiffs(cube_path, tmp_path)
    with rasterio.open(files[2]) as src:                       # third window, k = 2
        value = next(src.sample([(lon, lat)]))[0]
    assert value == pytest.approx(chl_value(2, lat, lon), abs=1e-6)


def test_nodata_cells_use_the_declared_nodata_value(cube_path, tmp_path):
    f = geotiff.write_geotiffs(cube_path, tmp_path)[0]
    with rasterio.open(f) as src:
        assert next(src.sample([(40, 0)]))[0] == geotiff.NODATA        # lon 40 < 45 is no-data in the fabricated data
        assert not np.isnan(src.read(1)).any(), "NaN must never be written, only the nodata value"


def test_geotiff_is_tiled_compressed_and_describes_itself(cube_path, tmp_path):
    f = geotiff.write_geotiffs(cube_path, tmp_path)[1]
    with rasterio.open(f) as src:
        assert src.is_tiled and src.compression.name.lower() == "deflate"
        assert src.units[0] == "mg m-3" and src.descriptions[0] == "chlorophyll_a"
        tags = src.tags()
        assert tags["window_start"] == "2025-07-16T00:00:00Z" and tags["window_end_exclusive"] == "2025-07-24T00:00:00Z"
        assert tags["source_product"].startswith("E06_OCM_GAC_16JUL2025")


def test_mosaic_config_files_extract_start_and_end_from_names(tmp_path):
    geotiff.write_mosaic_config(tmp_path)
    indexer = (tmp_path / "indexer.properties").read_text()
    assert "TimeAttribute=time;endtime" in indexer and "Name=chl" in indexer
    assert "TimestampFileNameExtractorSPI[timeregex](time)" in indexer
    assert "TimestampFileNameExtractorSPI[timeregexend](endtime)" in indexer
    stem = "chl_20250708T000000_20250715T235959"            # GeoServer matches against the name without ".tif"
    for fname, expected in [("timeregex.properties", "20250708T000000"), ("timeregexend.properties", "20250715T235959")]:
        regex, fmt = (tmp_path / fname).read_text().strip().split(",format=")
        assert regex.startswith("regex=") and fmt == "yyyyMMdd'T'HHmmss"
        assert re.search(regex.removeprefix("regex="), stem).group(0) == expected


def test_write_geotiffs_refuses_to_leave_stale_files(cube_path, tmp_path):
    (tmp_path / "chl_20240101T000000_20240108T235959.tif").write_bytes(b"old")
    geotiff.write_geotiffs(cube_path, tmp_path)
    assert not (tmp_path / "chl_20240101T000000_20240108T235959.tif").exists()
