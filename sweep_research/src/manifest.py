from __future__ import annotations

import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy
import pandas
import pyarrow
import yaml

from .. import __version__


def write_manifest(path: str | Path, cfg, files: dict[str, str], missing_external: dict[str, bool]) -> None:
    try:
        git = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        git = None
    payload = {
        "config": cfg.__dict__, "seed": cfg.seed, "data_sha256": files,
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "cli_command": "python -m sweep_research s0",
        "missing_external": missing_external, "package_version": __version__,
        "versions": {"python": platform.python_version(), "pandas": pandas.__version__,
                     "numpy": numpy.__version__, "pyarrow": pyarrow.__version__,
                     "duckdb": duckdb.__version__, "pyyaml": yaml.__version__},
        "git_commit": git,
    }
    Path(path).write_text(json.dumps(payload, default=lambda x: x.__dict__, indent=2, ensure_ascii=False))
