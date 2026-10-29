"""Deterministic randomized checks against independent geometric references."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.calculations.sun_earth import calculate_visibility, calculate_visibility_window
from backend.mission_summary import summarize_window
from backend.spice_kernels import KERNEL_FILES, kernel_directory
from backend.terrain.global_dem import RADIUS_M, build_global_profile, sample_height
from backend.terrain.horizon import horizon_at


def test_horizon_periodicity_including_negative_subnormal_bearings():
    profile = {"horizon": [2., 3., 5., 7.], "azimuth_step": 90.}
    for angle in [-1e-300, -1e-30, -math.ulp(0.), 0, 360, 720, -360]:
        assert horizon_at(profile, angle) == pytest.approx(2.)
    rng = np.random.default_rng(87)
    for angle in rng.uniform(-10000, 10000, 1000):
        assert horizon_at(profile, angle) == pytest.approx(horizon_at(profile, angle+360), abs=1e-10)


def _full_globe_reference(grid, latitude, longitude, profile):
    """Brute-force all raster cells as Cartesian points, with no bounding box."""
    rows, cols = grid.shape
    latitudes = np.radians(90-(np.arange(rows)+.5)*180/rows)
    longitudes = np.radians(-180+(np.arange(cols)+.5)*360/cols)
    clat, slat = np.cos(latitudes)[:, None], np.sin(latitudes)[:, None]
    unit = np.stack(np.broadcast_arrays(clat*np.cos(longitudes), clat*np.sin(longitudes), slat), axis=-1)
    lat, lon = math.radians(latitude), math.radians(longitude)
    observer_up = np.array([math.cos(lat)*math.cos(lon), math.cos(lat)*math.sin(lon), math.sin(lat)])
    # atan2(cross,dot) is independent of production's acos-based arc distance.
    distances = RADIUS_M*np.arctan2(np.linalg.norm(np.cross(unit, observer_up), axis=-1), unit@observer_up)
    inside = (distances >= profile["min_distance_m"]) & (distances <= profile["max_distance_m"])
    cells = unit[inside]*(RADIUS_M + grid[inside].astype(float)*.5 - 10000)[:, None]
    observer = observer_up*(RADIUS_M+profile["site_elevation_m"]+profile["observer_height_m"])
    rays = cells-observer
    east = np.array([-math.sin(lon), math.cos(lon), 0])
    north = np.cross(observer_up, east)
    bearing = np.degrees(np.arctan2(rays@east, rays@north)) % 360
    angle = np.degrees(np.arctan2(rays@observer_up, np.hypot(rays@east, rays@north)))
    cell_lat = np.broadcast_to(latitudes[:, None], grid.shape)[inside]
    half_size = math.pi*RADIUS_M/rows/2*np.sqrt(1+np.cos(cell_lat)**2)
    half_width = np.degrees(np.arctan2(half_size, distances[inside]))
    expected = []
    for bin_az in np.arange(len(profile["horizon"]))*profile["azimuth_step"]:
        covered = np.abs((bearing-bin_az+180) % 360-180) <= half_width+profile["azimuth_step"]/2
        expected.append(max(angle[covered]))
    return expected


def test_global_bounding_and_curvature_match_full_globe_cartesian_oracle():
    rng = np.random.default_rng(981)
    grid = rng.integers(16000, 28000, size=(180, 360), dtype=np.uint16)
    positions = [(90, 0), (-90, 135), (88.5, 179.9), (-88.5, -179.9), (0, 180), (0, -180)]
    positions += list(zip(rng.uniform(-90, 90, 24), rng.uniform(-180, 180, 24)))
    for lat, lon in positions:
        profile = build_global_profile(grid, lat, lon, max_distance_m=250000, azimuth_step=10)
        expected = _full_globe_reference(grid, lat, lon, profile)
        assert profile["horizon"] == pytest.approx(expected, abs=0.000051), (lat, lon)


def test_longitude_rotation_and_observer_height_monotonicity():
    rng = np.random.default_rng(726)
    grid = rng.integers(18000, 23000, size=(180, 360), dtype=np.uint16)
    for lat, lon in [(0, 170), (45, -170), (89, 22), (-90, -70)]:
        original = build_global_profile(grid, lat, lon, 250000, 10)
        rotated = build_global_profile(np.roll(grid, 45, axis=1), lat, (lon+45+180) % 360-180, 250000, 10)
        assert rotated["horizon"] == pytest.approx(original["horizon"], abs=.000051)
        raised = build_global_profile(grid, lat, lon, 250000, 10, observer_height_m=1000)
        assert all(a <= b for a, b in zip(raised["horizon"], original["horizon"]))


def test_bilinear_height_matches_analytic_plane_and_ignores_zero_weight_nodata():
    rows, cols = np.indices((180, 360))
    grid = (20000+2*rows+4*cols).astype(np.uint16)
    rng = np.random.default_rng(32)
    for y, x in zip(rng.uniform(1, 177, 1000), rng.uniform(1, 357, 1000)):
        lat, lon = 90-(y+.5), -180+(x+.5)
        assert sample_height(grid, lat, lon) == pytest.approx(y+2*x, abs=1e-9)
    # At the exact cell centre the diagonal neighbour contributes zero weight.
    grid[91, 181] = 0
    assert sample_height(grid, -.5, .5) == (grid[90, 180]*.5-10000)


def test_randomized_summary_matches_independent_interval_integration():
    rng = np.random.default_rng(330)
    for _ in range(500):
        count, step = int(rng.integers(1, 100)), int(rng.integers(1, 91))
        start = datetime(2026, 10, 3, tzinfo=timezone.utc)+timedelta(seconds=int(rng.integers(0, 10000)))
        lengths = [step*60.]*(count-1)+[float(rng.uniform(1, step*60))]
        end = start+timedelta(seconds=sum(lengths))
        # Use the actual microsecond-rounded final duration for the reference.
        lengths[-1] = (end-start).total_seconds()-sum(lengths[:-1])
        sun, earth = rng.integers(0, 2, count).astype(bool), rng.integers(0, 2, count).astype(bool)
        samples = [{"time": (start+timedelta(minutes=i*step)).isoformat(),
                    "sun": {"visible": bool(sun[i])}, "earth": {"visible": bool(earth[i])}}
                   for i in range(count)]
        result = summarize_window(None, samples, start.isoformat(), end.isoformat(), step)
        duration = sum(lengths)
        for name, flags in [("sunlight", sun), ("earth_visible", earth), ("both_available", sun & earth)]:
            expected = round(100*sum(length for flag, length in zip(flags, lengths) if flag)/duration, 1)
            assert result[name+"_percent"] == expected
        for name, flags in [("darkness", ~sun), ("comm_blackout", ~earth)]:
            best = run = 0.
            for active, length in zip(flags, lengths):
                run = run+length if active else 0
                best = max(best, run)
            assert result["longest_"+name+"_minutes"] == pytest.approx(best/60, abs=1e-6)


REAL_KERNELS = all((kernel_directory()/name).is_file() for name in KERNEL_FILES)


@pytest.mark.skipif(not REAL_KERNELS, reason="real NAIF kernels not installed")
def test_real_ephemeris_equivalent_timezones_seam_and_random_window_samples():
    rng = np.random.default_rng(109)
    for _ in range(60):
        latitude, longitude = float(rng.uniform(-90, 90)), float(rng.uniform(-180, 180))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)+timedelta(days=int(rng.integers(0, 365)))
        step = int(rng.integers(1, 61))
        end = start+timedelta(minutes=step*3+step/2)
        samples = calculate_visibility_window(latitude, longitude, start.isoformat(), end.isoformat(), step)
        assert len(samples) == 4
        for sample in samples:
            local_time = datetime.fromisoformat(sample["time"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=5, minutes=30)))
            single = calculate_visibility(latitude, longitude, local_time.isoformat())
            assert single["sun"] == sample["sun"]
            assert single["earth"] == sample["earth"]
    west = calculate_visibility(30, -180, "2026-10-03T00:00:00Z")
    east = calculate_visibility(30, 180, "2026-10-03T00:00:00Z")
    for body in ["sun", "earth"]:
        assert west[body]["elevation"] == pytest.approx(east[body]["elevation"], abs=1e-10)
        assert west[body]["azimuth"] == pytest.approx(east[body]["azimuth"], abs=1e-10)
