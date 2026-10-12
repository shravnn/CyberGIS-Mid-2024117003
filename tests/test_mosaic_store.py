"""The mosaic store is indexed oldest first on every OS (the stack order of overlapping windows follows the index)."""
from pathlib import Path

import pytest

from chlwcs import geoserver

NAMES = [  # created out of order on purpose: the file system's listing order must not matter
    "chl_20250803T000000_20250810T235959.tif",
    "chl_20250622T000000_20250629T235959.tif",
    "chl_20250801T000000_20250808T235959.tif",
    "chl_20250802T000000_20250809T235959.tif",
]


class RecordingRest:
    """Stands in for GeoServer's REST API: records each call and which granules were in the mosaic folder then."""

    def __init__(self, mosaic_dir: Path, fail_on: str | None = None):
        self.mosaic_dir, self.fail_on, self.calls = mosaic_dir, fail_on, []

    def call(self, method, path, data=None, **kw):
        present = sorted(p.name for p in self.mosaic_dir.glob("chl_*.tif"))
        self.calls.append((method, Path(data).name, present))
        if self.fail_on and Path(data).name == self.fail_on:
            raise geoserver.GeoServerError("harvest failed")


@pytest.fixture
def mosaic(tmp_path):
    d = tmp_path / "mosaic"
    d.mkdir()
    for name in NAMES:
        (d / name).write_bytes(b"tif")
    (d / "indexer.properties").write_text("Name=chl\n")
    return d


def test_store_starts_from_the_earliest_window_and_harvests_the_rest_in_time_order(mosaic):
    rest = RecordingRest(mosaic)
    geoserver._create_mosaic_store(rest, mosaic)
    ordered = sorted(NAMES)
    method, target, present = rest.calls[0]
    assert (method, target, present) == ("PUT", "mosaic", [ordered[0]])       # the folder holds only the earliest
    assert [(m, t) for m, t, _ in rest.calls[1:]] == [("POST", n) for n in ordered[1:]]
    assert sorted(p.name for p in mosaic.glob("chl_*.tif")) == ordered
    assert not (mosaic.parent / "mosaic-pending").exists()


def test_a_failed_harvest_puts_every_granule_back(mosaic):
    rest = RecordingRest(mosaic, fail_on=sorted(NAMES)[2])
    with pytest.raises(geoserver.GeoServerError):
        geoserver._create_mosaic_store(rest, mosaic)
    assert sorted(p.name for p in mosaic.glob("chl_*.tif")) == sorted(NAMES)
    assert not (mosaic.parent / "mosaic-pending").exists()
