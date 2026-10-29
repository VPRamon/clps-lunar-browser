# SPICE kernels

Run `python -m backend.setup_kernels` from the repository root to download these NAIF kernels into this folder. The command verifies SHA-256 hashes recorded from NAIF on October 6, 2026, skips verified files, and replaces files only after a successful download and hash check. Use `--check` for offline verification, or `--directory /path/to/kernels` for another folder. CI uses this same command. Kernel files should not be committed.

- [`naif0012.tls`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls) — retained as part of the reproducible context (civil UTC conversion uses tempoch)
- [`de440s.bsp`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp) — Sun, Earth, and Moon positions
- [`pck00011.tpc`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc) — lunar radii and other body constants
- [`moon_pa_de440_200625.bpc`](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/moon_pa_de440_200625.bpc) — high accuracy lunar orientation
- [`moon_de440_220930.tf`](https://naif.jpl.nasa.gov/pub/naif/pds/pds4/clps/clps_spice/spice_kernels/fk/moon_de440_220930.tf) — lunar `MOON_ME` frame definitions

The backend loads these files on the first visibility request. By default, it looks here. To use another folder, set `SPICE_KERNELS_DIR` to its path.

Runtime loading checks the pinned SHA-256 hashes before creating a Siderust
`SpiceContext.from_de440`. One immutable context is cached per process, keyed by
configured directory and every file's size, modification/change times and inode.
Each request checks identities; changes require full verification and a new
context. Invalid or unreadable files return 503, including with a warm cache.
Construction checks identities again after reading to reject files changed during
loading. Directory switching evicts the cached context; in-flight observers keep
owned native state. No global CSPICE pool or request-time downloads are used.

The authenticated PCK has three equal lunar radii of 1737.4 km, so flat mode uses
a 1,737,400 m radial observer. This equality is specific to the pinned manifest;
arbitrary kernels or ellipsoidal coordinates are not supported. Terrain mode
adds DEM elevation and instrument height to the terrain datum radius.
UTC-to-TT conversion and [coverage limits](../../docs/native-backend.md) are
independent of the SPK/BPC coverage.

Start the API from the repository root with:

```sh
uvicorn backend.app:app --reload
```
