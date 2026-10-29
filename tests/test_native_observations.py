"""Native ownership, verified resources, time boundaries and actual API requests."""
import gc
import importlib.metadata
import importlib.util
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from siderust import LunarObserver
from tempoch import JulianDate

from backend.app import app
from backend.calculations.sun_earth import calculate_visibility, parse_utc_time
from backend.spice_kernels import KERNEL_FILES, KernelError, kernel_directory, spice_context

TIME = "2026-10-03T18:00:00Z"
REAL_KERNELS = all((kernel_directory() / name).is_file() for name in KERNEL_FILES)
real_kernels = pytest.mark.skipif(not REAL_KERNELS, reason="real NAIF kernels not installed")


def test_published_dependencies_without_legacy_engine():
    assert importlib.metadata.version("siderust") == "0.8.0"
    assert importlib.metadata.version("tempoch") == "0.5.0"
    assert importlib.util.find_spec("spiceypy") is None


@pytest.mark.parametrize("time", [
    "2026-10-03T24:00:00Z", "20261003T240000Z", "2026-10-03 24:00Z",
    "2026-W40-6T24:00Z", "2026-10-03T23:59:60Z", "2026-10-03T18:00:00",
    "1960-01-01T00:00:00Z", "1965-01-01T00:00:00Z", "1972-01-01T00:00:00+01:00",
])
def test_invalid_civil_time_and_historical_utc_are_422(time):
    with TestClient(app) as client:
        for route in ("visibility", "visibility/window", "summary"):
            response = client.get("/api/" + route, params={
                "lat": 0, "lon": 0, "terrain": False, "time": time,
                "start": time, "end": "2026-10-04T00:00:00Z",
            })
            assert response.status_code == 422, response.text


@pytest.mark.parametrize("time", [
    "2026-10-03T18:24:00Z", "2026-10-03T18:00:24Z", "2026-10-03T18:00:00.240000Z",
    "2026-10-03T18:00:00+00:24", "2026-10-03T19:24:00+01:00",
    "1972-01-01T00:00:00Z", "1971-12-31T23:00:00-01:00",
])
def test_valid_minutes_fractions_and_normalized_utc_boundary(time):
    assert parse_utc_time(time).year >= 1972


@real_kernels
def test_native_properties_flat_geometry_and_kilometre_ranges():
    observer = LunarObserver.from_spice(
        context=spice_context(), frame="MOON_ME", lon_deg=45, lat_deg=20, radius_m=1737400,
    )
    result = calculate_visibility(20, 45, TIME)
    assert calculate_visibility(20, 45, TIME, 1737400) == result
    jd_tt = JulianDate.from_utc(TIME).value
    for name, native in (("sun", observer.observe_sun(jd_tt)), ("earth", observer.observe_earth(jd_tt))):
        assert result[name] == {"azimuth": native.az_deg, "elevation": native.alt_deg,
                                "visible": native.above_horizon}
        assert native.range_unit == "km"
        assert native.range > (1e8 if name == "sun" else 3e5)


@real_kernels
def test_directory_switch_evicts_context_but_observer_keeps_owned_state(monkeypatch, tmp_path):
    from backend.spice_kernels import _context
    original = spice_context()
    observer = LunarObserver.from_spice(context=original, frame="MOON_ME", lon_deg=45, lat_deg=20)
    jd_tt = JulianDate.from_utc(TIME).value
    expected = observer.observe_sun(jd_tt).to_dict()
    for name in KERNEL_FILES:
        shutil.copyfile(kernel_directory() / name, tmp_path / name)
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))
    other = spice_context()
    assert other is not original
    assert spice_context() is other
    assert _context.cache_info().currsize == 1
    del original
    gc.collect()
    assert observer.observe_sun(jd_tt).to_dict() == expected
    # A warm context never bypasses missing-file checks; repair restores service.
    missing = tmp_path / "naif0012.tls"
    content = missing.read_bytes()
    missing.unlink()
    with pytest.raises(KernelError, match="Missing"):
        spice_context()
    missing.write_bytes(content)
    assert calculate_visibility(20, 45, TIME)["sun"]["azimuth"] == observer.observe_sun(jd_tt).az_deg


@real_kernels
@pytest.mark.parametrize("time", ["2151-01-01T00:00:00Z", "2651-01-01T00:00:00Z"])
def test_spk_and_bpc_coverage_errors_remain_502(time):
    with TestClient(app) as client:
        response = client.get("/api/visibility", params={"lat": 0, "lon": 0, "terrain": False, "time": time})
    assert response.status_code == 502
    assert "coverage" in response.json()["detail"]


@real_kernels
def test_concurrent_api_requests_match_sequential_responses():
    with TestClient(app) as client:
        queries = [
            ("/api/visibility", {"lat": lat, "lon": lon, "terrain": False, "time": TIME})
            for lat, lon in ((-90, 180), (0, 0), (90, -180), (20, 45))
        ] + [("/api/sites/athena-im2/visibility", {"time": TIME}),
             ("/api/sites/odysseus-im1/visibility", {"time": TIME})]
        def request(query):
            route, params = query
            response = client.get(route, params=params)
            assert response.status_code == 200, response.text
            return response.json()
        expected = [request(query) for query in queries]
        with ThreadPoolExecutor(max_workers=12) as pool:
            actual = list(pool.map(request, queries * 8))
    assert actual == expected * 8


def test_invalid_native_kernel_format_fails_without_fallback(monkeypatch, tmp_path):
    import backend.spice_kernels as kernels
    from backend.setup_kernels import file_hash
    for name in KERNEL_FILES:
        (tmp_path / name).write_bytes(b"not a SPICE kernel")
    # Test native parser failure independently of authentication failure.
    monkeypatch.setattr(kernels, "KERNELS", {
        name: (name, file_hash(tmp_path / name)) for name in KERNEL_FILES
    })
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/visibility", params={"lat": 0, "lon": 0, "time": TIME, "terrain": False})
    # The native loader defers some parsing until observation; errors stay explicit.
    assert response.status_code == 502
    assert "Siderust could not calculate" in response.json()["detail"]


@real_kernels
def test_kernel_change_during_load_is_rejected(monkeypatch, tmp_path):
    import backend.setup_kernels as setup
    for name in KERNEL_FILES:
        shutil.copyfile(kernel_directory() / name, tmp_path / name)
    original_hash = setup.file_hash
    def change_after_hash(path):
        digest = original_hash(path)
        if path.name == "pck00011.tpc":
            path.write_bytes(path.read_bytes().replace(b"1737.4", b"1738.4"))
        return digest
    monkeypatch.setattr(setup, "file_hash", change_after_hash)
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))
    with pytest.raises(KernelError, match="changed during loading"):
        spice_context()
