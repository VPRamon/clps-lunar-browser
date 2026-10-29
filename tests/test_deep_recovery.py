"""Failures, concurrent access, and recovery of the backend's scientific data."""
import copy
import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from backend.app import app
from backend.calculations.sun_earth import calculate_visibility, validate_coordinates
from backend.landing_sites import get_landing_site, load_landing_sites
from backend.kernel_context import KernelError, get_context
from backend.setup_kernels import KERNELS, kernel_directory
from backend.terrain import global_dem as dem_module
from backend.terrain.horizon import HorizonError, load_profile


client = TestClient(app)
TIME = "2026-10-03T00:00:00Z"
REAL_KERNELS = all((kernel_directory()/name).is_file() for name in KERNELS)


@pytest.mark.parametrize("bad", [None, True, "0", 10 ** 1000, float("nan"), float("inf")],
                         ids=["none", "bool", "string", "oversized", "nan", "inf"])
def test_coordinate_helpers_reject_invalid_values_consistently(bad):
    for latitude, longitude in [(bad, 0), (0, bad)]:
        with pytest.raises(ValueError):
            validate_coordinates(latitude, longitude)


@pytest.mark.parametrize("payload", [
    None, b"{broken", b"\xff", b"{}",
    json.dumps([{"id": "test", "name": "Test", "latitude": 10**1000, "longitude": 0,
                 "source": "https://example.com"}]).encode(),
], ids=["missing", "json", "encoding", "schema", "oversized-coordinate"])
def test_invalid_catalog_is_unavailable_and_recovers(monkeypatch, tmp_path, payload):
    import backend.landing_sites as module
    original = load_landing_sites()
    path = tmp_path/"sites.json"
    if payload is not None:
        path.write_bytes(payload)
    monkeypatch.setattr(module, "LANDING_SITES_FILE", path)
    for route in ["/api/sites", "/api/sites/athena-im2/visibility", "/ready"]:
        response = client.get(route, params={"time": TIME})
        assert response.status_code == 503, (route, response.status_code, response.text)
    assert client.get("/health").status_code == 200
    path.write_text(json.dumps(original))
    assert client.get("/api/sites").json() == original


def test_unreadable_named_profile_is_unavailable_not_flat_fallback(monkeypatch, tmp_path):
    import backend.terrain.horizon as module
    monkeypatch.setattr(module, "PROFILE_DIR", tmp_path)
    (tmp_path/"athena-im2.json").write_bytes(b"\xff")
    assert client.get("/api/sites/athena-im2/horizon").status_code == 503
    assert client.get("/api/sites/athena-im2/visibility", params={"time": TIME}).status_code == 503


def test_named_profile_cache_returns_independent_data():
    site = get_landing_site("athena-im2")
    original = copy.deepcopy(load_profile(site))
    changed = load_profile(site)
    changed["horizon"][0] = 80
    changed["site_elevation_m"] = 20000
    assert load_profile(site) == original


def test_global_cache_returns_independent_data_and_serializes_cold_requests(monkeypatch, tmp_path):
    path = tmp_path/"fake.tif"
    path.write_bytes(b"identity")
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    monkeypatch.setattr(dem_module, "_open_dem", lambda *_: np.zeros((4, 8)))
    calls = []
    def build(_grid, lat, lon, **settings):
        calls.append((lat, lon))
        return {"latitude": lat, "longitude": lon, "horizon": [2.]}
    monkeypatch.setattr(dem_module, "build_global_profile", build)
    dem_module._cached_profile.cache_clear()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: dem_module.coordinate_profile(12, 34), range(128)))
    assert calls == [(12, 34)]
    results[0]["horizon"][0] = 80
    assert all(result["horizon"] == [2.] for result in results[1:])
    assert dem_module.coordinate_profile(12, 34)["horizon"] == [2.]
    # Cache capacity stays bounded under many different inputs.
    for lon in range(100):
        dem_module.coordinate_profile(12, lon)
    assert dem_module._cached_profile.cache_info().currsize <= 64


def test_dem_replacement_with_preserved_size_and_mtime_is_revalidated(monkeypatch, tmp_path):
    from backend import setup_terrain
    path = tmp_path/"dem.tif"
    tifffile.imwrite(path, np.full((4, 8), 20000, dtype=np.uint16))
    monkeypatch.setattr(dem_module, "SHAPE", (4, 8))
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    monkeypatch.setattr(setup_terrain, "DEM_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    dem_module.global_dem()
    stat = path.stat()
    replacement = tmp_path/"replacement.tif"
    tifffile.imwrite(replacement, np.full((4, 8), 24000, dtype=np.uint16))
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert replacement.stat().st_size == stat.st_size
    replacement.replace(path)
    with pytest.raises(HorizonError, match="checksum"):
        dem_module.global_dem()


def test_dem_disappearing_during_checksum_is_unavailable(monkeypatch, tmp_path):
    from backend import setup_kernels
    path = tmp_path/"vanishing.tif"
    tifffile.imwrite(path, np.full((4, 8), 20000, dtype=np.uint16))
    monkeypatch.setattr(dem_module, "SHAPE", (4, 8))
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    def interrupted(_path):
        raise FileNotFoundError("dataset replaced during verification")
    monkeypatch.setattr(setup_kernels, "file_hash", interrupted)
    with pytest.raises(HorizonError):
        dem_module.global_dem()


@pytest.mark.skipif(not REAL_KERNELS, reason="real NAIF kernels not installed")
def test_context_reused_across_observations_and_windows():
    from backend.calculations.sun_earth import calculate_visibility_window
    context = get_context()
    original = calculate_visibility(20, 45, TIME)
    samples = calculate_visibility_window(20, 45, TIME, "2026-10-03T02:00:00Z")
    assert get_context() is context
    assert samples[0]["sun"] == original["sun"]
    assert calculate_visibility(20, 45, TIME) == original


@pytest.mark.skipif(not REAL_KERNELS, reason="real NAIF kernels not installed")
def test_changed_but_parseable_kernel_is_rejected_and_repaired_copy_recovers(monkeypatch, tmp_path):
    original_directory = kernel_directory()
    directory = tmp_path/"kernels"
    directory.mkdir()
    for name in KERNELS:
        shutil.copyfile(original_directory/name, directory/name)
    expected = calculate_visibility(0, 0, TIME)
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(directory))
    assert calculate_visibility(0, 0, TIME) == expected
    pck = directory/"pck00011.tpc"
    original = pck.read_bytes()
    replacement = directory/"replacement"
    replacement.write_bytes(original.replace(b"1737.4", b"1738.4"))
    replacement.replace(pck)
    assert pck.read_bytes() != original
    with pytest.raises(KernelError, match="checksum"):
        calculate_visibility(0, 0, TIME)
    replacement.write_bytes(original)
    replacement.replace(pck)
    assert calculate_visibility(0, 0, TIME) == expected


@pytest.mark.skipif(not REAL_KERNELS, reason="real NAIF kernels not installed")
def test_concurrent_ephemeris_requests_match_sequential_references():
    positions = [(lat, lon) for lat in [-90, -45, 0, 45, 90] for lon in [-180, -90, 0, 90, 180]]
    baseline = {point: calculate_visibility(*point, TIME) for point in positions}
    jobs = positions*8
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda point: calculate_visibility(*point, TIME), jobs))
    assert all(result == baseline[point] for point, result in zip(jobs, results))
