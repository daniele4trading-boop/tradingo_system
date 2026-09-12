from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingo_lab.bars import (
    build_bars_from_ticks,
    discover_rate_files,
    discover_tick_files,
    load_rate_files,
    load_tick_files,
    save_bars,
)
from tradingo_lab.config import ensure_storage_dirs, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Build OHLC bars from exported MT5 ticks or rates.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--timeframe", default="1min", help="Pandas resample rule, e.g. 1min, 5min, 15min.")
    parser.add_argument("--source", choices=["auto", "ticks", "rates"], default="auto")
    parser.add_argument("--ticks", nargs="*", help="Optional explicit tick parquet files.")
    parser.add_argument("--rates", nargs="*", help="Optional explicit rates parquet files.")
    parser.add_argument("--rates-timeframe", default="M1", help="Rates partition to use when --source rates/auto.")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_storage_dirs(config)

    bars = None
    source_used = None
    files_used = []

    if args.source in {"auto", "ticks"}:
        tick_files = [Path(path) for path in args.ticks] if args.ticks else discover_tick_files(config)
        if tick_files:
            ticks = load_tick_files(tick_files)
            bars = build_bars_from_ticks(ticks, timeframe=args.timeframe)
            source_used = "ticks"
            files_used = tick_files
        elif args.source == "ticks":
            raise FileNotFoundError(f"No tick parquet files found under {config.storage.ticks_dir / config.symbol}")

    if bars is None and args.source in {"auto", "rates"}:
        rate_files = [Path(path) for path in args.rates] if args.rates else discover_rate_files(config, args.rates_timeframe)
        if not rate_files:
            raise FileNotFoundError(f"No rates parquet files found under {config.storage.rates_dir / config.symbol / args.rates_timeframe.upper()}")
        bars = load_rate_files(rate_files)
        source_used = "rates"
        files_used = rate_files

    if bars is None or bars.empty:
        raise RuntimeError("No bars were built. Check exported MT5 files.")

    output_path = save_bars(config, bars, args.timeframe)
    print(f"Source used: {source_used}")
    print(f"Loaded files: {len(files_used)}")
    print(f"Bars: {len(bars)}")
    print(f"Written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
