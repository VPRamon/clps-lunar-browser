import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.calculations.sun_earth import (
    ObservationError,
    calculate_visibility,
    parse_utc_time,
    validate_coordinates,
)
from backend.setup_kernels import KERNELS, kernel_directory


client = TestClient(app)


@pytest.mark.parametrize("time", [
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
])
@pytest.mark.parametrize("route", ["visibility", "visibility/window", "summary"])
def test_utc_conversion_overflow_returns_validation_error(route, time):
    response = client.get("/api/" + route, params={
        "lat": 0, "lon": 0, "terrain": False,
        "time": time, "start": time, "end": "2026-10-03T01:00:00Z",
    })
    assert response.status_code == 422
    assert "timestamp" in response.json()["detail"]


def test_summary_with_step_larger_than_datetime_range():
    params = {
        "lat": 0, "lon": 0, "terrain": False,
        "start": "2026-10-03T00:00:00Z", "end": "2026-10-03T01:00:00Z",
        "step_minutes": 100_000_000_000,
    }
    response = client.get("/api/summary", params=params)
    assert response.status_code == 200
    result = response.json()
    single = client.get("/api/visibility", params={**params, "time": params["start"]}).json()
    assert result["sunlight_percent"] == (100 if single["sun"]["visible"] else 0)
    assert result["earth_visible_percent"] == (100 if single["earth"]["visible"] else 0)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_visibility_response_shape(monkeypatch):
    expected = {
        "site": None,
        "latitude": -89.5,
        "longitude": 135.0,
        "time": "2026-10-03T18:00:00Z",
        "sun": {"azimuth": 124.2, "elevation": 3.7, "visible": True},
        "earth": {"azimuth": 241.8, "elevation": 7.1, "visible": True},
    }
    monkeypatch.setattr("backend.app.calculate_visibility", lambda *_: expected)

    response = client.get(
        "/api/visibility?terrain=false&lat=-89.5&lon=135&time=2026-10-03T18:00:00Z"
    )
    assert response.status_code == 200
    assert response.json() == expected


def test_api_rejects_out_of_range_coordinates():
    response = client.get(
        "/api/visibility?terrain=false&lat=91&lon=0&time=2026-10-03T18:00:00Z"
    )
    assert response.status_code == 422


def test_api_rejects_invalid_timestamp():
    response = client.get("/api/visibility?terrain=false&lat=0&lon=0&time=not-a-time")
    assert response.status_code == 422
    assert "ISO 8601" in response.json()["detail"]


def test_missing_kernels_return_service_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))

    response = client.get("/api/visibility?terrain=false&lat=0&lon=0&time=2026-10-03T18:00:00Z")
    assert response.status_code == 503
    assert "naif0012.tls" in response.json()["detail"]


def test_input_helpers():
    assert validate_coordinates(-89.5, 135) == (-89.5, 135)
    assert parse_utc_time("2026-10-03T18:00:00Z").isoformat() == (
        "2026-10-03T18:00:00+00:00"
    )
    with pytest.raises(ValueError, match="timezone"):
        parse_utc_time("2026-10-03T18:00:00")
    with pytest.raises(ValueError, match="lat"):
        validate_coordinates(91, 0)


def test_observation_error_returns_bad_gateway(monkeypatch):
    def fail_calculation(*_):
        raise ObservationError("Observation failed")

    monkeypatch.setattr("backend.app.calculate_visibility", fail_calculation)
    response = client.get("/api/visibility?terrain=false&lat=0&lon=0&time=2026-10-03T18:00:00Z")

    assert response.status_code == 502
    assert response.json()["detail"] == "Observation failed"


def test_visibility_window_response_shape(monkeypatch):
    expected = [
        {
            "time": "2026-10-03T18:00:00Z",
            "sun": {"azimuth": 134.3, "elevation": 0.7, "visible": True},
            "earth": {"azimuth": 226.8, "elevation": 4.2, "visible": True},
        }
    ]
    monkeypatch.setattr("backend.app.calculate_visibility_window", lambda *_: expected)

    response = client.get(
        "/api/visibility/window?terrain=false&lat=-89.5&lon=135&start=2026-10-03T18:00:00Z"
        "&end=2026-10-03T18:00:00Z&step_minutes=30"
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_visibility_window_rejects_invalid_step():
    response = client.get(
        "/api/visibility/window?terrain=false&lat=0&lon=0&start=2026-10-03T00:00:00Z"
        "&end=2026-10-03T01:00:00Z&step_minutes=0"
    )

    assert response.status_code == 422


def test_sites_endpoint_lists_landing_sites():
    response = client.get("/api/sites")

    assert response.status_code == 200
    sites = response.json()
    assert [site["id"] for site in sites] == ["odysseus-im1", "athena-im2"]
    assert sites[0]["latitude"] == -80.13
    assert sites[0]["longitude"] == 1.44


def test_named_site_visibility_uses_site_coordinates(monkeypatch):
    expected = {
        "site": None,
        "latitude": -84.79,
        "longitude": 29.2,
        "time": "2026-10-03T18:00:00Z",
        "sun": {"azimuth": 140.0, "elevation": 1.0, "visible": True},
        "earth": {"azimuth": 220.0, "elevation": 5.0, "visible": True},
    }
    calls = []

    def fake_calculation(latitude, longitude, time, radius=None):
        calls.append((latitude, longitude, time))
        return expected.copy()

    monkeypatch.setattr("backend.app.calculate_visibility", fake_calculation)
    response = client.get(
        "/api/sites/athena-im2/visibility?time=2026-10-03T18:00:00Z"
    )

    assert response.status_code == 200
    assert calls == [(-84.79, 29.2, "2026-10-03T18:00:00Z")]
    assert response.json()["site"] == "athena-im2"


def test_named_site_visibility_returns_404_for_unknown_site():
    response = client.get(
        "/api/sites/no-such-site/visibility?time=2026-10-03T18:00:00Z"
    )

    assert response.status_code == 404


KERNELS_AVAILABLE = all((kernel_directory() / name).is_file() for name in KERNELS)


@pytest.mark.skipif(not KERNELS_AVAILABLE, reason="SPICE kernels not installed")
def test_all_named_sites_use_terrain_at_the_dem_observer():
    path = "/api/sites/athena-im2/visibility?time=2026-10-03T18:00:00Z"
    response = client.get(path)
    assert response.status_code == 200
    result = response.json()
    metadata = result["terrain"]
    radius = metadata["datum_radius_m"] + metadata["site_elevation_m"] + metadata["observer_height_m"]
    original = calculate_visibility(-84.79, 29.2, "2026-10-03T18:00:00Z", radius)
    for name in ("sun", "earth"):
        assert result[name]["azimuth"] == original[name]["azimuth"]
        assert result[name]["elevation"] == original[name]["elevation"]
        assert result[name]["flat_visible"] == original[name]["visible"]
        assert result[name]["visible"] is (
            result[name]["elevation"] > result[name]["terrain_horizon"]
        )

    fallback = client.get(
        "/api/sites/odysseus-im1/visibility?time=2026-10-03T18:00:00Z"
    ).json()
    assert fallback["horizon_mode"] == "terrain"
    assert "terrain_horizon" in fallback["sun"]

    blocked = client.get(
        "/api/sites/athena-im2/visibility?time=2026-10-15T00:00:00Z"
    ).json()["sun"]
    assert blocked["elevation"] > 0
    assert blocked["flat_visible"] is True
    assert blocked["visible"] is False


@pytest.mark.skipif(not KERNELS_AVAILABLE, reason="SPICE kernels not installed")
def test_named_site_window_matches_single_time_with_terrain():
    query = "start=2026-10-03T18:00:00Z&end=2026-10-03T19:00:00Z&step_minutes=30"
    response = client.get("/api/sites/athena-im2/visibility/window?" + query)
    assert response.status_code == 200
    samples = response.json()
    assert len(samples) == 3
    for sample in samples:
        single = client.get(
            "/api/sites/athena-im2/visibility?time=" + sample["time"]
        ).json()
        assert sample["sun"] == single["sun"]
        assert sample["earth"] == single["earth"]

    fallback = client.get("/api/sites/odysseus-im1/visibility/window?" + query).json()
    first = client.get(
        "/api/sites/odysseus-im1/visibility?time=" + fallback[0]["time"]
    ).json()
    assert fallback[0]["sun"] == first["sun"]
    assert "terrain_horizon" in fallback[0]["sun"]

    summary = client.get("/api/sites/athena-im2/summary?" + query)
    assert summary.status_code == 200
    assert summary.json()["site"] == "athena-im2"
    assert 0 <= summary.json()["sunlight_percent"] <= 100


def test_window_request_limits_and_summary_validation():
    oversized = (
        "start=2026-10-03T00:00:00Z&end=2026-10-04T09:20:00Z&step_minutes=1"
    )
    assert client.get("/api/visibility/window?terrain=false&lat=0&lon=0&" + oversized).status_code == 422
    assert client.get("/api/sites/athena-im2/visibility/window?" + oversized).status_code == 422
    assert client.get("/api/sites/athena-im2/summary?" + oversized).status_code == 422
    assert client.get(
        "/api/sites/athena-im2/summary?start=2026-10-03T00:00:00Z&end=2026-10-03T00:00:00Z"
    ).status_code == 422
    assert client.get(
        "/api/sites/athena-im2/visibility/window?"
        "start=2026-10-04T00:00:00Z&end=2026-10-03T00:00:00Z"
    ).status_code == 422
    assert client.get(
        "/api/sites/athena-im2/visibility/window?"
        "start=2026-10-03T00:00:00Z&end=2026-10-04T00:00:00Z&step_minutes=0"
    ).status_code == 422
