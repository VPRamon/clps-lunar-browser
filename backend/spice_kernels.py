import os
import stat
from functools import lru_cache
from pathlib import Path
from threading import RLock

import spiceypy
from spiceypy.utils.exceptions import SpiceyError


SPICE_LOCK = RLock()
KERNEL_FILES = (
    "naif0012.tls",
    "de440s.bsp",
    "pck00011.tpc",
    "moon_pa_de440_200625.bpc",
    "moon_de440_220930.tf",
)
_loaded_paths: dict[Path, tuple] = {}


class KernelError(RuntimeError):
    pass


def _identity(path: Path) -> tuple:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("kernel is not a regular file")
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_ino


@lru_cache(maxsize=32)
def _verify_kernel(path: Path, identity: tuple, expected_hash: str) -> None:
    from backend.setup_kernels import file_hash
    try:
        if file_hash(path) != expected_hash:
            raise KernelError(f"SPICE kernel {path.name} checksum is incorrect; run python -m backend.setup_kernels")
        if _identity(path) != identity:
            raise KernelError(f"SPICE kernel {path.name} changed during verification; retry the request")
    except OSError as exc:
        raise KernelError(f"Could not verify SPICE kernel {path.name}: {exc}") from exc


def kernel_directory() -> Path:
    configured = os.environ.get("SPICE_KERNELS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "kernels"


def load_spice_kernels() -> None:
    with SPICE_LOCK:
        directory, identities = verified_kernel_set()
        paths = list(identities)

        try:
            loaded = {Path(spiceypy.kdata(i, "ALL", fillen=4096)[0]).resolve()
                      for i in range(spiceypy.ktotal("ALL"))}
            # Keep only this backend's selected set; do not accumulate handles
            # when switching directories or unload unrelated application data.
            for previous in list(_loaded_paths):
                if previous not in identities:
                    if previous in loaded:
                        spiceypy.unload(str(previous))
                    del _loaded_paths[previous]
            pool_ready = all(spiceypy.expool(name) for name in (
                "DELTET/DELTA_T_A", "DELTET/DELTA_AT", "BODY301_RADII", "FRAME_MOON_ME",
            ))
            for path in paths:
                if pool_ready and path in loaded and _loaded_paths.get(path) == identities[path]:
                    continue
                if path in loaded:
                    spiceypy.unload(str(path))
                spiceypy.furnsh(str(path))
                if _identity(path) != identities[path]:
                    raise KernelError(f"SPICE kernel {path.name} changed during loading; retry the request")
                _loaded_paths[path] = identities[path]
        except (SpiceyError, OSError) as exc:
            raise KernelError(f"Could not load SPICE kernel set: {exc}") from exc


def verified_kernel_set() -> tuple[Path, dict]:
    """Authenticate the configured set without touching CSPICE global state."""
    from backend.setup_kernels import KERNELS
    directory = kernel_directory()
    paths = [(directory / filename).resolve() for filename in KERNEL_FILES]
    identities = {}
    for path in paths:
        try:
            identities[path] = _identity(path)
        except OSError as exc:
            raise KernelError(f"Missing or unreadable SPICE kernel {path.name} in {directory}. See backend/kernels/README.md.") from exc
        _verify_kernel(path, identities[path], KERNELS[path.name][1])

    return directory, identities
