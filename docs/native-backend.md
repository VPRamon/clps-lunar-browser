# Native lunar observations

CLPS uses published `siderust==0.8.0` and `tempoch==0.5.0`. Install with
`python -m pip install -r requirements.txt`, then run the existing kernel and
terrain setup commands in the README. Wheels were verified on Linux x86-64
CPython 3.11 and 3.14 without a Rust toolchain or SpiceyPy. Other platforms must
have compatible published wheels; `--only-binary=:all:` makes that requirement
explicit. No runtime engine selector or comparison framework is installed.

`sun_earth.py` validates requests, normalizes UTC, converts to TT JD with
`JulianDate.from_utc(...).value`, and calls `LunarObserver.from_spice` followed by
`observe_sun` and `observe_earth`. Native `az_deg`, `alt_deg`, and `above_horizon`
properties populate the existing JSON fields. Native ranges are kilometres;
the API continues to omit ranges. The shared immutable context and its checksum
verification live in `kernel_context.py`; observers are inexpensive per-call owned
handles, with no observer cache. Window timestamps reuse the context.

The pinned DE440s SPK, lunar binary PCK and FK use `MOON_ME`, east-positive
planetocentric longitude/latitude, radial up and north-through-east azimuth.
Positions are geometric body centres, without light-time or aberration correction.
The pinned text PCK has equal 1737.4 km lunar radii, matching the original flat
reference surface. Terrain calculations still use datum radius + DEM elevation
+ observer height. LOLA algorithms, skyline masking, accuracy metadata, mission
summaries, frontend, JSON keys and units remain unchanged.

## Civil time and coverage

CLPS is a modern lunar mission planner; its catalog and mission queries do not
require historical UTC. The API now explicitly rejects normalized UTC before
1972-01-01 with 422. Tempoch models pre-1972 UTC drift differently from the former
NAIF LSK pathway and rejects UTC before 1961. We do not add a custom historical
conversion or claim civil UTC support across the entire approximately 1850–2150
DE440s interval. SPK/BPC coverage remains a separate check performed by Siderust;
coverage errors return 502. Timezone normalization, naive timestamp rejection,
strict hours below 24, minute/fraction handling and window limits are preserved.
Future UTC uses tempoch's known leap offsets; future leap-second changes require
a reviewed dependency update, rather than assuming an exact future UTC history.

Independent accuracy/performance evidence is in [evaluation PR #1](https://github.com/VPRamon/clps-lunar-browser/pull/1).
This production branch retains the independent JPL Horizons cases and tests native
ownership, context reuse, corrupted/missing data and repair, configured directories,
concurrent requests, observer radius and coverage. It does not import that PR's
benchmarks or dual-engine architecture.

## Distribution questions

Siderust declares AGPL-3.0. The original CLPS project has no declared license;
its authors' notices and attribution are retained, and this change does not
relicense their code. Licensing permissions, dependency obligations and a compatible
distribution approach need resolution before upstream or wider distribution.
This migration is confined to the fork. Kernel and LOLA provenance remain intact.
