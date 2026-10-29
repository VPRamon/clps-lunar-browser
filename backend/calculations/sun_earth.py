from datetime import datetime, timedelta, timezone
import math
import os
import re

import numpy as np
import spiceypy
from spiceypy.utils.exceptions import SpiceyError

from backend.spice_kernels import SPICE_LOCK, load_spice_kernels


MOON_FRAME = "MOON_ME"
MAX_WINDOW_SAMPLES = 2000


class SpiceCalculationError(RuntimeError):
    pass


def validate_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    for name, value, limit in (("lat", latitude, 90), ("lon", longitude, 180)):
        try:
            valid = not isinstance(value, bool) and math.isfinite(value) and -limit <= value <= limit
        except (TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{name} must be between {-limit} and {limit} degrees")
    return latitude, longitude


def parse_utc_time(value: str) -> datetime:
    try:
        # Python 3.14 accepts 24:00; retain the API's strict civil-hour contract.
        if re.search(r"[^\d]24(?::|00)", value):
            raise ValueError("hour must be below 24")
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("time must be an ISO 8601 timestamp with a timezone") from exc

    if parsed.tzinfo is None:
        raise ValueError("time must include a timezone, for example 2026-10-03T18:00:00Z")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp is outside the supported UTC datetime range") from exc


def _local_angles(vector: np.ndarray, latitude: float, longitude: float) -> tuple[float, float]:
    distance = float(np.linalg.norm(vector))
    if not math.isfinite(distance) or distance == 0:
        raise SpiceCalculationError("SPICE returned an invalid position vector")

    lat = math.radians(latitude)
    lon = math.radians(longitude)
    direction = vector / distance

    # Azimuth is measured clockwise from north in the local east/north/up basis.
    east = np.array([-math.sin(lon), math.cos(lon), 0.0])
    north = np.array(
        [-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat)]
    )
    up = np.array(
        [math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)]
    )

    elevation = math.degrees(math.asin(float(np.clip(np.dot(direction, up), -1.0, 1.0))))
    azimuth = math.degrees(
        math.atan2(float(np.dot(direction, east)), float(np.dot(direction, north)))
    ) % 360
    # A tiny negative bearing can round up to 360 during modulo addition.
    if azimuth >= 360:
        azimuth = 0.0
    return azimuth, elevation


def calculate_visibility(latitude: float, longitude: float, utc_time: str,
                         observer_radius_m: float | None = None) -> dict:
    latitude, longitude = validate_coordinates(latitude, longitude)
    if observer_radius_m is not None and (not math.isfinite(observer_radius_m) or observer_radius_m <= 0):
        raise ValueError("observer radius must be a positive finite distance")
    time = parse_utc_time(utc_time)
    output_time = time.isoformat().replace("+00:00", "Z")

    engine = selected_backend()
    if engine == "siderust":
        from backend.calculations.siderust_backend import observe_utc
        bodies = observe_utc(latitude, longitude, output_time, observer_radius_m)
    else:
        with SPICE_LOCK:
            load_spice_kernels()
            try:
                et = spiceypy.utc2et(output_time)
                bodies = observe_spiceypy_et(latitude, longitude, et, observer_radius_m)
            except SpiceyError as exc:
                raise SpiceCalculationError(f"SPICE could not calculate the Sun/Earth position: {exc}") from exc

    return {
        "site": None,
        "latitude": latitude,
        "longitude": longitude,
        "time": output_time,
        "sun": {k: bodies["sun"][k] for k in ("azimuth", "elevation", "visible")},
        "earth": {k: bodies["earth"][k] for k in ("azimuth", "elevation", "visible")},
    }


def calculate_visibility_window(
    latitude: float,
    longitude: float,
    start: str,
    end: str,
    step_minutes: int = 30,
    observer_radius_m: float | None = None,
) -> list[dict]:
    """Calculate visibility at regular UTC times, including end when it lands on the grid."""
    start_time, end_time, step = validate_window_request(latitude, longitude, start, end, step_minutes)
    samples = []
    current = start_time
    while current <= end_time:
        result = calculate_visibility(latitude, longitude, current.isoformat(), observer_radius_m)
        samples.append(
            {
                "time": result["time"],
                "sun": result["sun"],
                "earth": result["earth"],
            }
        )
        if end_time - current < step:
            break
        current += step
    return samples


def validate_window_request(latitude: float, longitude: float, start: str, end: str,
                            step_minutes: int = 30) -> tuple[datetime, datetime, timedelta]:
    latitude, longitude = validate_coordinates(latitude, longitude)
    start_time = parse_utc_time(start)
    end_time = parse_utc_time(end)
    if (
        not isinstance(step_minutes, int)
        or isinstance(step_minutes, bool)
        or step_minutes < 1
    ):
        raise ValueError("step_minutes must be a positive integer")
    if end_time < start_time:
        raise ValueError("end must be at or after start")

    try:
        step = timedelta(minutes=step_minutes)
    except OverflowError as exc:
        raise ValueError("step_minutes is too large") from exc
    if (end_time - start_time) // step + 1 > MAX_WINDOW_SAMPLES:
        raise ValueError(f"window exceeds {MAX_WINDOW_SAMPLES} samples")

    return start_time, end_time, step


def selected_backend() -> str:
    engine = os.environ.get("ASTRO_BACKEND", "spiceypy")
    if engine not in ("spiceypy", "siderust"):
        from backend.spice_kernels import KernelError
        raise KernelError(f"Unknown ASTRO_BACKEND={engine!r}; choose spiceypy or siderust")
    return engine


def observe_spiceypy_et(latitude, longitude, et, observer_radius_m=None):
    """Original geometry at exact ET, including range for scientific comparison.

    Caller must hold SPICE_LOCK and load the configured kernels.
    """
    _, radii = spiceypy.bodvrd("MOON", "RADII", 3)
    equatorial_radius = float(radii[0])
    flattening = (equatorial_radius - float(radii[2])) / equatorial_radius
    if observer_radius_m is None:
        site_fixed = spiceypy.georec(
            math.radians(longitude), math.radians(latitude), 0.0, equatorial_radius, flattening
        )
    else:
        # LOLA latitudes are planetocentric; match its radial observer exactly.
        lat, lon = math.radians(latitude), math.radians(longitude)
        site_fixed = observer_radius_m / 1000 * np.array([
            math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)
        ])

    # SPICE rotates the surface point between the lunar fixed frame and inertial J2000.
    fixed_to_j2000 = spiceypy.pxform(MOON_FRAME, "J2000", et)
    j2000_to_fixed = spiceypy.pxform("J2000", MOON_FRAME, et)
    site_j2000 = fixed_to_j2000 @ site_fixed

    bodies = {}
    for name, target in (("sun", "SUN"), ("earth", "EARTH")):
        moon_to_body, _ = spiceypy.spkpos(target, et, "J2000", "NONE", "MOON")
        body_from_site_fixed = j2000_to_fixed @ (moon_to_body - site_j2000)
        azimuth, elevation = _local_angles(body_from_site_fixed, latitude, longitude)
        bodies[name] = {
            "azimuth": azimuth,
            "elevation": elevation,
            "visible": elevation > 0,
            "range_km": float(np.linalg.norm(body_from_site_fixed)),
        }

    return bodies
