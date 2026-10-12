"""The runner's command line: hosted mode and its validation, and the container image recipe. Nothing is started."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "scripts" / "run.py"


def run(*args):
    return subprocess.run([sys.executable, str(RUN), *args], capture_output=True, text=True, timeout=120)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_script", RUN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tunnel_and_fixed_public_url_are_mutually_exclusive():
    r = run("--public", "--public-url", "https://example.org")
    assert r.returncode == 2 and "not allowed with" in r.stderr


def test_hosted_mode_cannot_open_a_tunnel():
    r = run("--hosted", "--public")
    assert r.returncode == 2 and "cannot be combined with --public" in r.stderr


@pytest.mark.parametrize("bad", ["not-a-url", "https://example.org/path", "ftp://example.org"])
def test_public_url_must_be_a_bare_http_origin(bad):
    r = run("--public-url", bad)
    assert r.returncode == 2 and "--public-url must look like" in r.stderr


def test_platform_url_prefers_an_explicit_public_url(runner):
    env = {"PUBLIC_URL": "https://wcs.example.org", "CONTAINER_APP_NAME": "app",
           "CONTAINER_APP_ENV_DNS_SUFFIX": "x.centralindia.azurecontainerapps.io"}
    assert runner.platform_url(env) == "https://wcs.example.org"


def test_platform_url_from_azure_container_apps_variables(runner):
    env = {"CONTAINER_APP_NAME": "chlorophyll-wcs", "CONTAINER_APP_ENV_DNS_SUFFIX": "x.centralindia.azurecontainerapps.io"}
    assert runner.platform_url(env) == "https://chlorophyll-wcs.x.centralindia.azurecontainerapps.io"


def test_platform_url_is_none_outside_a_platform(runner):
    assert runner.platform_url({}) is None
    assert runner.platform_url({"CONTAINER_APP_NAME": "app"}) is None


def test_container_image_runs_the_hosted_mode_as_a_non_root_user():
    text = (ROOT / "Dockerfile").read_text(encoding="utf8")
    assert 'CMD ["python", "scripts/run.py", "--hosted"]' in text
    assert f"EXPOSE {8000}" in text and "USER app" in text
    # GeoServer is configured while the image is built, so a cold start only boots it
    assert "scripts/geoserver.py start" in text and "scripts/prep_data.py" in text
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf8").split()
    assert {".venv", "Datasets", "data/geoserver", "data/wcs"} <= set(ignored)
