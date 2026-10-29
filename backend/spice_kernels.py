import os
import stat
from functools import lru_cache
from pathlib import Path
from threading import RLock

from siderust import SpiceContext

from backend.setup_kernels import KERNELS


_CONTEXT_LOCK = RLock()
KERNEL_FILES = tuple(KERNELS)


class KernelError(RuntimeError):
    pass


def _identity(path: Path) -> tuple:
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError("kernel is not a regular file")
    return metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_ino


def kernel_directory() -> Path:
    configured = os.environ.get("SPICE_KERNELS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "kernels"


@lru_cache(maxsize=1)
def _context(directory: Path, identities: tuple) -> SpiceContext:
    from backend.setup_kernels import file_hash
    try:
        for path, _ in identities:
            if file_hash(path) != KERNELS[path.name][1]:
                raise KernelError(f"SPICE kernel {path.name} checksum is incorrect; run python -m backend.setup_kernels")
        context = SpiceContext.from_de440(str(directory))
        if any(_identity(path) != identity for path, identity in identities):
            raise KernelError("SPICE kernel set changed during loading; retry the request")
        return context
    except (ValueError, OSError) as exc:
        raise KernelError(f"Could not load Siderust DE440 context: {exc}") from exc


def spice_context() -> SpiceContext:
    # Serialize cold verification/construction; observations own immutable native state.
    with _CONTEXT_LOCK:
        directory = kernel_directory()
        identities = []
        for filename in KERNEL_FILES:
            path = directory / filename
            try:
                identity = _identity(path)
            except OSError as exc:
                raise KernelError(f"Missing or unreadable SPICE kernel {path.name} in {directory}. See backend/kernels/README.md.") from exc
            identities.append((path, identity))
        return _context(directory, tuple(identities))
