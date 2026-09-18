from __future__ import annotations

import argparse
from dataclasses import replace

from .src.config import load_config


def _apply_overrides(cfg, overrides: list[str]):
    for assignment in overrides:
        key, value = assignment.split("=", 1)
        if not hasattr(cfg, key):
            raise ValueError(f"unknown config override: {key}")
        current = getattr(cfg, key)
        if isinstance(current, bool):
            parsed = value.lower() in {"1", "true", "yes", "on"}
        elif isinstance(current, int):
            parsed = int(value)
        elif isinstance(current, float):
            parsed = float(value)
        else:
            parsed = value
        cfg = replace(cfg, **{key: parsed})
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--stage", required=True)
    run.add_argument("--config", required=True)
    run.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args()
    cfg = _apply_overrides(load_config(args.config), args.set)
    if args.stage == "count":
        from .src.stages.count import run

        run(cfg)
        return 0
    if args.stage == "s0":
        from .src.stages.s0 import run

        run(cfg)
        return 0
    parser.error(f"stage non valido: {args.stage}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
