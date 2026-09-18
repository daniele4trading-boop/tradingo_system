from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

from ..bars import aggregate_h4
from ..config import Config
from ..data import list_m1_files, read_m1, sha256_files
from ..events import dedup_events, detect_events


def _source(cfg: Config, symbol: str) -> str:
    return cfg.sources.get(symbol, "dukascopy")


def _symbol_count(cfg: Config, symbol: str) -> dict:
    files = list_m1_files(cfg, symbol)
    m1 = read_m1(cfg, symbol)
    h4 = aggregate_h4(m1, cfg.h4.anchor_utc_hour)
    raw = detect_events(h4, cfg, symbol)
    dedup = dedup_events(raw)
    filtered = dedup[~dedup["below_min_penetration"]].copy()
    if filtered.empty:
        years = Counter()
        levels = Counter()
    else:
        years = Counter(pd.to_datetime(filtered["event_ts_utc"]).dt.year.astype(str))
        levels = Counter(filtered["level_type"])
    return {
        "symbol": symbol,
        "source": _source(cfg, symbol),
        "files": len(files),
        "input_sha256": sha256_files(files),
        "n_m1": int(len(m1)),
        "n_h4": int(len(h4)),
        "raw": int(len(raw)),
        "dedup": int(len(dedup)),
        "post_threshold": int(len(filtered)),
        "year": dict(sorted(years.items())),
        "level_type": dict(sorted(levels.items())),
        "range_start": h4["ts"].min().isoformat() if not h4.empty else None,
        "range_end": h4["ts"].max().isoformat() if not h4.empty else None,
    }


def _markdown(summary: dict) -> str:
    rows = [
        "# H4 Step 1 counts",
        "",
        "Dedup key: `(symbol, event_ts_utc, dir)`; priority: equal > swing20 > swing10 > swing5.",
        "Post-threshold means `dedup ∧ ¬below_min_penetration` with penetration measured in ATR14.",
        "",
        "| Symbol | Source | M1 files | H4 bars | Raw | Dedup | Post-threshold | Range |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["symbols"].values():
        rows.append(
            f"| {row['symbol']} | {row['source']} | {row['files']} | {row['n_h4']} | "
            f"{row['raw']} | {row['dedup']} | "
            f"{row['post_threshold']} | {row['range_start']} → {row['range_end']} |"
        )
    total = summary["total"]
    rows.extend(
        [
            f"| **TOTAL** | — | — | {total['n_h4']} | {total['raw']} | {total['dedup']} | "
            f"{total['post_threshold']} | — |",
            "",
            f"GATE: **{'PASS' if summary['gate']['pass'] else 'FAIL'}** — {summary['gate']['reason']}",
            "",
        ]
    )
    for row in summary["symbols"].values():
        rows.append(f"## {row['symbol']}")
        rows.append("")
        rows.append("### Post-threshold per anno")
        rows.append("")
        rows.append("| Anno | Count |")
        rows.append("|---:|---:|")
        rows.extend(f"| {year} | {count} |" for year, count in row["year"].items())
        rows.append("")
        rows.append("### Post-threshold per level_type")
        rows.append("")
        rows.append("| Level type | Count |")
        rows.append("|---|---:|")
        rows.extend(f"| {level} | {count} |" for level, count in row["level_type"].items())
        rows.append("")
    return "\n".join(rows)


def run(cfg: Config) -> dict:
    per_symbol = {symbol: _symbol_count(cfg, symbol) for symbol in cfg.symbols}
    total = {
        "n_h4": sum(row["n_h4"] for row in per_symbol.values()),
        "raw": sum(row["raw"] for row in per_symbol.values()),
        "dedup": sum(row["dedup"] for row in per_symbol.values()),
        "post_threshold": sum(row["post_threshold"] for row in per_symbol.values()),
    }
    qualifying = sum(row["post_threshold"] >= 400 for row in per_symbol.values())
    gate = {
        "pass": total["post_threshold"] >= 3000 and qualifying >= 3,
        "reason": (
            f"post_threshold={total['post_threshold']} (need >=3000); symbols_ge_400={qualifying} (need >=3)"
        ),
    }
    summary = {"symbols": per_symbol, "total": total, "gate": gate}
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "counts.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (output / "COUNTS.md").write_text(_markdown(summary))
    return summary
