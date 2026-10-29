from contextlib import contextmanager
import hashlib
from io import BytesIO
from urllib.error import URLError

import pytest

from backend.setup_kernels import KERNELS, setup_kernels


def test_manifest_contains_the_documented_kernel_set():
    assert set(KERNELS) == {
        "naif0012.tls", "de440s.bsp", "pck00011.tpc",
        "moon_pa_de440_200625.bpc", "moon_de440_220930.tf",
    }


def test_offline_verification_detects_corrupt_kernel(monkeypatch, tmp_path):
    good = b"expected kernel"
    monkeypatch.setattr("backend.setup_kernels.KERNELS", {
        "example.tls": ("example.tls", hashlib.sha256(good).hexdigest()),
    })
    target = tmp_path / "example.tls"
    target.write_bytes(good)
    setup_kernels(tmp_path, check_only=True)
    target.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="incorrect kernel"):
        setup_kernels(tmp_path, check_only=True)
    assert target.read_bytes() == b"corrupt"


def test_failed_download_preserves_existing_file_and_cleans_temporary(monkeypatch, tmp_path):
    monkeypatch.setattr("backend.setup_kernels.KERNELS", {
        "example.tls": ("example.tls", hashlib.sha256(b"expected").hexdigest()),
    })
    target = tmp_path / "example.tls"
    target.write_bytes(b"previous version")

    @contextmanager
    def open_url(*args, **kwargs):
        yield BytesIO(b"truncated download")

    monkeypatch.setattr("backend.setup_kernels.urlopen", open_url)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        setup_kernels(tmp_path)
    assert target.read_bytes() == b"previous version"
    assert list(tmp_path.iterdir()) == [target]


def test_transient_download_failure_is_retried(monkeypatch, tmp_path):
    good = b"expected kernel"
    monkeypatch.setattr("backend.setup_kernels.KERNELS", {
        "example.tls": ("example.tls", hashlib.sha256(good).hexdigest()),
    })
    calls = []

    @contextmanager
    def open_url(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise URLError("connection interrupted")
        yield BytesIO(good)

    monkeypatch.setattr("backend.setup_kernels.urlopen", open_url)
    setup_kernels(tmp_path)
    assert len(calls) == 2
    assert (tmp_path / "example.tls").read_bytes() == good
    assert not list(tmp_path.glob("*.part"))
