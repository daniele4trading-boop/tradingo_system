from __future__ import annotations

import argparse
import sys

from .src.config import load_config


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--stage", required=True)
    run.add_argument("--config", required=True)
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.stage == "s0":
        from .src.stages.s0 import run as run_s0
        run_s0(cfg)
        return 0
    if args.stage == "s1":
        from .src.stages.s1 import run as run_s1
        run_s1(cfg)
        return 0
    if args.stage in {"s1", "s2", "s3", "s4", "s5"}:
        module = __import__(f"sweep_research.src.stages.{args.stage}", fromlist=["run"])
        module.run()
        return 2
    parser.error(f"stage non valido: {args.stage}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
