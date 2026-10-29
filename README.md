# CLPS Lunar Browser

we're building this for NASA Space Apps 2026

basic idea is u pick a lunar landing spot + date/time and it shows where the sun and earth are from that spot. from there we can tell if theres sunlight for power and if earth is visible for comms.

## what we're tryna make

- moon map + landing site picker
- date/time controls
- sun + earth position calculations
- horizon view so u can actually see where they are
- sunlight / comm timelines
- terrain blocking so crater rims and stuff actually matter
- real CLPS mission + landing site data

## who's doing what rn

- **suhas + rishi** - backend + sun/earth calculations
- **tap + hemanth** - website/frontend
- **dip** - horizon + timeline visualizations
- **srikar** - terrain stuff + testing/integration

not super strict tho, if someone finishes their part just help wherever

## repo layout

```
backend/        sun/earth calc + api
frontend/       main site
visualization/  horizon + timeline stuff
terrain/        terrain/horizon processing
data/           landing sites + mission data
docs/           shared api format
```

## main flow

```
landing spot + date/time
        ↓
backend calculates sun + earth position
        ↓
terrain checks if either one is blocked
        ↓
frontend shows the result
        ↓
visualizations make it easy to understand
```

## backend output

keeping one shared format so frontend/visualization can work before the full backend is done

```json
{
  "site": "Shackleton Rim",
  "latitude": -89.5,
  "longitude": 135.0,
  "time": "2026-10-03T18:00:00Z",
  "sun": {
    "azimuth": 124.2,
    "elevation": 3.7,
    "visible": true
  },
  "earth": {
    "azimuth": 241.8,
    "elevation": 7.1,
    "visible": true
  }
}
```

## working on it

make ur own branch for your part and PR it into main when its ready. try not to directly edit main unless its something tiny.

## backend is ready for integration

Sun/Earth directions, visibility windows, CLPS site lookup, terrain horizons, and planning summaries are implemented for named sites and any selected lunar coordinate. Terrain is enabled by default: Athena and Odysseus use detailed 20 m and 30 m LOLA profiles with global terrain extending the skyline to 300 km; other positions use global LOLA elevation data. Responses report unresolved accuracy rather than claiming survey certification. The display model and its illustrative lighting are separate from these calculations.

From the repo root, with Python 3.11 or newer (tested on 3.11 and 3.14):

```sh
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m backend.setup_kernels
python -m backend.setup_terrain
python -m pytest -q
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs` to try the API. `/ready` verifies a real SPICE calculation, global elevation data and named terrain profiles; `/health` just checks that the server responds. Kernels are roughly 45 MB and the global DEM is 506 MiB; downloads are SHA-256 verified and remain untracked. Both setup commands accept `--check` for offline verification. Set `LUNAR_DEM_PATH` to store the DEM elsewhere. Add `terrain=false` to visibility or summary requests for an explicit flat-horizon estimate.

Frontend requests from `http://localhost:5173` and `http://127.0.0.1:5173` are allowed by default. For another frontend origin, set `CORS_ORIGINS` to a comma-separated list before starting the API. An empty value disables cross-origin access. Same-origin `/api` proxying also works.

See [the frontend handoff](docs/backend-handoff.md), [API contract](docs/api.md), and [terrain source and limits](backend/terrain/README.md).

The backend uses published `siderust==0.8.0` and `tempoch==0.5.0` wheels;
no Rust build is needed on supported wheel platforms. Siderust owns geometric
lunar observations; CLPS retains terrain processing and the existing API.
UTC inputs start at 1972-01-01; finite kernel coverage is a separate constraint.
See [native backend conventions and limits](docs/native-backend.md), including
historical UTC and the licensing questions to resolve before wider distribution.
