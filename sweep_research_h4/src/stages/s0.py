from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from ..bars import aggregate_h4
from ..config import Config, resolve_macro_calendar
from ..data import list_m1_files, read_h4, read_m1_with_stats, sha256_files
from ..events import dedup_events, detect_events
from ..features import add_features
from ..leakage import run_leakage, write_leakage
from ..manifest import write_manifest
from ..outcomes import compute_outcomes, drift_table
from ..schema import write_schema
from .count import _symbol_count


def _external(cfg: Config) -> dict[str, pd.DataFrame]:
    out = {}
    for name, spec in {
        "dxy": cfg.external.dxy,
        "ust": cfg.external.real_yield_proxy,
    }.items():
        symbol = spec.get("symbol")
        if symbol:
            frame = read_h4(cfg, symbol)
            if not frame.empty:
                out[name] = frame
    return out


def _counts_markdown(cfg: Config, summary: dict, old: dict, dropped: dict[str, int]) -> str:
    lines = [
        "# H4 S0 counts",
        "",
        "Final population is `dedup ∧ ¬below_min_penetration`, with equal-level tolerance 0.15 ATR.",
        "",
        "`COUNTS_step1_tol005.md` is a historical pre-cleaning report. The tol 0.05 "
        "comparison below is recomputed on the cleaned M1 data.",
        "",
        "XAGUSD uses `symbol_period_start=2022-08-01`; 2015-02→2022-07 M1 history "
        "is unavailable in the warehouse although remote tick sync completed.",
        "",
        "## Pulizia M1",
        "",
        "Configured `drop_flat_zero_volume_m1=true`; rows are dropped only when "
        "`volume == 0 AND high == low`.",
        "",
        "| Symbol | Rows before | Rows dropped | Dropped % | H4 bars with n_m1 < 30 |",
        "|---|---:|---:|---:|---:|",
    ]
    for symbol, row in summary["symbols"].items():
        lines.append(
            f"| {symbol} | {row['m1_rows_before']} | {row['m1_rows_dropped']} | "
            f"{row['m1_drop_pct']:.3f}% | {row['h4_bars_n_m1_lt_30']} |"
        )
    lines.extend(
        [
            "",
            "Weekday distribution after filtering (`0=Monday`, ..., `6=Sunday`):",
            "",
            "| Symbol | Mon | Tue | Wed | Thu | Fri | Sat | Sun |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for symbol, row in summary["symbols"].items():
        weekday = row["m1_weekday_after"]
        lines.append(
            f"| {symbol} | {weekday.get('0', 0)} | {weekday.get('1', 0)} | "
            f"{weekday.get('2', 0)} | {weekday.get('3', 0)} | {weekday.get('4', 0)} | "
            f"{weekday.get('5', 0)} | {weekday.get('6', 0)} |"
        )
    lines.extend(
        [
            "",
            "| Symbol | Source | M1 files | H4 bars | Raw | Dedup | Post-threshold | "
            "Dropped no next bar | Mean exec cost bp | Range |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for symbol, row in summary["symbols"].items():
        lines.append(
            f"| {symbol} | {row['source']} | {row['files']} | {row['n_h4']} | {row['raw']} | "
            f"{row['dedup']} | {row['post_threshold']} | {dropped.get(symbol, 0)} | "
            f"{row['mean_exec_cost_bp']:.6f} | {row['range_start']} → {row['range_end']} |"
        )
    lines.extend(
        [
            "",
            "## Equal tolerance comparison",
            "",
            "| Symbol | Post-threshold tol 0.05 | Equal 0.05 | Post-threshold tol 0.15 | Equal 0.15 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for symbol in cfg.symbols:
        old_equal = old["symbols"][symbol]["level_type"].get("equal_high", 0) + old["symbols"][symbol][
            "level_type"
        ].get("equal_low", 0)
        new_equal = summary["symbols"][symbol]["level_type"].get("equal_high", 0) + summary["symbols"][
            symbol
        ]["level_type"].get("equal_low", 0)
        lines.append(
            f"| {symbol} | {old['symbols'][symbol]['post_threshold']} | {old_equal} | "
            f"{summary['symbols'][symbol]['post_threshold']} | {new_equal} |"
        )
    lines.extend(["", "## Post-threshold by year and level_type", ""])
    for symbol, row in summary["symbols"].items():
        lines.extend(
            [
                f"### {symbol}",
                "",
                "| Year | Events | Median ATR pts | Median ATR / close bp |",
                "|---:|---:|---:|---:|",
            ]
        )
        for year, count in row["year"].items():
            stats = row["year_stats"].get(year, {})
            lines.append(
                f"| {year} | {count} | {stats.get('atr_median', float('nan')):.6f} | "
                f"{stats.get('atr_close_bp', float('nan')):.6f} |"
            )
        lines.extend(["", "| Level type | Count |", "|---|---:|"])
        lines.extend(f"| {level} | {count} |" for level, count in row["level_type"].items())
        lines.append("")
    lines.append(f"GATE: **{'PASS' if summary['gate']['pass'] else 'FAIL'}** — {summary['gate']['reason']}")
    return "\n".join(lines) + "\n"


def run(cfg: Config) -> dict:
    started = time.time()
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    old_cfg = replace(cfg, equal_level_tol_atr=0.05)
    old = {"symbols": {s: _symbol_count(old_cfg, s) for s in cfg.symbols}}
    external = _external(cfg)
    m1_stats = {}
    all_bars = {}
    for symbol in cfg.symbols:
        m1_frame, m1_stats[symbol] = read_m1_with_stats(cfg, symbol)
        all_bars[symbol] = aggregate_h4(m1_frame, cfg.h4.anchor_utc_hour)
    for symbol, frame in all_bars.items():
        frame["symbol"] = symbol
    all_events = []
    all_raw = []
    all_outcomes = []
    leakage = []
    dropped = {}
    per_symbol = {}
    hashes = {}
    external_symbols = {
        spec.get("symbol") for spec in (cfg.external.dxy, cfg.external.real_yield_proxy) if spec.get("symbol")
    }
    for external_symbol in external_symbols:
        hashes.update(sha256_files(list_m1_files(cfg, external_symbol)))
    for symbol in cfg.symbols:
        bars = all_bars[symbol]
        files = list_m1_files(cfg, symbol)
        hashes.update(sha256_files(files))
        raw = detect_events(bars, cfg, symbol)
        dedup = dedup_events(raw)
        final = dedup[~dedup["below_min_penetration"]].copy()
        features = add_features(final, bars, cfg, symbol, external, all_bars)
        drift = drift_table(bars, cfg.horizons_bars)
        if not drift.empty:
            drift["symbol"] = symbol
        outcomes, dropped_count = compute_outcomes(
            features, bars, cfg.horizons_bars, cfg.mfe_mae_horizon_bars, drift
        )
        dropped[symbol] = dropped_count
        all_raw.append(raw)
        all_events.append(outcomes)
        all_outcomes.append(outcomes)
        leakage.append(run_leakage(cfg, symbol, bars, final, features, external, all_bars))
        years = {}
        year_stats = {}
        if not final.empty:
            final_year = pd.to_datetime(final.event_ts_utc).dt.year.astype(str)
            for year, group in final.groupby(final_year):
                years[year] = int(len(group))
                idx = group.bar_idx.astype(int)
                atr_values = bars.iloc[idx].atr14.to_numpy(float)
                close_values = bars.iloc[idx].close.to_numpy(float)
                year_stats[year] = {
                    "atr_median": float(pd.Series(atr_values).median()),
                    "atr_close_bp": float(pd.Series(atr_values / close_values * 1e4).median()),
                }
        per_symbol[symbol] = {
            "symbol": symbol,
            "source": cfg.sources.get(symbol, "dukascopy"),
            "files": len(files),
            "n_h4": len(bars),
            "raw": len(raw),
            "dedup": len(dedup),
            "post_threshold": len(final),
            "year": years,
            "year_stats": year_stats,
            "level_type": final["level_type"].value_counts().sort_index().astype(int).to_dict()
            if not final.empty
            else {},
            "range_start": bars.ts.min().isoformat() if not bars.empty else None,
            "range_end": bars.ts.max().isoformat() if not bars.empty else None,
            "mean_exec_cost_bp": float(outcomes.exec_cost_bp.mean()) if not outcomes.empty else float("nan"),
            "m1_rows_before": int(m1_stats[symbol]["rows_before"]),
            "m1_rows_dropped": int(m1_stats[symbol]["rows_dropped"]),
            "m1_drop_pct": float(m1_stats[symbol]["drop_pct"]),
            "m1_weekday_before": m1_stats[symbol]["weekday_before"],
            "m1_weekday_after": m1_stats[symbol]["weekday_after"],
            "h4_bars_n_m1_lt_30": int((bars["n_m1"] < 30).sum()),
        }
    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    raw_events = pd.concat(all_raw, ignore_index=True) if all_raw else pd.DataFrame()
    drift_frames = []
    for _symbol, bars in all_bars.items():
        frame = drift_table(bars, cfg.horizons_bars)
        frame["symbol"] = _symbol
        drift_frames.append(frame)
    drift_all = pd.concat(drift_frames, ignore_index=True) if drift_frames else pd.DataFrame()
    events.to_parquet(output / "events_h4.parquet", index=False)
    raw_events.to_parquet(output / "events_h4_raw.parquet", index=False)
    drift_all.to_parquet(output / "drift_table.parquet", index=False)
    total = {
        key: sum(row[key] for row in per_symbol.values())
        for key in ("n_h4", "raw", "dedup", "post_threshold")
    }
    qualifying = sum(row["post_threshold"] >= 400 for row in per_symbol.values())
    summary = {
        "symbols": per_symbol,
        "total": total,
        "dropped_no_next_bar": dropped,
        "gate": {
            "pass": total["post_threshold"] >= 3000 and qualifying >= 3,
            "reason": (
                f"post_threshold={total['post_threshold']} (need >=3000); "
                f"symbols_ge_400={qualifying} (need >=3)"
            ),
        },
        "equal_tolerance": {"old": old, "new": cfg.equal_level_tol_atr},
    }
    (output / "COUNTS.md").write_text(_counts_markdown(cfg, summary, old, dropped))
    (output / "counts.json").write_text(json.dumps(summary, indent=2, default=str))
    write_schema(
        output / "SCHEMA.md",
        events,
        "NFP deterministic rule; FOMC omitted (historical parsing not reliable); "
        "CPI omitted (BLS returned HTTP 403).",
    )
    write_leakage(output / "leakage.json", leakage)
    macro_path = resolve_macro_calendar(cfg.macro_calendar)
    if macro_path.exists():
        hashes["macro_calendar.csv"] = hashlib.sha256(macro_path.read_bytes()).hexdigest()
    write_manifest(
        output / "manifest.json",
        cfg,
        hashes,
        started,
        metadata={"m1_cleaning": {symbol: {
            "rows_before": row["m1_rows_before"],
            "rows_dropped": row["m1_rows_dropped"],
            "drop_pct": row["m1_drop_pct"],
            "weekday_before": row["m1_weekday_before"],
            "weekday_after": row["m1_weekday_after"],
            "h4_bars_n_m1_lt_30": row["h4_bars_n_m1_lt_30"],
        } for symbol, row in per_symbol.items()}},
    )
    return summary
