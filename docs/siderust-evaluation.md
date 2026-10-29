# Evaluating Siderust for CLPS Lunar Browser

This fork adds an optional native Siderust engine to the existing lunar planning
backend. The demonstrated benefit is encapsulating SPK/PCK/FK observations behind
an owned context and a lunar observer. It does **not** establish greater absolute
accuracy, survey-grade terrain predictions, or mission operational availability.
SpiceyPy remains the default and an independent reference implementation.

## Baseline and scope

The base is `902ee268a4aa3a46b355b73a80eb85be615a0ad4` on the fork's
`suhas/backend`, identical to the fetched head of
[upstream PR #8](https://github.com/suhaslord/clps-lunar-browser/pull/8).
The existing implementation already combines DE440s ephemerides, binary lunar
orientation, radial DEM observers, local and global LOLA skylines, explicit
provenance/error handling, Horizons regression references, and sampled planning
summaries. That scientific work is preserved. No frontend, catalog, terrain
algorithm, profile, or mission summary algorithm is replaced.

Only `feat/siderust-evaluation` in `VPRamon/clps-lunar-browser` is published.
This is a demonstration within the fork, not an upstream migration request.

## Architecture and actual code

`calculate_visibility` still validates coordinates/timestamps and produces the
same public JSON. `ASTRO_BACKEND` selects `spiceypy` or `siderust`. The ordinary
native path converts normalized UTC to TT through tempoch, then calls
`LunarObserver.observe_sun` and `observe_earth`. There is no analytical fallback.
The terrain service still supplies `datum_radius_m + site_elevation_m +
observer_height_m`; existing horizon masking and summaries consume the result.

The retained CSPICE calculation in `backend/calculations/sun_earth.py` uses:

```python
et = spiceypy.utc2et(output_time)
_, radii = spiceypy.bodvrd("MOON", "RADII", 3)
# georec for the reference surface, or a radial DEM position
fixed_to_j2000 = spiceypy.pxform(MOON_FRAME, "J2000", et)
j2000_to_fixed = spiceypy.pxform("J2000", MOON_FRAME, et)
site_j2000 = fixed_to_j2000 @ site_fixed
moon_to_body, _ = spiceypy.spkpos(target, et, "J2000", "NONE", "MOON")
body_from_site_fixed = j2000_to_fixed @ (moon_to_body - site_j2000)
azimuth, elevation = _local_angles(body_from_site_fixed, latitude, longitude)
```

The corresponding calls in `backend/calculations/siderust_backend.py` are:

```python
context = SpiceContext.from_de440(key[0])
return LunarObserver.from_spice(context=context, frame="MOON_ME", lon_deg=longitude,
                               lat_deg=latitude, radius_m=radius)
# Normalized aware UTC; value is a TT Julian Date, not UTC JD.
return JulianDate.from_utc(utc).value
pair = site.observe_sun(jd_tt=jd_tt), site.observe_earth(jd_tt=jd_tt)
```

The adapter also authenticates kernels, caches objects, maps errors, and formats
results. It does not implement an SPK evaluator, orientation interpolation, or
local projection. Kernel hash verification is factored out of the existing loader
and shared; native requests do not furnish or modify CSPICE's pool. Contexts are
immutable Rust-owned objects, cached for two file-identity configurations;
observers are cached for 128 geometry/context combinations. A new context clears the observer cache so it does not retain evicted kernel
sets; in-flight observer handles keep their owned context alive. A lock serializes cold cache
construction; observations use the binding's immutable shared handles. This is
state-isolation evidence, not proof of parallel CPU scaling: Python/PyO3/GIL
behavior and CLPS's existing terrain locks still matter.

File size, nanosecond modification/change times and inode identify verified
inputs. Every request rechecks identity; changed/missing/corrupt datasets fail
rather than serving stale cached observers. Ordinary requests never download
anything. Do not change process environment while serving requests: configure
one engine and kernel directory before Uvicorn startup.

## Scientific conventions and results

Both paths use geometric target centres (`abcorr=NONE`), SPK DE440s, the same
binary PCK and FK, north-clockwise azimuth, radial local up, east-positive
longitude and planetocentric latitude. No light-time, aberration, refraction,
physical limb, radio budget or power model is added. Native ranges are kilometres
and are exposed in the comparison tool only, preserving the public API keys.

The primary comparison supplies **identical ET (TDB seconds past J2000)** and
identical radial positions to both engines. Ten sites include equatorial,
mid-latitude, Athena, Odysseus, both exact poles, near poles and longitude wrap.
Three radii (1730, 1737.4 and 1745 km) and five ET epochs spanning almost all of
DE440s's 1850–2150 interval give 150 observer/epoch/radius cases, 300 target
observations. Machine-readable evidence: [science.json](evaluation/science.json).

| Maximum absolute residual | Sun | Earth |
| --- | ---: | ---: |
| Wrapped azimuth, degrees | 5.6843e-14 | 5.6843e-14 |
| Elevation, degrees | 2.8422e-14 | 1.4211e-13 |
| Slant range, km | 1.1921e-7 | 1.7462e-10 |

Acceptance uses a 1e-10 degree roundoff floor and a 2e-7 km range floor (a few
floating-point ulps at Sun distance), consistent with the binding's documented
same-kernel bounds and comfortably above observed residuals. Azimuth tolerance
is divided by the horizontal direction norm near zenith/nadir. Constructed
zenith/nadir tests compare direction vectors with a sqrt(machine-epsilon)
budget because the baseline `asin(dot)` loses conditioning at |dot|=1 and the
bearing there is undefined. No claim of a well-defined zenith azimuth is made.
Tests also bisect real Sun and Earth horizon crossings and compare classifications
on both sides, plus a two-month timeline with multiple crossings.

The flat-mode baseline calls `georec`, whereas Siderust accepts a radial observer.
For **this authenticated PCK**, all three `BODY301_RADII` values equal 1737.4 km.
The ellipsoidal flattening is therefore zero: both reference surfaces coincide.
A dedicated test verifies that fact and compares both paths. This is not a general
claim that geodetic and planetocentric coordinates are interchangeable. Terrain
requests use the actual profile radius, including the local DEM elevation and
instrument height, in both engines.

The original saved Horizons tests remain unchanged, with their 0.02 degree
budget for geometric DE440 versus apparent DE441 directions. They provide an
external regression check, not a machine-precision identity test. Same-kernel
agreement does not establish absolute ephemeris or terrain accuracy.

### UTC, TT and TDB are separate error budgets

`JulianDate.from_utc(utc_str).value` was checked against the installed tempoch
0.5.0 API. `SpiceContext.from_de440(kernel_dir)` and
`LunarObserver.from_spice(context, frame, lon_deg, lat_deg, *, height_m=None,
radius_m=None)` were checked against the installed 0.8.0 bindings and pinned
Rust/PyO3 source. Ordinary observations accept **TT JD**; exact-ET observations
use `observe_sun_et(et_tdb_seconds=...)` and `observe_earth_et(...)`.

For the six sampled dates from 1972 to 2149, tempoch and CSPICE TT JD values
coincide at double-JD precision. Native TT→TDB ET minus CSPICE UTC→ET ranges from
−21.93 to +38.74 microseconds. Double JD spacing is about 40 microseconds here;
conversion quantization and TT/TDB algorithms contribute. The suite compares
native epoch metadata with tempoch's explicit TT→TDB conversion, under a separate
100 microsecond budget. No timing-model error is hidden in the exact-ET table.

Historical UTC differs: at 1961-01-01, native minus CSPICE is about −7.57719 s;
at 1965-01-01 it is −5.45989 s. tempoch models pre-1972 UTC drift, whereas the
naif0012 pathway uses its leap-offset extrapolation. Native UTC before 1961 is
rejected explicitly (502); exact-ET observations remain usable inside kernel
coverage. Do not infer historical UTC parity from modern results. Both time
models assume the last known leap offset for future dates; neither predicts
future leap seconds. Python datetime rejects explicit `23:59:60`; the unchanged
API convention accepts ordinary civil timestamps only. Timezone-offset
normalization and naive-time rejection are tested. A small compatibility fix
also retains rejection of `24:00`, which Python 3.14 newly accepts.

## Performance

Measured on 2026-10-29, Intel Core Ultra 9 185H, Linux x86-64/glibc 2.43,
CPython 3.14.6, NumPy 2.5.4, SpiceyPy 8.3.0, source-built Siderust 0.8.0 and
tempoch 0.5.0. [benchmark.json](evaluation/benchmark.json) includes full
hardware/library/kernel identifiers and summary distributions. All entries
below are milliseconds, median (p90):

| Workload | SpiceyPy | Siderust |
| --- | ---: | ---: |
| Fresh-process verified kernel/context load | 58.3969 (63.0858) | 71.1823 (74.0543) |
| Observer construction | 0.006499 (0.0067831) | 0.000505501 (0.000846998) |
| One exact-ET Sun/Earth pair | 0.050183 (0.0566072) | 0.002192 (0.002352) |
| 2,000 exact-ET pairs, one site | 103.199 (103.779) | 4.6618 (13.0191) |
| Five sites × 100 exact-ET pairs | 25.2173 (25.3915) | 1.50239 (1.5168) |
| 24-hour timeline, 49 samples | 16.2364 (16.5445) | 5.03106 (5.24036) |
| Timeline at 2,000-sample limit | 664.964 (669.326) | 205.226 (206.025) |
| Warm ASGI flat request | 3.2079 (4.21444) | 2.45255 (3.11077) |
| Warm ASGI terrain request | 4.55469 (5.5162) | 3.56392 (4.36677) |

The 2,000-pair microbenchmark is about 22× faster here and the complete
2,000-sample calculation is about 3.2× faster. The reduced timeline advantage
reflects per-sample identity verification, UTC conversion and Python response
construction that remain in both application paths. Single-request ASGI
results are much closer and overlap in variability; they do not establish a
portable service throughput gain. Cold loading is slower for Siderust, and fresh
process peak RSS is about 124,460 KiB versus 56,352 KiB for SpiceyPy: owned parsed
kernels have an appreciable memory cost in this configuration. No memory or
cold-start advantage is claimed.

Seven repetitions are used for cold load and multi-sample workloads, 700 for
single pair/construction and 70 for ASGI requests, after an untimed warmup.
JSON records min/max, stdev, p10 and p90 as well as medians. Fresh processes have
empty application caches but **warm OS page cache**, not guaranteed cold disk.
Load timing includes hash authentication and native import on first use, excludes
common module imports and interpreter startup, and for native includes the first
observer construction. Memory uses each process's Linux `/proc/self/status`
VmHWM: Linux `getrusage` can inherit a spawning process's earlier peak and was not
used as the reported memory measurement. This benchmark is Linux-specific.

Exact-ET loops perform equivalent two-target angle/range observations without
UTC conversion, disk I/O or terrain preprocessing. The original SpiceyPy geometry
is retained, including its two rotations and radial surface position; native
observers are reused as intended. Construction measures `bodvrd`/`georec` versus
`from_spice` with a loaded context; those objects have different responsibilities,
so this is a setup-cost comparison, not an identical low-level operation.
The API timelines use both real UTC pathways and existing kernel/cache checks.
ASGI timings include FastAPI serialization and a TestClient round trip. Terrain
requests warm the same actual Athena profile before measurement; cold terrain
preprocessing is not charged to only one engine. Live TCP/Uvicorn requests are
verified separately in [live-http.json](evaluation/live-http.json), whose
single-run timings are **not** a statistical HTTP benchmark.

The many Python→CSPICE calls and NumPy projection in the retained baseline versus
two native observation calls explain a plausible portion of the warm gain; this
measurement does not isolate FFI overhead from numerical algorithms. Both engines
run on the same interpreter, hardware, kernels, sites and epochs, sequentially.
Engine order is fixed, system load/CPU frequency/thermal state are not controlled,
and between-invocation timing shifts were observed. Treat the results as a local
reproducible experiment, not a universal speed claim or an optimized-CSPICE limit.

## Application maintenance and integration cost

No application-owned astronomical code is deleted: **0 net lines removed**.
Keeping an independent baseline is essential to this evaluation. Two custom
scientific routines (`_local_angles` and the observer/rotation/projection loop)
are bypassed in native mode, rather than falsely counted as eliminated from
the repository. [code-counts.json](evaluation/code-counts.json) records physical
and nonblank/noncomment function-line counts, including docstrings, for review.
Native observation glue still handles errors and maps objects to CLPS dictionaries;
the entire adapter also needs verification/cache/dependency handling.

The retained geometry routines contain 63 physical lines / 52 nonblank,
noncomment lines (including docstrings), versus 26 / 25 for the native observation
and result-mapping helpers. Context/observer setup and error handling add further
glue in the native module. Two custom geometry routines are avoided in the native
path; none are deleted from the repository.

Six explicit scientific responsibilities move behind Siderust's interface:
SPK target evaluation, binary-PCK/FK rotation, observer translation/parallax,
radial site interpretation, ENU projection, and angular/range units. UTC/TT
conversion is delegated separately to tempoch. CLPS still owns selecting a
compatible kernel set and correctly labelling inputs and time scales. Python
floats are not statically unit-safe merely because Rust internals are typed.

This **adds** integration code and a second dependency path; a shorter native
science call site does not prove fewer bugs or lower total maintenance cost.
The practical prospect is reusing the same observer abstraction in other
planetary applications, subject to each body's actual library support. Existing
SpiceyPy lock/recovery logic is legitimate and remains available.

On this Linux installation, package directories occupy approximately 9.06 MB
for Siderust, 1.00 MB for tempoch, and 8.55 MB for SpiceyPy (uncompressed,
including Python files; not wheel/download/container sizes). Both engines stay
installed for comparison, so this evaluation increases footprint. Kernels and
LOLA dominate data storage. Build caches and the Rust toolchain add development
cost and are not needed after deploying a compatible locally built wheel.

## Reproducible installation and demonstration

PyPI release verification returned HTTP 404 for
`https://pypi.org/pypi/siderust/0.8.0/json`, and direct installation of
`siderust==0.8.0` failed resolution. A merged PR was not treated as publication.
The fallback is explicitly pinned to
[`aef6b565773cf806be231df9e91086edc9f785c3`](https://github.com/Siderust/siderust-py/commit/aef6b565773cf806be231df9e91086edc9f785c3),
which declares Python version 0.8.0, with Siderust Rust 0.17.0 and SPICE enabled.
It builds locally through maturin/PyO3; a Rust/Cargo toolchain, C linker and Git
are currently required. No upstream source was edited and no package published.

From the fork's evaluation branch:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -r requirements-siderust.txt
python -m backend.setup_kernels
python -m backend.setup_terrain
python -m backend.setup_kernels --check
python -m backend.setup_terrain --check
python -m examples.compare_siderust_spiceypy
python -m examples.compare_siderust_spiceypy --site odysseus-im1
ASTRO_BACKEND=siderust uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

The demo prints versions, kernel SHA-256 identifiers, actual observer radius,
frame, UTC/TT/ET, side-by-side angles/ranges, absolute residuals for exact ET
and ordinary UTC paths, and terrain clearance/provenance. It uses the existing
catalog and real detailed/global LOLA profiles; failures are visible tracebacks,
not omitted comparisons. `ASTRO_BACKEND=spiceypy` selects the independent baseline.

To reproduce the recorded Python environment, use
`python -m pip install -r docs/evaluation/python-environment.txt` instead of the
two requirements commands (same Python/platform recommended). The source commit
and its Cargo lockfile pin Rust dependencies; the recorded compiler was Rust/Cargo 1.97.1; compiler/platform changes may
still affect results. The optional source-build requirement should be replaced
by a verified compatible published 0.8.0 wheel when available, rather than silently
installing an older version without `SpiceContext`.

```sh
ASTRO_BACKEND=spiceypy python -m pytest -q
ASTRO_BACKEND=siderust python -m pytest -q
python -m pytest tests/test_siderust.py -q
python -m examples.smoke_astro_api
python -m benchmarks.compare_astro_backends --runs 7 --output benchmark.json
```

The benchmark's JSON includes environment, dependency versions, kernel hashes,
median/stdev/min/max/p10/p90 and separate time/geometry evidence. Run benchmarks
on an otherwise idle machine and repeat before capacity decisions.

## Validation and operational robustness

With the full verified DEM, both engine settings pass all **369 tests** on
CPython 3.14.6 and on CPython 3.11.15 (the CI version). The dedicated native suite
contains 167 passing cases. Both named-site demos and real Uvicorn smoke checks
succeed. Warnings are dependency deprecations: one Starlette/httpx warning on
3.14; 240 including Rasterio/Affine deprecations on 3.11. An isolated rerun of
the unmodified base with complete data passes 195 tests and fails the one
`24:00` parser regression on Python 3.14; this integration fixes that compatibility
issue. Hosted CI also caught an overbroad intermediate hour check that matched
`:24` minutes; the final check is restricted to the hour after the date/time
separator, with six independent regression cases. All four local full-suite runs
were repeated after the correction. Hosted CI status is recorded on the PR.

The first unmodified-base
run while the DEM was downloading had 183 passing, 6 failing and 7 skipped tests:
five failures required the absent DEM, and one exposed Python 3.14's acceptance
of `24:00`. This is recorded rather than described as a clean initial baseline.

Existing tests are preserved. New tests cover configuration, missing bindings,
coverage failures, kernel-directory isolation, missing files, shared immutable
observers, concurrent actual ASGI requests, flat reference geometry, all parity
cases, time conventions, horizon crossings and singular direction conditioning.
Existing corrupt-kernel/recovery and terrain tests run under both engine settings.
The live smoke command additionally starts real Uvicorn subprocesses for both
engines and exercises readiness, sites, named/coordinate visibility, skyline,
24-hour timeline and summary. It checks HTTP errors and records latency without
presenting single cold requests as statistical performance results.

CI retains the baseline job and adds a Siderust matrix job, with explicit native
source installation, the same verified datasets, full tests, demo, benchmark and
uploaded JSON evidence. Timing numbers from CI should not be treated as stable
hardware-independent guarantees.

## Limits, adoption and licensing

- Kernel coverage is finite; errors outside coverage are explicit. Hashes enforce
  this matching kernel set; arbitrary replacement kernels require a reviewed
  manifest/convention update. Equal lunar radii are a property of the current PCK.
- Terrain remains limited by resolution, inner exclusion, registration, assumed
  instrument height and 300 km extent. `accuracy.status` and visibility validation
  remain `unvalidated`; null uncertainty fields are preserved.
- DEM profiles use DE421 mean-Earth coordinates and runtime uses the DE440 FK's
  `MOON_ME_DE440_ME421` alignment. The project's documented approximation remains;
  no unsupported rotation is introduced. The FK's close alignment over 2000–2040
  must not be extrapolated into an absolute error guarantee across 1850–2150.
- The high-level interface has less application-specific science code but depends
  on a younger, alpha-classified library and currently unpublished Python release.
  CSPICE/SpiceyPy offer a much longer operational history and wider SPICE support.
- The pinned Siderust Python and Rust packages declare **AGPL-3.0**. The original
  CLPS repository has no declared license in the inspected tree/metadata. Public
  hosting does not establish permission to relicense or redistribute its code.
  No license is added to the authors' code. Before wider distribution or upstream
  adoption, the authors need to resolve their project's licensing/permissions,
  assess applicable dependency and network-use obligations, and agree a compatible
  distribution approach. This evaluation stays in the authorized fork and makes
  no assertion of legal clearance. Third-party kernel/LOLA provenance remains intact.

Siderust is scientifically credible as an **optional** engine for this matching
kernel/geometry configuration. Its strongest demonstrated advantage is moving
rotation, parallax and local observation mechanics into a reusable owned API,
with excellent measured same-model parity. Adoption is justified when that API
reduces scientific code the team otherwise needs to maintain, and when packaging,
license agreement and long-term library support are acceptable. The evidence does
not justify an accuracy claim or an unconditional migration. Retaining the mature
SpiceyPy backend while assessing native deployment is a reasonable outcome.
