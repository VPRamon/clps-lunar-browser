# Shared API format

All endpoints are GET requests. Latitude is planetocentric, -90..90°, and longitude is east-positive, -180..180°, in lunar `MOON_ME`. Timestamps must include a timezone; outputs use UTC. Azimuth is clockwise from north, and elevation is above the local radial tangent plane, in degrees.

## Endpoints

| Feature | Route | Required parameters |
| --- | --- | --- |
| CLPS site list | `/api/sites` | none |
| Coordinate visibility | `/api/visibility` | `lat`, `lon`, `time` |
| Coordinate timeline | `/api/visibility/window` | `lat`, `lon`, `start`, `end` |
| Coordinate planning summary | `/api/summary` | `lat`, `lon`, `start`, `end` |
| Coordinate skyline | `/api/horizon` | `lat`, `lon` |
| Named-site visibility | `/api/sites/{site_id}/visibility` | `time` |
| Named-site timeline | `/api/sites/{site_id}/visibility/window` | `start`, `end` |
| Named-site summary | `/api/sites/{site_id}/summary` | `start`, `end` |
| Named-site skyline | `/api/sites/{site_id}/horizon` | none |

Timeline and summary endpoints accept `step_minutes` (positive integer, default 30). Visibility, timeline and summary endpoints accept `terrain` (boolean, **default true**). Set `terrain=false` to explicitly request the original flat-horizon calculation. This applies to named sites as well as coordinates.

## Terrain selection and visibility

Terrain applies worldwide, including Odysseus, both poles and the longitude seam. Exact listed Athena and Odysseus coordinates use checked-in 20 m and 30 m local LOLA profiles through both named and coordinate routes. Their local 40 km skylines are combined with global outer terrain out to 300 km. Other positions use global 64-pixel/degree LOLA terrain out to 300 km, about 474 m north/south (east/west spacing narrows toward the poles). Exact custom coordinate profiles can be prepared offline; ambiguous matches return 503. Requests never silently substitute a flat horizon for missing terrain.

For terrain mode, SPICE body directions and the skyline use the same observer radius: 1,737,400 m plus DEM site elevation plus an assumed 2 m instrument height. Sun and Earth positions are geometric at the requested timestamp, with no light-time correction. `visible` compares body-centre elevation with the interpolated terrain skyline; `flat_visible` compares the same direction with 0°. For flat mode, the observer is at zero height on the kernel reference surface and only `azimuth`, `elevation`, `visible` appear in each body record.

A visibility response has this structure (angles below are illustrative):

```json
{
  "site": null,
  "latitude": 12,
  "longitude": 179.9,
  "time": "2026-10-15T00:00:00Z",
  "horizon_mode": "terrain",
  "terrain": {
    "site_id": "coordinates",
    "latitude": 12,
    "longitude": 179.9,
    "source": "https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_64_uint.tif",
    "source_frame": "MOON_ME_DE421",
    "runtime_frame": "MOON_ME_DE440_ME421",
    "datum_radius_m": 1737400,
    "site_elevation_m": 1000,
    "observer_height_m": 2,
    "pixel_resolution_m": 473.802,
    "pixels_per_degree": 64,
    "max_distance_m": 300000,
    "min_distance_m": 335.029,
    "azimuth_step": 0.5
  },
  "sun": {"azimuth": 90, "elevation": 3, "flat_visible": true, "terrain_horizon": 5, "visible": false},
  "earth": {"azimuth": 180, "elevation": 8, "flat_visible": true, "terrain_horizon": 2, "visible": true}
}
```

`site` is null for coordinates and the site ID for named sites. `terrain` includes provenance and model limits, without the large skyline arrays; flat mode returns `terrain: null` and `horizon_mode: "flat"`.

## Timelines and summaries

A timeline returns records with `time`, `sun`, `earth`, `horizon_mode` and `terrain`, with the same body and terrain fields as single-time visibility. Samples begin at `start`; `end` is included only if on the sampling grid. Windows allow `end == start`, but summaries require `end > start`. At most 2,000 samples are allowed.

Summaries return `site`, `latitude`, `longitude`, `start`, `end`, `step_minutes`, `horizon_mode`, `terrain`, `sunlight_percent`, `earth_visible_percent`, `both_available_percent`, `longest_darkness_minutes`, `longest_comm_blackout_minutes`, and `windows` for `sunlight`, `earth_visible`, and `both`. Each window has `start` and `end`.

Each sample describes the interval from its timestamp to the next step, or the requested end if sooner. The sample exactly at end contributes no duration. Boundaries are approximate at the requested sampling cadence; crossing times are not refined. Summary `sampling` records this method, the cadence, `transition_error_minutes: null` and `continuous_visibility_validated: false`.

```text
/api/summary?lat=-80.13&lon=1.44&start=2026-10-15T00:00:00Z&end=2026-10-16T00:00:00Z&step_minutes=30
```

## Skyline and readiness

Skyline endpoints return `{site, terrain_available: true, profile}`. The profile is the data used for masking, with provenance, range, resolution, observer height, `horizon`, `peak_distance_m`, and `azimuth_step`. `horizon[i]` is elevation at azimuth `i * azimuth_step`; lookup interpolates across adjacent bins and wraps 360° to 0°. The global profile is generated from surrounding raster cells and cached for 64 exact coordinate pairs per process. The DEM is memory-mapped and processed in bounded stripes, avoiding a full raster copy per request. Replacing the dataset invalidates cached profiles.

Checked-in named profiles are also reloaded when their file changes. Invalid replacements return 503 rather than continuing to serve a stale skyline. Profile geometry must use finite JSON numbers; numeric strings and booleans are rejected.

File identity includes change time and, for the global DEM, inode, so replacing the raster with the same size and modification time still triggers verification. Cached terrain is copied for each caller to prevent one request's edits from changing later results. At exact raster cell centres, zero-weight missing neighbours do not invalidate a valid observer elevation; missing cells that contribute to interpolation or terrain masking still fail.

`/health` checks that the API responds. `/ready` checks a real SPICE calculation, the global DEM and all configured named profiles. Readiness does not guarantee kernel coverage at every requested date.

| Error | Status |
| --- | --- |
| Invalid coordinates, timestamp, range or sampling step | 422 |
| Unknown named site | 404 |
| Missing/corrupt kernels, global DEM, terrain profile or landing-site catalog | 503 |
| SPICE calculation failure (including unsupported dates) | 502 |

Local Vite origins `http://localhost:5173` and `http://127.0.0.1:5173` are allowed. Configure comma-separated `CORS_ORIGINS` before startup for other origins; an empty string disables CORS.

## Accuracy checks and limitations

Five reference-surface cases in `tests/test_spice_calculations.py` compare equatorial, mid-latitude and south-pole results against saved [JPL Horizons](https://ssd-api.jpl.nasa.gov/doc/horizons.html) topocentric observations. The apparent DE441 Horizons directions differ slightly from our geometric DE440 directions; the tolerance is 0.02°. Terrain observer geometry, curvature, ridge directions, both poles, missing data and longitude wrap are tested separately.

Terrain extends to 300 km. Excluded inner radii follow pixel half-diagonals: about 335 m globally, 14 m for Athena and 21 m for Odysseus. Small landforms and inner-radius obstructions remain unresolved. `outer_terrain` records the separate global resolution for detailed profiles. Cell footprints conservatively contribute to overlapping 0.5° bins. Global elevations are gridded/interpolated LOLA data, not a high-resolution survey at every location. Site coordinates and lunar frame alignment introduce additional uncertainty. See [terrain sources and limits](../backend/terrain/README.md).

Earth visibility represents geometric line of sight, not link availability; sunlight visibility does not calculate solar power. The frontend's Moon GLB and illustrative lighting remain separate from the backend's elevation data and SPICE calculations.


## Accuracy fields

All runtime profiles include `accuracy` with `status: "unvalidated"`, `survey_grade: false`, `horizontal_error_m: null`, `vertical_error_m: null`, `horizon_error_deg: null`, `independent_control_points: 0`, `coordinate_rounding` and `limits`. Null means unknown, not zero. Catalog rounding displacement assumes rounding to the stated increment; it is not a measured position error.

Terrain body records additionally include `terrain_clearance_deg`, `visibility_validation: "unvalidated"` and `distant_raster_may_block`. The existing `visible` Boolean remains a model prediction. `distant_terrain_ceiling_deg` bounds omitted distant cell centres from the pinned raster's maximum represented height, including range-edge overlap; `distant_raster_bounded` means that ceiling is below every skyline bin. It cannot certify unresolved terrain or source/position error. Do not interpret `distant_raster_may_block: false` as independently validated visibility.

Independent-reference residual comparisons are available offline through `python -m backend.terrain.validate_accuracy`; see the terrain documentation for inputs, explicit tolerances and limits. No survey-grade certification is implied by passing tests or observed controls.

## Astronomical engine selection (evaluation branch)

Set `ASTRO_BACKEND=spiceypy` (default) or `ASTRO_BACKEND=siderust` before starting
Uvicorn. Both engines preserve the response contract above and use the same
terrain service. Invalid configuration or unavailable native bindings returns
503; calculation/coverage failures return 502. No fallback occurs. Siderust uses
aware UTC → tempoch TT Julian Date → native TDB observations. Its UTC path rejects
dates before 1961 and differs from naif0012's historical UTC convention before
1972; exact ET parity is tested separately. Both frozen leap-second models assume
no additional leap seconds beyond their tables. Python's datetime parser rejects
explicit leap-second timestamps. The authenticated PCK's lunar radii are equal,
so its zero-height reference surface is the same 1737400 m radial sphere used by
the native adapter; this equality must be rechecked before changing the kernel
manifest. See [scientific conventions and results](siderust-evaluation.md).
