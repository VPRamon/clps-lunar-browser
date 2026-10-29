import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.setup_kernels import KERNELS, kernel_directory


client = TestClient(app)
WINDOW = {"start": "2026-10-15T00:00:00Z", "end": "2026-10-15T01:10:00Z", "step_minutes": 30}
KERNELS_AVAILABLE = all((kernel_directory() / name).is_file() for name in KERNELS)


def test_browser_can_read_api_and_errors():
    headers = {"Origin": "http://localhost:5173"}
    for path in ("/api/sites", "/api/sites/unknown/horizon"):
        response = client.get(path, headers=headers)
        assert response.headers["access-control-allow-origin"] == headers["Origin"]
    preflight = client.options("/api/sites", headers={**headers, "Access-Control-Request-Method": "GET"})
    assert preflight.status_code == 200
    assert client.options("/api/sites", headers={**headers, "Access-Control-Request-Method": "POST"}).status_code == 400
    assert "access-control-allow-origin" not in client.get(
        "/api/sites", headers={"Origin": "https://unconfigured.example"}
    ).headers


def test_horizon_exposes_real_profiles_for_all_named_sites():
    response = client.get("/api/sites/athena-im2/horizon")
    assert response.status_code == 200
    result = response.json()
    assert result["terrain_available"] is True
    profile = result["profile"]
    assert profile["site_id"] == result["site"]
    assert len(profile["horizon"]) == 720
    assert profile["azimuth_step"] == 0.5
    assert profile["pixel_resolution_m"] == 20
    assert profile["source"].endswith("LDEM_80S_20MPP_ADJ.TIF")
    assert profile["max_distance_m"] == 300000
    assert profile["detailed_max_distance_m"] == 40000
    odysseus = client.get("/api/sites/odysseus-im1/horizon").json()
    assert odysseus["terrain_available"] is True
    assert odysseus["profile"]["site_id"] == "odysseus-im1"
    assert odysseus["profile"]["pixel_resolution_m"] == 30
    assert odysseus["profile"]["source"].endswith("LDEM_75S_30MPP_ADJ.TIF")
    assert client.get("/api/sites/unknown/horizon").status_code == 404


def test_horizon_failure_is_not_reported_as_missing_coverage(monkeypatch):
    from backend.terrain.horizon import HorizonError

    def broken_profile(*_):
        raise HorizonError("invalid profile")

    monkeypatch.setattr("backend.app.terrain_profile", broken_profile)
    assert client.get("/api/sites/athena-im2/horizon").status_code == 503


def test_readiness_detects_missing_kernels(monkeypatch, tmp_path):
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503


@pytest.mark.parametrize("params", [
    {"lat": 91}, {"lon": 181}, {"lat": "nan"}, {"step_minutes": 0},
    {"end": WINDOW["start"]}, {"end": "2026-10-14T00:00:00Z"},
    {"end": "2027-01-01T00:00:00Z"}, {"start": "2026-10-15T00:00:00"},
])
def test_coordinate_summary_rejects_invalid_requests(params):
    response = client.get("/api/summary", params={"lat": -84.79, "lon": 29.2, **WINDOW, **params})
    assert response.status_code == 422


@pytest.mark.skipif(not KERNELS_AVAILABLE, reason="SPICE kernels not installed")
def test_real_summary_uses_selected_coordinates_and_distinguishes_terrain():
    assert client.get("/ready").status_code == 200
    coordinate = client.get("/api/summary", params={"lat": -84.79, "lon": 29.2, "terrain": False, **WINDOW})
    terrain = client.get("/api/sites/athena-im2/summary", params=WINDOW)
    assert coordinate.status_code == terrain.status_code == 200
    flat = coordinate.json()
    masked = terrain.json()
    assert flat["site"] is None
    assert flat["latitude"] == -84.79
    assert flat["longitude"] == 29.2
    assert flat["horizon_mode"] == "flat"
    assert masked["horizon_mode"] == "terrain"
    assert flat["sunlight_percent"] == 100
    assert masked["sunlight_percent"] == 0
    assert masked["longest_darkness_minutes"] == 70
    fallback = client.get("/api/sites/odysseus-im1/summary", params=WINDOW)
    assert fallback.json()["horizon_mode"] == "terrain"
    selected = client.get("/api/summary", params={"lat": -84.79, "lon": 29.2, **WINDOW}).json()
    assert selected["sunlight_percent"] == masked["sunlight_percent"]
    assert selected["terrain"] == masked["terrain"]
