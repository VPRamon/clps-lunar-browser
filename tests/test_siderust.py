"""Same-kernel identity tests are separate from the preserved Horizons tests."""

import math
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import pytest
import spiceypy as spice
from fastapi.testclient import TestClient

from backend.app import app
from backend.calculations.sun_earth import (
    SpiceCalculationError,
    calculate_visibility,
    calculate_visibility_window,
    observe_spiceypy_et,
)
from backend.spice_kernels import (
    KERNEL_FILES,
    SPICE_LOCK,
    KernelError,
    kernel_directory,
    load_spice_kernels,
)

pytest.importorskip("siderust")
from backend.calculations import siderust_backend as native

TIME = "2026-10-03T18:00:00Z"
SITES = [
    (0, 0),
    (20, 45),
    (-84.79, 29.2),
    (-80.13, 1.44),
    (90, 0),
    (-90, 180),
    (89.999999, 179.999999),
    (-89.999999, -179.999999),
    (0, 90),
    (0, -90),
]
# Absolute floors cover ordinary f64 roundoff; azimuth error is conditioned by
# horizontal projection norm and is undefined at exact zenith/nadir.
ANGLE_FLOOR_DEG = 1e-10
RANGE_FLOOR_KM = 2e-7  # a few ulps at the Sun's ~1.5e8 km slant range


@pytest.mark.parametrize("lat,lon", SITES)
@pytest.mark.parametrize(
    "et", [-4730000000.0, -2208988800.0, 0.0, 844000000.0, 4700000000.0]
)
@pytest.mark.parametrize("radius", [1730000.0, 1737400.0, 1745000.0])
def test_exact_et_parity(lat, lon, et, radius):
    with SPICE_LOCK:
        load_spice_kernels()
        reference = observe_spiceypy_et(lat, lon, et, radius)
    result = native.observations(native.observer(lat, lon, radius), et=et)
    for body in ("sun", "earth"):
        a, b = reference[body], result[body]
        assert abs(a["elevation"] - b["elevation"]) < ANGLE_FLOOR_DEG
        conditioning = max(abs(math.cos(math.radians(a["elevation"]))), 1e-8)
        assert (
            abs((a["azimuth"] - b["azimuth"] + 180) % 360 - 180)
            < ANGLE_FLOOR_DEG / conditioning
        )
        assert abs(a["range_km"] - b["range_km"]) < RANGE_FLOOR_KM
        assert a["visible"] == b["visible"]


def test_flat_surface_is_identical_for_authenticated_pck():
    with SPICE_LOCK:
        load_spice_kernels()
        _, radii = spice.bodvrd("MOON", "RADII", 3)
        assert list(radii) == [native.REFERENCE_RADIUS_M / 1000] * 3
        et = spice.utc2et(TIME)
        for lat, lon in SITES:
            surface = observe_spiceypy_et(lat, lon, et)
            radial = observe_spiceypy_et(lat, lon, et, native.REFERENCE_RADIUS_M)
            for name in surface:
                assert surface[name]["elevation"] == pytest.approx(
                    radial[name]["elevation"], abs=1e-12
                )


@pytest.mark.parametrize(
    "utc",
    [
        "1965-01-01T00:00:00Z",
        "1972-07-01T00:00:00Z",
        "2000-01-01T12:00:00Z",
        "2016-12-31T23:59:59Z",
        "2017-01-01T00:00:00Z",
        TIME,
        "2149-01-01T00:00:00Z",
    ],
)
def test_time_model_budget_separate_from_geometric_parity(utc):
    from tempoch import TimeScale, convert_timescale

    with SPICE_LOCK:
        load_spice_kernels()
        et = spice.utc2et(utc)
        tt = spice.unitim(et, "ET", "JDTDT")
    jd = native.utc_to_tt(utc)
    # Before 1972 tempoch models historical UTC drift; naif0012 extrapolates
    # integer leap offsets. Record this intentional model difference separately.
    budget = 7 if utc.startswith("1965") else 0.0001
    assert abs((jd - tt) * 86400) < budget
    obs = native.observer(20, 45).observe_sun(jd_tt=jd)
    converted = (convert_timescale(jd, TimeScale.TT, TimeScale.TDB) - 2451545) * 86400
    assert abs(obs.epoch_et_tdb_seconds - converted) < 0.0001
    assert abs(obs.epoch_et_tdb_seconds - et) < (
        7 if utc.startswith("1965") else 0.0001
    )


def test_utc_before_definition_is_rejected():
    with pytest.raises(SpiceCalculationError, match="1961"):
        native.utc_to_tt("1851-01-01T00:00:00Z")


def test_selection_contract_offset_and_no_fallback(monkeypatch):
    monkeypatch.setenv("ASTRO_BACKEND", "siderust")
    first = calculate_visibility(20, 45, TIME)
    assert first == calculate_visibility(20, 45, "2026-10-03T20:00:00+02:00")
    assert set(first["sun"]) == {"azimuth", "elevation", "visible"}
    with pytest.raises(ValueError, match="timezone"):
        calculate_visibility(20, 45, "2026-10-03T18:00:00")
    monkeypatch.setenv("ASTRO_BACKEND", "typo")
    assert (
        TestClient(app)
        .get(
            "/api/visibility",
            params={"lat": 0, "lon": 0, "time": TIME, "terrain": False},
        )
        .status_code
        == 503
    )
    monkeypatch.setenv("ASTRO_BACKEND", "siderust")
    monkeypatch.setitem(sys.modules, "siderust", None)
    with pytest.raises(KernelError, match="requirements-siderust"):
        calculate_visibility(1, 2, TIME)


def test_out_of_coverage_is_explicit(monkeypatch):
    monkeypatch.setenv("ASTRO_BACKEND", "siderust")
    with pytest.raises(SpiceCalculationError):
        calculate_visibility(0, 0, "2500-01-01T00:00:00Z")
    response = TestClient(app).get(
        "/api/visibility",
        params={"lat": 0, "lon": 0, "time": "2500-01-01T00:00:00Z", "terrain": False},
    )
    assert response.status_code == 502


def test_kernel_configuration_isolation_and_missing_file(monkeypatch, tmp_path):
    source = kernel_directory()
    original = native.observer(20, 45)
    with ThreadPoolExecutor(max_workers=8) as pool:
        sites = list(pool.map(lambda _: native.observer(20, 45), range(32)))
    assert all(site is original for site in sites)
    for name in KERNEL_FILES:
        shutil.copyfile(source / name, tmp_path / name)
    monkeypatch.setenv("SPICE_KERNELS_DIR", str(tmp_path))
    alternate = native.observer(20, 45)
    assert alternate != original
    assert native.observations(alternate, et=0) == native.observations(original, et=0)
    (tmp_path / "de440s.bsp").unlink()
    with pytest.raises(KernelError, match="Missing"):
        native.observer(20, 45)
    # Existing owned context stays valid after source paths disappear.
    assert native.observations(alternate, et=0) == native.observations(original, et=0)
    assert native._observer.cache_info().currsize <= 128


def test_month_timeline_multiple_crossings_and_horizon_cases(monkeypatch):
    windows = []
    for engine in ("spiceypy", "siderust"):
        monkeypatch.setenv("ASTRO_BACKEND", engine)
        windows.append(
            calculate_visibility_window(
                0, 90, "2026-10-01T00:00:00Z", "2026-11-30T00:00:00Z", 120
            )
        )
    for name in ("sun", "earth"):
        states = [sample[name]["visible"] for sample in windows[0]]
        assert sum(a != b for a, b in pairwise(states)) >= 2
        assert min(abs(sample[name]["elevation"]) for sample in windows[0]) < 0.1
        assert states == [sample[name]["visible"] for sample in windows[1]]
        assert (
            max(
                abs(a[name]["elevation"] - b[name]["elevation"])
                for a, b in zip(*windows)
            )
            < 1e-6
        )


def test_near_zenith_nadir_compare_directions_not_undefined_bearings():
    import numpy as np

    with SPICE_LOCK:
        load_spice_kernels()
        et = 844000000.0
        position, _ = spice.spkpos("SUN", et, "MOON_ME", "NONE", "MOON")
        unit = position / np.linalg.norm(position)
        for sign in (1, -1):
            lat = math.degrees(math.asin(sign * unit[2]))
            lon = math.degrees(math.atan2(sign * unit[1], sign * unit[0]))
            a = observe_spiceypy_et(lat, lon, et, 1737400)["sun"]
            b = native.observations(native.observer(lat, lon, 1737400), et=et)["sun"]

            def direction(record):
                az, el = map(math.radians, (record["azimuth"], record["elevation"]))
                return np.array(
                    [
                        math.cos(el) * math.sin(az),
                        math.cos(el) * math.cos(az),
                        math.sin(el),
                    ]
                )

            # Baseline asin(dot) loses precision near |dot|=1: sqrt(epsilon).
            assert np.linalg.norm(direction(a) - direction(b)) < 3e-8


def test_exact_et_at_both_sides_of_real_horizon_crossings():
    with SPICE_LOCK:
        load_spice_kernels()
        start = spice.utc2et("2026-10-01T00:00:00Z")
        site = native.observer(0, 90, 1737400)
        for name in ("sun", "earth"):
            previous = start
            for i in range(1, 61 * 12):
                current = start + i * 7200
                f = lambda et, name=name: observe_spiceypy_et(0, 90, et, 1737400)[name][
                    "elevation"
                ]
                if f(previous) * f(current) < 0:
                    break
                previous = current
            else:
                pytest.fail(f"No real {name} crossing found")
            left, right = previous, current
            for _ in range(40):
                mid = (left + right) / 2
                if f(left) * f(mid) <= 0:
                    right = mid
                else:
                    left = mid
            for et in (mid - 0.01, mid + 0.01):
                a = observe_spiceypy_et(0, 90, et, 1737400)[name]
                b = native.observations(site, et=et)[name]
                assert abs(a["elevation"]) < 1e-5
                assert a["visible"] == b["visible"]
                assert abs(a["elevation"] - b["elevation"]) < ANGLE_FLOOR_DEG


def test_concurrent_native_api_contract_and_shared_terrain(monkeypatch):
    monkeypatch.setenv("ASTRO_BACKEND", "siderust")
    client = TestClient(app)
    routes = [
        "/api/sites/athena-im2/visibility",
        "/api/sites/odysseus-im1/visibility",
        "/api/visibility",
    ]
    params = {"lat": 20, "lon": 45, "time": TIME, "terrain": False}
    references = {route: client.get(route, params=params).json() for route in routes}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda route: client.get(route, params=params), routes * 8)
        )
    for route, result in zip(routes * 8, results):
        assert result.status_code == 200
        assert result.json() == references[route]


def test_incompatible_frame_set_does_not_use_analytical_fallback():
    from siderust import LunarObserver, SpiceContext

    directory = kernel_directory()
    # A valid text PCK is not a lunar FK. Loading files is insufficient evidence
    # of scientific compatibility: requesting the missing runtime frame must fail.
    context = SpiceContext.from_kernels(
        spk=directory / "de440s.bsp",
        bpc=directory / "moon_pa_de440_200625.bpc",
        fk=directory / "pck00011.tpc",
    )
    with pytest.raises(ValueError, match="(?i)frame"):
        LunarObserver.from_spice(context, "MOON_ME", 0, 0, radius_m=1737400)
