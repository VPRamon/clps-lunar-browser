import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.calculations.sun_earth import (
    ObservationError, calculate_visibility, calculate_visibility_window,
    parse_utc_time, validate_window_request,
)
from backend.landing_sites import LandingSiteError, get_landing_site, load_landing_sites
from backend.mission_summary import summarize_window
from backend.kernel_context import KernelError
from backend.terrain.global_dem import global_dem, sample_height
from backend.terrain.horizon import HorizonError, apply_horizon
from backend.terrain.service import observer_radius, terrain_metadata, terrain_profile

app = FastAPI(title="CLPS Lunar Browser API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",") if origin.strip()],
    allow_methods=["GET"],
)


@app.exception_handler(HorizonError)
@app.exception_handler(KernelError)
@app.exception_handler(LandingSiteError)
async def unavailable(_request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ObservationError)
async def observation_failure(_request, exc):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid_request(_request, exc):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def _site(site_id: str) -> dict:
    try:
        return get_landing_site(site_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="landing site not found") from exc


def _visibility(lat: float, lon: float, time: str, terrain: bool, site: dict | None = None):
    parse_utc_time(time)
    profile = terrain_profile(lat, lon, site) if terrain else None
    result = calculate_visibility(lat, lon, time, observer_radius(profile))
    result["site"] = site["id"] if site else None
    result.update(terrain_metadata(profile))
    return apply_horizon(result, profile)


def _window(lat: float, lon: float, start: str, end: str, step_minutes: int,
            terrain: bool, site: dict | None = None):
    validate_window_request(lat, lon, start, end, step_minutes)
    profile = terrain_profile(lat, lon, site) if terrain else None
    samples = calculate_visibility_window(lat, lon, start, end, step_minutes, observer_radius(profile))
    for sample in samples:
        apply_horizon(sample, profile)
        sample.update(terrain_metadata(profile))
    return samples, profile


def _summary(lat: float, lon: float, start: str, end: str, step_minutes: int,
             terrain: bool, site: dict | None = None):
    if parse_utc_time(end) <= parse_utc_time(start):
        raise ValueError("summary end must be after start")
    samples, profile = _window(lat, lon, start, end, step_minutes, terrain, site)
    result = summarize_window(site["id"] if site else None, samples, start, end, step_minutes)
    result.update(latitude=lat, longitude=lon, **terrain_metadata(profile))
    result["sampling"] = {"method": "left sample held to next cadence or requested end",
                          "step_minutes": step_minutes, "transition_error_minutes": None,
                          "continuous_visibility_validated": False}
    return result


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/ready")
def ready() -> dict:
    """Check real ephemeris calculations, the global DEM, and named profiles."""
    try:
        calculate_visibility(0, 0, "2026-10-03T00:00:00Z")
        grid, _ = global_dem()
        sample_height(grid, 0, 0)
        for site in load_landing_sites():
            terrain_profile(site["latitude"], site["longitude"], site)
        return {"ok": True}
    except (KernelError, ObservationError, HorizonError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/sites")
def landing_sites() -> list[dict]:
    return load_landing_sites()


@app.get("/api/sites/{site_id}/horizon")
def site_horizon(site_id: str) -> dict:
    site = _site(site_id)
    profile = terrain_profile(site["latitude"], site["longitude"], site)
    return {"site": site_id, "terrain_available": True, "profile": profile}


@app.get("/api/horizon")
def coordinate_horizon(lat: float = Query(..., ge=-90, le=90),
                       lon: float = Query(..., ge=-180, le=180)) -> dict:
    return {"site": None, "terrain_available": True, "profile": terrain_profile(lat, lon)}


@app.get("/api/sites/{site_id}/visibility")
def site_visibility(site_id: str, time: str = Query(...), terrain: bool = Query(True)) -> dict:
    site = _site(site_id)
    return _visibility(site["latitude"], site["longitude"], time, terrain, site)


@app.get("/api/sites/{site_id}/visibility/window")
def site_visibility_window(site_id: str, start: str = Query(...), end: str = Query(...),
                           step_minutes: int = Query(30, ge=1), terrain: bool = Query(True)) -> list[dict]:
    site = _site(site_id)
    return _window(site["latitude"], site["longitude"], start, end, step_minutes, terrain, site)[0]


@app.get("/api/sites/{site_id}/summary")
def site_summary(site_id: str, start: str = Query(...), end: str = Query(...),
                 step_minutes: int = Query(30, ge=1), terrain: bool = Query(True)) -> dict:
    site = _site(site_id)
    return _summary(site["latitude"], site["longitude"], start, end, step_minutes, terrain, site)


@app.get("/api/visibility")
def visibility(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
               time: str = Query(...), terrain: bool = Query(True)) -> dict:
    return _visibility(lat, lon, time, terrain)


@app.get("/api/visibility/window")
def visibility_window(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                      start: str = Query(...), end: str = Query(...),
                      step_minutes: int = Query(30, ge=1), terrain: bool = Query(True)) -> list[dict]:
    return _window(lat, lon, start, end, step_minutes, terrain)[0]


@app.get("/api/summary")
def coordinate_summary(lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180),
                       start: str = Query(...), end: str = Query(...),
                       step_minutes: int = Query(30, ge=1), terrain: bool = Query(True)) -> dict:
    return _summary(lat, lon, start, end, step_minutes, terrain)
