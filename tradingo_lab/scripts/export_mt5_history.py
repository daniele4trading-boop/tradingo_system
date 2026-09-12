from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingo_lab.config import ensure_storage_dirs, load_config
from tradingo_lab.mt5_exporter import (
    connect_mt5,
    export_rates,
    export_symbol_metadata,
    export_ticks,
    get_connection_info,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export XAUUSD history from an open XM MT5 terminal.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--start", help="UTC start date, e.g. 2026-01-01 or 2026-01-01T08:00:00")
    parser.add_argument("--end", help="UTC end date. Date-only values are treated as exclusive next day.")
    parser.add_argument("--days", type=int, default=7, help="Used when --start/--end are not provided.")
    parser.add_argument("--ticks", action="store_true", help="Export tick history with copy_ticks_range.")
    parser.add_argument("--rates", action="store_true", help="Export M1/rates history with copy_rates_range.")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_storage_dirs(config)
    start, end = resolve_window(args.start, args.end, args.days)
    export_ticks_flag = args.ticks or not args.rates
    export_rates_flag = args.rates or not args.ticks

    mt5 = connect_mt5(config)
    try:
        print(f"Connected: {get_connection_info(mt5)}")
        metadata_path = export_symbol_metadata(mt5, config)
        print(f"Metadata: {metadata_path}")

        if export_ticks_flag:
            tick_paths = export_ticks(mt5, config, start, end)
            print(f"Tick files written: {len(tick_paths)}")
            for path in tick_paths:
                print(f"  {path}")

        if export_rates_flag:
            rates_path = export_rates(mt5, config, start, end)
            print(f"Rates file written: {rates_path}")
    finally:
        mt5.shutdown()

    return 0


def resolve_window(start_arg: str | None, end_arg: str | None, days: int) -> tuple[datetime, datetime]:
    if start_arg or end_arg:
        if not start_arg or not end_arg:
            raise ValueError("--start and --end must be provided together")
        return parse_utc(start_arg, is_end=False), parse_utc(end_arg, is_end=True)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def parse_utc(value: str, is_end: bool) -> datetime:
    date_only = len(value) == 10
    if date_only:
        parsed = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return parsed + timedelta(days=1) if is_end else parsed
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
