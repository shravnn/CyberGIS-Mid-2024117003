from datetime import date
from pathlib import Path

import pytest

from chlwcs import geoserver, geotiff, ingest
from tests.helpers import GS_PORT, make_product

WINDOWS = [date(2025, 7, 8), date(2025, 7, 16), date(2025, 7, 24), date(2025, 8, 9)]  # last one after a gap


@pytest.fixture(scope="session")
def cube_path(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("raw")
    zips = [make_product(d, s, k) for k, s in enumerate(WINDOWS)]
    return ingest.build_cube(zips, d / "cube.nc")


@pytest.fixture(scope="session")
def service(tmp_path_factory):
    """A real GeoServer on fabricated data (known pixel values), configured by the production code path."""
    try:
        home = geoserver.find_home()
    except geoserver.GeoServerError as err:
        pytest.skip(str(err))
    root = tmp_path_factory.mktemp("wcs")
    zips = [make_product(root / "raw", s, k) for k, s in enumerate(WINDOWS)]
    cube = ingest.build_cube(zips, root / "cube.nc")
    mosaic = root / "mosaic"
    geotiff.write_geotiffs(cube, mosaic)
    geotiff.write_mosaic_config(mosaic)
    data_dir = root / "gs" / "data_dir"
    proc = geoserver.start(data_dir, GS_PORT, home=home)
    try:
        geoserver.wait_ready(GS_PORT, proc=proc)
        geoserver.configure(mosaic, data_dir, GS_PORT)
        yield data_dir
    finally:
        geoserver.stop(data_dir, GS_PORT, proc)
