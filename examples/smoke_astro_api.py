"""Exercise both engines over real HTTP/Uvicorn, with verified local datasets."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx


def run(engine):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app:app",
            "--fd",
            str(sock.fileno()),
            "--log-level",
            "error",
        ],
        pass_fds=(sock.fileno(),),
        env={**os.environ, "ASTRO_BACKEND": engine},
    )
    sock.close()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120) as client:
            for _ in range(100):
                try:
                    client.get("/health").raise_for_status()
                    break
                except httpx.TransportError:
                    if process.poll() is not None:
                        raise RuntimeError("Uvicorn exited")
                    time.sleep(0.05)
            else:
                raise RuntimeError("Uvicorn did not start")
            records = []
            requests = [
                ("/ready", {}),
                ("/api/sites", {}),
                ("/api/sites/athena-im2/visibility", {"time": "2026-10-03T18:00:00Z"}),
                (
                    "/api/sites/odysseus-im1/visibility",
                    {"time": "2026-10-03T18:00:00Z", "terrain": False},
                ),
                (
                    "/api/visibility",
                    {"lat": 12, "lon": 179.9, "time": "2026-10-03T18:00:00Z"},
                ),
                (
                    "/api/sites/athena-im2/visibility/window",
                    {"start": "2026-10-03T00:00:00Z", "end": "2026-10-04T00:00:00Z"},
                ),
                (
                    "/api/sites/athena-im2/summary",
                    {"start": "2026-10-03T00:00:00Z", "end": "2026-10-04T00:00:00Z"},
                ),
                ("/api/horizon", {"lat": 12, "lon": 179.9}),
            ]
            for path, params in requests:
                start = time.perf_counter()
                response = client.get(path, params=params)
                response.raise_for_status()
                data = response.json()
                records.append(
                    {
                        "path": path,
                        "status": response.status_code,
                        "latency_ms": (time.perf_counter() - start) * 1000,
                        "response_keys": sorted(data)
                        if isinstance(data, dict)
                        else None,
                    }
                )
            bad = client.get(
                "/api/visibility",
                params={"lat": 0, "lon": 0, "time": "2026-10-03", "terrain": False},
            )
            assert bad.status_code == 422
            return records
    finally:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    print(
        json.dumps(
            {engine: run(engine) for engine in ("spiceypy", "siderust")}, indent=2
        )
    )
