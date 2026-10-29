"""Scientific geometry and API coverage without a network/data prerequisite."""
import copy
import math

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from backend.app import app
from backend.terrain.global_dem import RADIUS_M, build_global_profile, sample_height
from backend.terrain.horizon import HorizonError, horizon_at

client = TestClient(app)
TIME = "2026-10-15T00:00:00Z"
WINDOW = {"start": TIME, "end": "2026-10-15T01:00:00Z", "step_minutes": 30}


@pytest.fixture
def globe():
    # Same encoding/projection as NASA's DEM at a smaller size, ~3 km/pixel.
    return np.full((1800, 3600), 20000, dtype=np.uint16)


@pytest.mark.parametrize("latitude,longitude", [(0, 0), (0, 180), (0, -180), (90, 37), (-90, -120), (89.7, 179.9), (-80.13, 1.44)])
def test_curved_flat_globe_covers_seams_and_poles(globe, latitude, longitude):
    profile = build_global_profile(globe, latitude, longitude)
    assert len(profile["horizon"]) == 720
    assert max(profile["horizon"]) < 0
    assert min(profile["horizon"]) > -0.3
    assert profile["site_elevation_m"] == 0
    assert profile["observer_height_m"] == 2
    assert all(profile["min_distance_m"] <= d <= 40000 for d in profile["peak_distance_m"])


def test_east_ridge_blocks_sun_across_longitude_seam(globe):
    # Observer is west of the seam; east ridge is on the opposite image edge.
    lat, lon = 0.05, 179.9
    ridge_lon = ((lon + math.degrees(15000 / RADIUS_M) + 180) % 360) - 180
    row = int((90 - lat) * globe.shape[0] / 180)
    col = int((ridge_lon + 180) * globe.shape[1] / 360)
    globe[row-1:row+2, col-1:col+2] = 24000  # 2 km ridge
    profile = build_global_profile(globe, lat, lon)
    assert horizon_at(profile, 90) > 6
    assert horizon_at(profile, 270) < 0
    raised = build_global_profile(globe, lat, lon, observer_height_m=100)
    assert horizon_at(raised, 90) < horizon_at(profile, 90)


def test_height_encoding_and_bilinear_longitude_wrap():
    grid = np.full((180, 360), 22000, dtype=np.uint16)
    assert sample_height(grid, 20, 20) == 1000
    grid[:, 0], grid[:, -1] = 20000, 24000
    assert sample_height(grid, 0, 180) == sample_height(grid, 0, -180) == 1000
    assert sample_height(grid, 90, 180) == 1000


def test_missing_cells_do_not_become_a_flat_horizon(globe):
    globe[895, 1800] = 0
    with pytest.raises(HorizonError, match="missing pixels"):
        build_global_profile(globe, 0, 0)
    globe[900, 1800] = 65535
    with pytest.raises(HorizonError, match="observer"):
        build_global_profile(globe, 0, 0)


def test_global_loader_rejects_bad_or_missing_data(monkeypatch, tmp_path):
    from backend.terrain.global_dem import global_dem
    path = tmp_path / "bad.tif"
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    with pytest.raises(HorizonError, match="setup_terrain"):
        global_dem()
    tifffile.imwrite(path, np.zeros((4, 8), dtype=np.uint16))
    with pytest.raises(HorizonError, match="11520"):
        global_dem()


def test_cache_separates_locations_and_invalidates_replaced_dataset(monkeypatch, tmp_path):
    from backend.terrain import global_dem as module
    path = tmp_path / "global.tif"
    path.write_bytes(b"a")
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    calls = []
    monkeypatch.setattr(module, "_open_dem", lambda *_: np.full((1800, 3600), 20000, dtype=np.uint16))
    def make(_grid, lat, lon, **settings):
        calls.append((lat, lon))
        return {"latitude": lat, "longitude": lon}
    monkeypatch.setattr(module, "build_global_profile", make)
    module._cached_profile.cache_clear()
    module.coordinate_profile(10, 30)
    module.coordinate_profile(10, 30)
    module.coordinate_profile(10, 31)
    path.write_bytes(b"bb")
    module.coordinate_profile(10, 30)
    assert calls == [(10, 30), (10, 31), (10, 30)]


@pytest.fixture
def fake_terrain(monkeypatch, globe):
    template = build_global_profile(globe, 0, 0)
    calls = []
    def profile(lat, lon, site=None):
        calls.append((lat, lon))
        return {**copy.deepcopy(template), "latitude": lat, "longitude": lon,
                "site_id": site["id"] if site else "coordinates", "horizon": [5] * 720,
                "site_elevation_m": 1000}
    monkeypatch.setattr("backend.app.terrain_profile", profile)
    return calls


def test_all_routes_use_same_terrain_observer_and_summary(fake_terrain, monkeypatch):
    def visibility(lat, lon, time, radius=None):
        assert radius == RADIUS_M + 1002
        return {"latitude": lat, "longitude": lon, "time": time,
                "sun": {"azimuth": 90, "elevation": 3, "visible": True},
                "earth": {"azimuth": 180, "elevation": 8, "visible": True}}
    def window(lat, lon, start, end, step, radius=None):
        result = visibility(lat, lon, start, radius)
        return [{**copy.deepcopy({key: result[key] for key in ("time", "sun", "earth")}), "time": time}
                for time in (start, "2026-10-15T00:30:00Z", end)]
    monkeypatch.setattr("backend.app.calculate_visibility", visibility)
    monkeypatch.setattr("backend.app.calculate_visibility_window", window)
    for base, position in [("/api", {"lat": 12, "lon": 179.9}), ("/api/sites/odysseus-im1", {})]:
        one = client.get(base + "/visibility", params={**position, "time": TIME}).json()
        samples = client.get(base + "/visibility/window", params={**position, **WINDOW}).json()
        summary = client.get(base + "/summary", params={**position, **WINDOW}).json()
        horizon = client.get(base + "/horizon", params=position).json()
        assert one["horizon_mode"] == summary["horizon_mode"] == "terrain"
        assert one["sun"]["flat_visible"] is True
        assert one["sun"]["visible"] is False
        assert samples[0]["sun"] == one["sun"]
        assert summary["sunlight_percent"] == 0
        assert summary["earth_visible_percent"] == 100
        assert horizon["terrain_available"] is True
    assert (12, 179.9) in fake_terrain
    assert (-80.13, 1.44) in fake_terrain


def test_missing_global_data_is_503_and_explicit_flat_mode_works(monkeypatch, tmp_path):
    monkeypatch.setenv("LUNAR_DEM_PATH", str(tmp_path / "missing.tif"))
    for path, params in [
        ("/api/horizon", {"lat": 1, "lon": 2}),
        ("/api/visibility", {"lat": 1, "lon": 2, "time": TIME}),
        ("/api/summary", {"lat": 1, "lon": 2, **WINDOW}),
        ("/api/sites/odysseus-im1/horizon", {}),
        ("/api/sites/odysseus-im1/summary", WINDOW),
        ("/ready", {}),
    ]:
        assert client.get(path, params=params).status_code == 503
    def fake(lat, lon, time, radius=None):
        assert radius is None
        return {"site": None, "latitude": lat, "longitude": lon, "time": time,
                "sun": {"azimuth": 0, "elevation": 1, "visible": True},
                "earth": {"azimuth": 0, "elevation": -1, "visible": False}}
    monkeypatch.setattr("backend.app.calculate_visibility", fake)
    response = client.get("/api/visibility", params={"lat": 1, "lon": 2, "time": TIME, "terrain": False})
    assert response.status_code == 200
    assert response.json()["horizon_mode"] == "flat"
    assert response.json()["terrain"] is None
    assert "terrain_horizon" not in response.json()["sun"]


def test_invalid_request_precedes_missing_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LUNAR_DEM_PATH", str(tmp_path / "missing"))
    assert client.get("/api/visibility", params={"lat": 1, "lon": 2, "time": "bad"}).status_code == 422
    assert client.get("/api/visibility/window", params={"lat": 1, "lon": 2, **WINDOW, "end": "2027-01-01T00:00:00Z"}).status_code == 422


@pytest.mark.parametrize("step", [0, -1, float("nan"), 7])
def test_invalid_azimuth_grid_is_rejected(globe, step):
    with pytest.raises(ValueError, match="divide 360"):
        build_global_profile(globe, 0, 0, azimuth_step=step)


def test_correct_shape_with_corrupt_content_fails_checksum(monkeypatch, tmp_path):
    from backend.terrain import global_dem as module
    path = tmp_path / "corrupt.tif"
    tifffile.imwrite(path, np.full((4, 8), 20000, dtype=np.uint16))
    monkeypatch.setattr(module, "SHAPE", (4, 8))
    monkeypatch.setenv("LUNAR_DEM_PATH", str(path))
    with pytest.raises(HorizonError, match="checksum"):
        module.global_dem()


from backend.setup_kernels import KERNELS, kernel_directory
from backend.terrain.global_dem import dem_path

REAL_DATA = dem_path().is_file() and all((kernel_directory() / name).is_file() for name in KERNELS)


@pytest.mark.skipif(not REAL_DATA, reason="real SPICE kernels and global DEM not installed")
@pytest.mark.parametrize("lat,lon", [(0, 0), (30, -75), (-30, 60), (0, 180), (90, 0), (-90, 135)])
def test_real_global_locations_have_consistent_horizon_and_visibility(lat, lon):
    coords = {"lat": lat, "lon": lon}
    response = client.get("/api/horizon", params=coords)
    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["pixels_per_degree"] == 64
    assert profile["latitude"] == lat and profile["longitude"] == lon
    one = client.get("/api/visibility", params={**coords, "time": TIME})
    assert one.status_code == 200
    result = one.json()
    assert result["horizon_mode"] == "terrain"
    for name in ("sun", "earth"):
        body = result[name]
        boundary = horizon_at(profile, body["azimuth"])
        assert body["terrain_horizon"] == boundary
        assert body["visible"] == (body["elevation"] > boundary)
    timeline = client.get("/api/visibility/window", params={**coords, **WINDOW})
    assert timeline.status_code == 200
    assert timeline.json()[0]["sun"] == result["sun"]
    summary = client.get("/api/summary", params={**coords, **WINDOW})
    assert summary.status_code == 200
    assert summary.json()["horizon_mode"] == "terrain"


def test_pole_height_is_unique_and_polar_cap_is_continuous():
    grid = np.full((180, 360), 20000, dtype=np.uint16)
    grid[0] = np.arange(360, dtype=np.uint16) + 20000
    grid[-1] = np.arange(360, dtype=np.uint16) + 21000
    for lat, row in [(90, 0), (-90, -1)]:
        expected = grid[row].mean() * 0.5 - 10000
        for lon in [-180, -45, 0, 90, 180]:
            assert sample_height(grid, lat, lon) == expected
            near = sample_height(grid, math.copysign(90 - 1e-8, lat), lon)
            assert near == pytest.approx(expected, abs=0.00001)
