from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import Config
from ..data import list_data_files, read_bars, sha256_files, tick_loader
from ..leakage import check_leakage
from ..manifest import write_manifest
from ..outcomes import add_outcomes
from ..pipeline import compute_s0
from ..reports import write_reports
from ..ticks import build_tick_panel


def _rss_mb() -> float:
    try:
        with Path("/proc/self/status").open() as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (FileNotFoundError, ValueError):
        pass
    return float("nan")


def _inventory_entry(frame: pd.DataFrame, files: int, missing: bool = False) -> dict[str, Any]:
    timestamps = pd.to_datetime(frame["ts"]) if "ts" in frame else pd.Series(dtype="datetime64[ns]")
    return {
        "rows": len(frame),
        "min_ts": timestamps.min() if not timestamps.empty else None,
        "max_ts": timestamps.max() if not timestamps.empty else None,
        "files": files,
        "missing": missing,
    }


def run(cfg: Config) -> pd.DataFrame:
    np.random.seed(cfg.seed)
    started = time.perf_counter()
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    bars: dict[str, pd.DataFrame] = {}
    for tf in [*cfg.timeframes, "M1"]:
        bars[tf] = read_bars(cfg, cfg.symbol, tf, cfg.start, cfg.end, warmup_days=30)
        bars[tf].attrs["tf_minutes"] = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[tf]
    print(f"load_bars: {time.perf_counter() - started:.2f}s rss_mb={_rss_mb():.1f}", flush=True)
    tick_path = output / "tick_m1_panel.parquet"
    panel, tick_days_present = build_tick_panel(cfg, cfg.start, cfg.end, tick_path)
    print(
        f"tick_panel: {time.perf_counter() - started:.2f}s "
        f"({len(panel)} M1 rows, {len(tick_days_present)} days) rss_mb={_rss_mb():.1f}",
        flush=True,
    )
    external: dict[str, pd.DataFrame] = {}
    for symbol in [cfg.external.dxy, cfg.external.ust_proxy, cfg.external.silver]:
        external[symbol] = read_bars(cfg, symbol, "M1", cfg.start, cfg.end, warmup_days=1)
    inventory = {
        f"{cfg.symbol}/{tf}": _inventory_entry(
            bars[tf], len(list_data_files(cfg, cfg.symbol, tf, cfg.start, cfg.end))
        )
        for tf in [*cfg.timeframes, "M1"]
    }
    inventory[f"{cfg.symbol}/ticks"] = _inventory_entry(panel, len(tick_days_present))
    for symbol, frame in external.items():
        inventory[f"{symbol}/M1"] = _inventory_entry(
            frame, len(list_data_files(cfg, symbol, "M1", cfg.start, cfg.end)), frame.empty
        )
    print(f"external_loaded: rss_mb={_rss_mb():.1f}", flush=True)
    bundle = {
        "bars": {tf: bars[tf] for tf in cfg.timeframes},
        "m1": bars["M1"],
        "tick_m1_panel": panel,
        "external": external,
        "ticks_by_day": tick_loader(cfg, cfg.symbol, cfg.start, cfg.end),
    }
    events = compute_s0(bundle, cfg)
    print(f"causal_features: rss_mb={_rss_mb():.1f}", flush=True)
    missing = {symbol: frame.empty for symbol, frame in external.items()}
    events = add_outcomes(events, bars["M1"], cfg)
    print(f"outcomes: rss_mb={_rss_mb():.1f}", flush=True)
    if not events.empty:
        events = events.sort_values("event_ts_utc").reset_index(drop=True)
        events["event_id"] = range(len(events))
    events.to_parquet(output / "events.parquet", compression="zstd", index=False)
    leakage = check_leakage(bundle, cfg, events)
    print(f"leakage: rss_mb={_rss_mb():.1f}", flush=True)
    (output / "leakage.json").write_text(json.dumps(leakage, indent=2, ensure_ascii=False))
    (output / "missing_external.json").write_text(json.dumps(missing, indent=2, ensure_ascii=False))
    (output / "inventory.json").write_text(
        json.dumps(inventory, default=str, indent=2, ensure_ascii=False)
    )
    write_manifest(output / "manifest.json", cfg, sha256_files(
        cfg, [cfg.symbol, *external], [*cfg.timeframes, "M1"], cfg.start, cfg.end
    ),
                   missing)
    counts = events.groupby(["tf", "level_type"]).size().to_dict() if not events.empty else {}
    (output / "s0_summary.json").write_text(json.dumps({str(k): v for k, v in counts.items()}, indent=2))
    write_reports(events, cfg, leakage, missing, inventory)
    print(f"outcomes_reports: {time.perf_counter() - started:.2f}s rss_mb={_rss_mb():.1f}", flush=True)
    for key, count in counts.items():
        print(f"events tf={key[0]} level_type={key[1]} count={count}", flush=True)
    print(f"total_events={len(events)} total_seconds={time.perf_counter() - started:.2f}", flush=True)
    return events
