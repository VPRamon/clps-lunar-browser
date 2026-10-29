"""Run from repository root: python -m benchmarks.compare_astro_backends --output FILE."""

import argparse
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
import spiceypy as spice

from backend.calculations import siderust_backend as native
from backend.calculations.sun_earth import (
    calculate_visibility_window,
    observe_spiceypy_et,
)
from backend.spice_kernels import SPICE_LOCK, load_spice_kernels, verified_kernel_set
from examples.compare_siderust_spiceypy import residuals


def distribution(values):
    return {
        "median_ms": statistics.median(values) * 1000,
        "p10_ms": float(np.percentile(values, 10)) * 1000,
        "p90_ms": float(np.percentile(values, 90)) * 1000,
        "min_ms": min(values) * 1000,
        "max_ms": max(values) * 1000,
        "stdev_ms": statistics.stdev(values) * 1000 if len(values) > 1 else 0,
        "runs": len(values),
    }


def measure(fn, runs):
    fn()
    values = []
    for _ in range(runs):
        start = time.perf_counter()
        fn()
        values.append(time.perf_counter() - start)
    return distribution(values)


def cold(engine):
    # Fresh process; timer starts after imports, before verification and loading.
    code = """import json,time,resource
from pathlib import Path
from backend.spice_kernels import load_spice_kernels
from backend.calculations.siderust_backend import observer
start=time.perf_counter()
%s
elapsed=time.perf_counter()-start
# /proc resets across exec; getrusage can retain the spawning parent's peak.
status=Path('/proc/self/status').read_text()
rss=int(next(line for line in status.splitlines() if line.startswith('VmHWM:')).split()[1])
print(json.dumps({'seconds':elapsed,'rss_kib':rss}))
""" % ("load_spice_kernels()" if engine == "spiceypy" else "observer(20,45,1737400)")
    return json.loads(subprocess.check_output([sys.executable, "-c", code], text=True))


def scientific_report():
    sites = [
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
    epochs = [-4730000000.0, -2208988800.0, 0.0, 844000000.0, 4700000000.0]
    maxima = {
        name: {key: 0.0 for key in ("azimuth_deg", "elevation_deg", "range_km")}
        for name in ("sun", "earth")
    }
    for lat, lon in sites:
        for radius in [1730000.0, 1737400.0, 1745000.0]:
            site = native.observer(lat, lon, radius)
            for et in epochs:
                a = observe_spiceypy_et(lat, lon, et, radius)
                b = native.observations(site, et=et)
                for body, fields in residuals(a, b).items():
                    for field, value in fields.items():
                        maxima[body][field] = max(maxima[body][field], value)
    times = []
    for utc in [
        "1961-01-01T00:00:00Z",
        "1965-01-01T00:00:00Z",
        "1972-07-01T00:00:00Z",
        "2000-01-01T12:00:00Z",
        "2016-12-31T23:59:59Z",
        "2017-01-01T00:00:00Z",
        "2026-10-03T18:00:00Z",
        "2149-01-01T00:00:00Z",
    ]:
        et = spice.utc2et(utc)
        jd = native.utc_to_tt(utc)
        observation = site.observe_sun(jd_tt=jd)
        times.append(
            {
                "utc": utc,
                "tt_difference_seconds": (jd - spice.unitim(et, "ET", "JDTDT")) * 86400,
                "tdb_et_difference_seconds": observation.epoch_et_tdb_seconds - et,
            }
        )
    return {
        "site_epoch_radius_cases": 150,
        "max_exact_et_absolute_residuals": maxima,
        "time_models": times,
    }


def run(runs):
    from fastapi.testclient import TestClient
    from siderust import LunarObserver

    from backend.app import app
    from backend.setup_kernels import KERNELS

    results = {
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "cpu": next(
                (
                    line.strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                platform.processor(),
            ),
            "versions": {
                p: version(p)
                for p in ("siderust", "tempoch", "spiceypy", "numpy", "fastapi")
            },
            "kernel_sha256": {k: v[1] for k, v in KERNELS.items()},
        },
        "engines": {},
    }
    # Complete all fresh-process measurements before terrain touches large pages.
    cold_results = {
        engine: [cold(engine) for _ in range(runs)]
        for engine in ("spiceypy", "siderust")
    }
    for engine in ("spiceypy", "siderust"):
        cold_runs = cold_results[engine]
        os.environ["ASTRO_BACKEND"] = engine
        load_spice_kernels()
        site = native.observer(20, 45, 1737400)
        directory, identities = verified_kernel_set()
        context = native._context((str(directory), tuple(identities.items())))
        et = spice.utc2et("2026-10-03T18:00:00Z")

        def construct(engine=engine, context=context):
            if engine == "siderust":
                return LunarObserver.from_spice(
                    context, "MOON_ME", 45, 20, radius_m=1737400
                )
            _, r = spice.bodvrd("MOON", "RADII", 3)
            return spice.georec(
                math.radians(45), math.radians(20), 0, float(r[0]), (r[0] - r[2]) / r[0]
            )

        def pair(t=et, engine=engine, site=site):
            if engine == "siderust":
                return native.observations(site, et=t)
            with SPICE_LOCK:
                return observe_spiceypy_et(20, 45, t, 1737400)

        def multiple(engine=engine, et=et):
            for lat, lon in [
                (0, 0),
                (20, 45),
                (-84.79, 29.2),
                (-80.13, 1.44),
                (90, 180),
            ]:
                if engine == "siderust":
                    s = native.observer(lat, lon, 1737400)
                    for i in range(100):
                        native.observations(s, et=et + i * 60)
                else:
                    with SPICE_LOCK:
                        for i in range(100):
                            observe_spiceypy_et(lat, lon, et + i * 60, 1737400)

        client = TestClient(app)
        metrics = {
            "cold_verified_load": distribution([v["seconds"] for v in cold_runs]),
            "cold_process_peak_rss_kib": [v["rss_kib"] for v in cold_runs],
            "observer_construction": measure(construct, runs * 100),
            "one_exact_et_pair": measure(pair, runs * 100),
            "same_site_2000_pairs": measure(
                lambda et=et: [pair(et + i * 60) for i in range(2000)], runs
            ),
            "five_sites_100_pairs_each": measure(multiple, runs),
            "api_timeline_24h_49_samples": measure(
                lambda: calculate_visibility_window(
                    20, 45, "2026-10-03T00:00:00Z", "2026-10-04T00:00:00Z", 30, 1737400
                ),
                runs,
            ),
            "api_timeline_2000_samples": measure(
                lambda: calculate_visibility_window(
                    20, 45, "2026-10-03T00:00:00Z", "2026-10-04T09:19:00Z", 1, 1737400
                ),
                runs,
            ),
        }

        def request(terrain, client=client):
            response = client.get(
                "/api/sites/athena-im2/visibility",
                params={"time": "2026-10-03T18:00:00Z", "terrain": terrain},
            )
            response.raise_for_status()

        metrics["asgi_flat_request"] = measure(lambda: request(False), runs * 10)
        metrics["asgi_warm_terrain_request"] = measure(lambda: request(True), runs * 10)
        results["engines"][engine] = metrics
    results["science"] = scientific_report()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2")
    result = run(args.runs)
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
