"""Real-kernel, identical radial geometry comparison; no automatic downloads."""

import argparse
import json
from importlib.metadata import version

import spiceypy

from backend.calculations.siderust_backend import observations, observer, utc_to_tt
from backend.calculations.sun_earth import observe_spiceypy_et, parse_utc_time
from backend.landing_sites import get_landing_site
from backend.setup_kernels import KERNELS
from backend.spice_kernels import SPICE_LOCK, load_spice_kernels
from backend.terrain.horizon import apply_horizon
from backend.terrain.service import observer_radius, terrain_metadata, terrain_profile


def residuals(a, b):
    return {
        name: {
            "azimuth_deg": abs(
                (b[name]["azimuth"] - a[name]["azimuth"] + 180) % 360 - 180
            ),
            "elevation_deg": abs(b[name]["elevation"] - a[name]["elevation"]),
            "range_km": abs(b[name]["range_km"] - a[name]["range_km"]),
        }
        for name in ("sun", "earth")
    }


def compare(site_id="athena-im2", utc="2026-10-03T18:00:00Z"):
    site = get_landing_site(site_id)
    utc = parse_utc_time(utc).isoformat().replace("+00:00", "Z")
    profile = terrain_profile(site["latitude"], site["longitude"], site)
    radius = observer_radius(profile)
    with SPICE_LOCK:
        load_spice_kernels()
        et = spiceypy.utc2et(utc)
        baseline = observe_spiceypy_et(site["latitude"], site["longitude"], et, radius)
    native = observer(site["latitude"], site["longitude"], radius)
    exact = observations(native, et=et)
    jd_tt = utc_to_tt(utc)
    ordinary = observations(native, jd_tt=jd_tt)
    return {
        "versions": {
            name: version(name) for name in ("siderust", "tempoch", "spiceypy")
        },
        "site": site,
        "radius_m": radius,
        "frame": "MOON_ME_DE440_ME421",
        "utc": utc,
        "exact_et_tdb_seconds": et,
        "jd_tt": jd_tt,
        "kernels_sha256": {name: spec[1] for name, spec in KERNELS.items()},
        "spiceypy": baseline,
        "siderust_exact_et": exact,
        "exact_et_absolute_residuals": residuals(baseline, exact),
        "siderust_utc_tt": ordinary,
        "utc_path_absolute_residuals": residuals(baseline, ordinary),
        "terrain_result": apply_horizon(
            {
                "sun": dict(ordinary["sun"]),
                "earth": dict(ordinary["earth"]),
                **terrain_metadata(profile),
            },
            profile,
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", default="athena-im2")
    parser.add_argument("--time", default="2026-10-03T18:00:00Z")
    args = parser.parse_args()
    print(json.dumps(compare(args.site, args.time), indent=2))


if __name__ == "__main__":
    main()
