from datetime import datetime, timedelta, timezone
import math
import re

from siderust import LunarObserver
from tempoch import JulianDate

from backend.kernel_context import get_context


# Authenticated pck00011.tpc has equal BODY301_RADII = 1737.4 km.
REFERENCE_RADIUS_M = 1737400.0
MAX_WINDOW_SAMPLES = 2000


class ObservationError(RuntimeError):
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
        # Python 3.14 accepts 24:00; retain strict civil hours, including basic ISO.
        if re.match(r"^(?:\d{4}-?\d{2}-?\d{2}|\d{4}-?W\d{2}(?:-?\d)?).24", value.strip()):
            raise ValueError("hour must be below 24")
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("time must be an ISO 8601 timestamp with a timezone") from exc

    if parsed.tzinfo is None:
        raise ValueError("time must include a timezone, for example 2026-10-03T18:00:00Z")
    try:
        parsed = parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise ValueError("timestamp is outside the supported UTC datetime range") from exc

    if parsed.year < 1972:
        raise ValueError("timestamp must be on or after 1972-01-01 UTC")
    return parsed


def calculate_visibility(latitude: float, longitude: float, utc_time: str,
                         observer_radius_m: float | None = None) -> dict:
    latitude, longitude = validate_coordinates(latitude, longitude)
    if observer_radius_m is not None and (not math.isfinite(observer_radius_m) or observer_radius_m <= 0):
        raise ValueError("observer radius must be a positive finite distance")
    time = parse_utc_time(utc_time)
    output_time = time.isoformat().replace("+00:00", "Z")

    context = get_context()
    try:
        observer = LunarObserver.from_spice(
            context=context, frame="MOON_ME", lon_deg=longitude, lat_deg=latitude,
            radius_m=REFERENCE_RADIUS_M if observer_radius_m is None else observer_radius_m,
        )
        jd_tt = JulianDate.from_utc(output_time).value
        bodies = {
            name: {"azimuth": result.az_deg, "elevation": result.alt_deg,
                   "visible": result.above_horizon}
            for name, result in (
                ("sun", observer.observe_sun(jd_tt=jd_tt)),
                ("earth", observer.observe_earth(jd_tt=jd_tt)),
            )
        }
    except (ValueError, OSError) as exc:
        raise ObservationError(f"Siderust could not calculate Sun/Earth position: {exc}") from exc

    return {
        "site": None,
        "latitude": latitude,
        "longitude": longitude,
        "time": output_time,
        **bodies,
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
        samples.append({key: result[key] for key in ("time", "sun", "earth")})
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
