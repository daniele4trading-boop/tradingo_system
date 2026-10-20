from __future__ import annotations

import json
import platform
import resource
import subprocess
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def write_manifest(
    path: str | Path,
    cfg: object,
    inputs: dict[str, str],
    started: float,
    peak_rss: int = 0,
    metadata: dict | None = None,
) -> None:
    if peak_rss <= 0:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    versions = {}
    for package in ("pandas", "numpy", "duckdb", "pyarrow"):
        try:
            module = __import__(package)
            versions[package] = getattr(module, "__version__", "unknown")
        except ImportError:
            versions[package] = "missing"
    try:
        versions["ruff"] = version("ruff")
    except PackageNotFoundError:
        versions["ruff"] = "missing"
    try:
        git = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        git = "unknown"
    payload = {
        "config": vars(cfg),
        "python": platform.python_version(),
        "versions": versions,
        "input_sha256": inputs,
        "git_commit": git,
        "seed": getattr(cfg, "seed", None),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_s": time.time() - started,
        "peak_rss_kb": peak_rss,
    }
    if metadata:
        payload.update(metadata)
    Path(path).write_text(json.dumps(payload, indent=2, default=str))
