"""Native DE440 observations; terrain and response formatting stay in CLPS."""

from functools import lru_cache
from threading import RLock

from backend.spice_kernels import KernelError, _identity, verified_kernel_set

_LOCK = RLock()
# The authenticated pck00011.tpc has three equal BODY301_RADII = 1737.4 km.
# Thus its zero-height georec surface equals a radial sphere for this set only.
REFERENCE_RADIUS_M = 1737400.0


def _bindings():
    try:
        from siderust import LunarObserver, SpiceContext
        from tempoch import JulianDate

        return SpiceContext, LunarObserver, JulianDate
    except ImportError as exc:
        raise KernelError(
            "Siderust engine unavailable; install requirements-siderust.txt "
            "(native lunar API requires 0.8.0). See docs/siderust-evaluation.md"
        ) from exc


@lru_cache(maxsize=2)
def _context(key):
    SpiceContext, _, _ = _bindings()
    try:
        context = SpiceContext.from_de440(key[0])
        if any(_identity(path) != identity for path, identity in key[1]):
            raise KernelError("SPICE kernel set changed during Siderust loading; retry")
        # Do not let observer entries retain many evicted full kernel contexts.
        # Running requests keep their own owned handles.
        _observer.cache_clear()
        return context
    except (OSError, ValueError) as exc:
        raise KernelError(f"Could not load Siderust DE440 context: {exc}") from exc


@lru_cache(maxsize=128)
def _observer(context, latitude, longitude, radius):
    _, LunarObserver, _ = _bindings()
    return LunarObserver.from_spice(
        context=context,
        frame="MOON_ME",
        lon_deg=longitude,
        lat_deg=latitude,
        radius_m=radius,
    )


def observer(latitude, longitude, radius=None):
    # Serialize cold construction (lru_cache alone can construct duplicate values).
    # File identities are checked on every request, even for cached observers.
    with _LOCK:
        directory, identities = verified_kernel_set()
        context = _context((str(directory), tuple(identities.items())))
        try:
            return _observer(
                context,
                latitude,
                longitude,
                REFERENCE_RADIUS_M if radius is None else radius,
            )
        except ValueError as exc:
            raise KernelError(
                f"Could not construct Siderust MOON_ME observer: {exc}"
            ) from exc


def observations(site, *, jd_tt=None, et=None):
    from backend.calculations.sun_earth import SpiceCalculationError

    try:
        if et is None:
            pair = site.observe_sun(jd_tt=jd_tt), site.observe_earth(jd_tt=jd_tt)
        else:
            pair = (
                site.observe_sun_et(et_tdb_seconds=et),
                site.observe_earth_et(et_tdb_seconds=et),
            )
        return {
            name: {
                "azimuth": value.az_deg,
                "elevation": value.alt_deg,
                "visible": value.above_horizon,
                "range_km": value.range,
            }
            for name, value in zip(("sun", "earth"), pair)
        }
    except (ValueError, OSError) as exc:
        raise SpiceCalculationError(
            f"Siderust could not calculate Sun/Earth position: {exc}"
        ) from exc


def utc_to_tt(utc):
    from backend.calculations.sun_earth import SpiceCalculationError

    _, _, JulianDate = _bindings()
    try:
        return JulianDate.from_utc(utc).value
    except ValueError as exc:
        raise SpiceCalculationError(
            f"Siderust UTC to TT conversion failed: {exc}"
        ) from exc


def observe_utc(latitude, longitude, utc, radius=None):
    return observations(observer(latitude, longitude, radius), jd_tt=utc_to_tt(utc))
