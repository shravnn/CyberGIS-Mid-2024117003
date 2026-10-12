"""The runner's command line: hosted mode (--public-url) and its validation. Nothing is started by these tests."""
import subprocess
import sys
from pathlib import Path

import pytest

RUN = Path(__file__).resolve().parents[1] / "scripts" / "run.py"
DOCKERFILE = Path(__file__).resolve().parents[1] / "deploy" / "huggingface" / "Dockerfile"


def run(*args):
    return subprocess.run([sys.executable, str(RUN), *args], capture_output=True, text=True, timeout=120)


def test_tunnel_and_fixed_public_url_are_mutually_exclusive():
    r = run("--public", "--public-url", "https://example.hf.space")
    assert r.returncode == 2 and "not allowed with" in r.stderr


@pytest.mark.parametrize("bad", ["not-a-url", "https://example.hf.space/path", "ftp://example.org"])
def test_public_url_must_be_a_bare_http_origin(bad):
    r = run("--public-url", bad)
    assert r.returncode == 2 and "--public-url must look like" in r.stderr


def test_container_command_uses_the_hosted_mode_on_the_platform_port():
    text = DOCKERFILE.read_text(encoding="utf8")
    assert 'scripts/run.py --host 0.0.0.0 --port 7860 --public-url "https://${SPACE_HOST' in text
    assert "EXPOSE 7860" in text and "USER user" in text
    readme = (DOCKERFILE.parent / "README.md").read_text(encoding="utf8")
    assert "sdk: docker" in readme and "app_port: 7860" in readme
