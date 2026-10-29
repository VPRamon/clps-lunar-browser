"""Download the documented NAIF kernel set and verify its SHA-256 hashes."""

import argparse
import hashlib
import os
from http.client import HTTPException
from pathlib import Path
import ssl
import tempfile
from urllib.error import URLError
from urllib.request import Request, urlopen

import certifi

BASE = "https://naif.jpl.nasa.gov/pub/naif/"
KERNELS = {
    "naif0012.tls": ("generic_kernels/lsk/naif0012.tls", "678e32bdb5a744117a467cd9601cd6b373f0e9bc9bbde1371d5eee39600a039b"),
    "de440s.bsp": ("generic_kernels/spk/planets/de440s.bsp", "c1c7feeab882263fc493a9d5a5b2ddd71b54826cdf65d8d17a76126b260a49f2"),
    "pck00011.tpc": ("generic_kernels/pck/pck00011.tpc", "3dff7b1dbeceaa01f25467767d3fa25816051c85d162d1edf04acb310ee28bb1"),
    "moon_pa_de440_200625.bpc": ("generic_kernels/pck/moon_pa_de440_200625.bpc", "60cd55aa401ea2ea97360636f567554bfe4e37bb829f901b4460a455dfaf783f"),
    "moon_de440_220930.tf": ("pds/pds4/clps/clps_spice/spice_kernels/fk/moon_de440_220930.tf", "73eb6b216c06a27c3419c4cfeaded7ffce46a714e3d4f9f5142dda51c0710f76"),
}


def kernel_directory() -> Path:
    configured = os.environ.get("SPICE_KERNELS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parent / "kernels"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_kernel(directory: Path, filename: str, remote: str, expected_hash: str) -> None:
    target = directory / filename
    context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(3):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".part", delete=False) as file:
                temporary = Path(file.name)
                request = Request(BASE + remote, headers={"User-Agent": "CLPS-Lunar-Browser/1.0"})
                with urlopen(request, timeout=120, context=context) as response:
                    while chunk := response.read(1024 * 1024):
                        file.write(chunk)
            if file_hash(temporary) != expected_hash:
                raise ValueError(f"SHA-256 mismatch for {filename}; existing file left unchanged")
            temporary.replace(target)
            print(f"Downloaded and verified {filename}")
            return
        except (OSError, ValueError, URLError, HTTPException):
            if attempt == 2:
                raise
            print(f"Retrying {filename} after an incomplete or failed download")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def setup_kernels(directory: Path, check_only: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for filename, (remote, expected_hash) in KERNELS.items():
        target = directory / filename
        if target.is_file() and file_hash(target) == expected_hash:
            print(f"Verified {filename}")
            continue
        if check_only:
            raise ValueError(f"Missing or incorrect kernel: {filename}")
        download_kernel(directory, filename, remote, expected_hash)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=kernel_directory())
    parser.add_argument("--check", action="store_true", help="verify without downloading")
    args = parser.parse_args()
    try:
        setup_kernels(args.directory, args.check)
    except (OSError, ValueError, URLError, HTTPException) as exc:
        parser.exit(1, f"Kernel setup failed: {exc}\n")


if __name__ == "__main__":
    main()
