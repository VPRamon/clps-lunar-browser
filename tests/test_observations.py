import math

import pytest

from backend.calculations.sun_earth import (
    calculate_visibility,
    calculate_visibility_window,
)
from backend.setup_kernels import KERNELS, kernel_directory


KERNELS_AVAILABLE = all((kernel_directory() / name).is_file() for name in KERNELS)
pytestmark = pytest.mark.skipif(
    not KERNELS_AVAILABLE,
    reason="download the SPICE kernels described in backend/kernels/README.md",
)

# Snapshot from JPL Horizons: target 10/399, topocentric observer coord@301,
# geodetic SITE_COORD (east longitude, latitude, 0 km), quantity 4, airless.
# Horizons uses DE441 and apparent directions; this backend uses DE440s and
# geometric directions, so the comparison below allows a small angular difference.
HORIZONS_CASES = [
    (
        "equator-noon",
        0,
        0,
        "2026-10-03T12:00:00Z",
        {
            "sun": (268.956425, 2.374065),
            "earth": (163.063455, 84.682522),
        },
    ),
    (
        "equator-sunset",
        0,
        0,
        "2026-10-03T16:30:00Z",
        {
            "sun": (268.952991, 0.088674),
            "earth": (160.434707, 84.823564),
        },
    ),
    (
        "mid-latitude",
        20,
        45,
        "2026-10-04T06:00:00Z",
        {
            "sun": (292.099914, -48.104074),
            "earth": (244.780449, 41.357761),
        },
    ),
    (
        "south-pole",
        -89.5,
        135,
        "2026-10-03T18:00:00Z",
        {
            "sun": (134.332834, 0.698389),
            "earth": (226.760415, 4.171660),
        },
    ),
    (
        "near-pole",
        -89.9,
        0,
        "2026-10-03T00:00:00Z",
        {
            "sun": (278.467568, 1.045185),
            "earth": (1.036680, 5.396450),
        },
    ),
]
HORIZONS_TOLERANCE_DEGREES = 0.02


def _angle_difference(first: float, second: float) -> float:
    return abs((first - second + 180) % 360 - 180)


@pytest.mark.parametrize(
    ("case_name", "latitude", "longitude", "time", "reference"),
    HORIZONS_CASES,
    ids=[case[0] for case in HORIZONS_CASES],
)
def test_real_positions_match_jpl_horizons(
    case_name, latitude, longitude, time, reference
):
    result = calculate_visibility(latitude, longitude, time)

    for body_name, (reference_azimuth, reference_elevation) in reference.items():
        body = result[body_name]
        assert _angle_difference(body["azimuth"], reference_azimuth) < (
            HORIZONS_TOLERANCE_DEGREES
        ), case_name
        assert abs(body["elevation"] - reference_elevation) < (
            HORIZONS_TOLERANCE_DEGREES
        ), case_name
        assert body["visible"] is (reference_elevation > 0)


@pytest.mark.parametrize(
    ("latitude", "longitude", "time"),
    [
        (-89.9, 0, "2026-10-03T00:00:00Z"),
        (-89.5, 135, "2026-10-03T18:00:00Z"),
        (-85, 30, "2026-10-03T18:00:00Z"),
        (0, 0, "2026-10-03T12:00:00Z"),
        (20, 45, "2026-10-04T06:00:00Z"),
    ],
)
def test_real_positions_have_valid_angles_and_visibility(latitude, longitude, time):
    result = calculate_visibility(latitude, longitude, time)

    for body in (result["sun"], result["earth"]):
        assert math.isfinite(body["azimuth"])
        assert 0 <= body["azimuth"] < 360
        assert -90 <= body["elevation"] <= 90
        assert body["visible"] is (body["elevation"] > 0)


def test_south_pole_calculation_matches_real_kernel_reference():
    result = calculate_visibility(-89.5, 135, "2026-10-03T18:00:00Z")

    assert result["sun"]["azimuth"] == pytest.approx(134.338517, abs=1e-5)
    assert result["sun"]["elevation"] == pytest.approx(0.698450, abs=1e-5)
    assert result["earth"]["azimuth"] == pytest.approx(226.760616, abs=1e-5)
    assert result["earth"]["elevation"] == pytest.approx(4.171645, abs=1e-5)


def test_visibility_changes_when_sun_crosses_the_flat_horizon():
    before = calculate_visibility(0, 0, "2026-10-03T16:30:00Z")["sun"]
    after = calculate_visibility(0, 0, "2026-10-03T17:00:00Z")["sun"]

    assert before["elevation"] > 0
    assert before["visible"] is True
    assert after["elevation"] < 0
    assert after["visible"] is False


def test_positions_move_smoothly_with_time_and_nearby_coordinates():
    first = calculate_visibility(-89.5, 135, "2026-10-03T18:00:00Z")
    later = calculate_visibility(-89.5, 135, "2026-10-03T18:30:00Z")
    nearby = calculate_visibility(-89.49, 135.01, "2026-10-03T18:00:00Z")

    for body_name in ("sun", "earth"):
        earlier_body = first[body_name]
        later_body = later[body_name]
        nearby_body = nearby[body_name]
        time_azimuth_change = _angle_difference(
            earlier_body["azimuth"], later_body["azimuth"]
        )
        time_elevation_change = abs(earlier_body["elevation"] - later_body["elevation"])
        assert time_azimuth_change < 1
        assert time_elevation_change < 1
        assert max(time_azimuth_change, time_elevation_change) > 1e-5
        assert _angle_difference(earlier_body["azimuth"], nearby_body["azimuth"]) < 0.2
        assert abs(earlier_body["elevation"] - nearby_body["elevation"]) < 0.2


def test_window_samples_match_single_time_calculations():
    samples = calculate_visibility_window(
        -89.5,
        135,
        "2026-10-03T00:00:00Z",
        "2026-10-03T02:00:00Z",
        30,
    )

    assert [sample["time"] for sample in samples] == [
        "2026-10-03T00:00:00Z",
        "2026-10-03T00:30:00Z",
        "2026-10-03T01:00:00Z",
        "2026-10-03T01:30:00Z",
        "2026-10-03T02:00:00Z",
    ]
    for sample in samples:
        single = calculate_visibility(-89.5, 135, sample["time"])
        assert set(sample) == {"time", "sun", "earth"}
        assert sample["sun"] == single["sun"]
        assert sample["earth"] == single["earth"]


def test_window_rejects_invalid_range_and_step():
    with pytest.raises(ValueError, match="at or after"):
        calculate_visibility_window(
            0,
            0,
            "2026-10-04T00:00:00Z",
            "2026-10-03T00:00:00Z",
        )
    with pytest.raises(ValueError, match="positive integer"):
        calculate_visibility_window(
            0,
            0,
            "2026-10-03T00:00:00Z",
            "2026-10-03T00:00:00Z",
            0,
        )


@pytest.mark.skipif(not KERNELS_AVAILABLE, reason="SPICE kernels not installed")
def test_surface_observer_height_has_expected_earth_parallax():
    # Radial observer displacement changes elevation by -dh*cos(elevation)/distance.
    ground = calculate_visibility(20, 30, "2026-10-03T18:00:00Z", 1737400)
    raised = calculate_visibility(20, 30, "2026-10-03T18:00:00Z", 1747400)
    import math
    change = raised["earth"]["elevation"] - ground["earth"]["elevation"]
    assert change < 0
    assert change == pytest.approx(-math.degrees(10 / 380000 * math.cos(math.radians(ground["earth"]["elevation"]))), abs=0.0002)
    assert raised["earth"]["azimuth"] == pytest.approx(ground["earth"]["azimuth"], abs=1e-10)


@pytest.mark.parametrize("radius", [0, -1, float("nan"), float("inf")])
def test_invalid_observer_radius_is_rejected(radius):
    with pytest.raises(ValueError, match="observer radius"):
        calculate_visibility(0, 0, "2026-10-03T18:00:00Z", radius)
