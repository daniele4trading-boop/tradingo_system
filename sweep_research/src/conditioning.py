from __future__ import annotations

import base64
import hashlib
import html
import json
import platform
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd
from scipy.stats import norm

from .. import __version__
from .config import Config
from .event_study import dedup_events, describe_returns
from .reports import write_reports

CONTINUOUS_FEATURES = [
    "wick_ratio",
    "time_beyond_level_sec",
    "displacement_60s",
    "displacement_180s",
    "dist_from_level_atr",
    "level_age_bars",
    "delta_est",
    "cvd_session",
    "delta_divergence_mag",
    "vol_vs_hourly_median",
    "vpin",
    "spread_at_event",
    "spread_expansion",
    "minutes_from_london_open",
    "minutes_from_ny_open",
    "vs_session_vwap",
    "vs_poc",
    "vs_vah",
    "vs_val",
    "efficiency_ratio",
    "overnight_range",
    "asia_gap",
    "dxy_intraday_trend",
    "ust_proxy_change",
    "xagusd_divergence",
]
CATEGORICAL_FEATURES = [
    "tf",
    "dir",
    "level_type",
    "session",
    "killzone_flag",
    "day_of_week",
    "is_month_end",
    "delta_divergence_flag",
    "vol_burst_flag",
    "volatility_regime",
    "liquidity_density",
    "level_touch_count",
    "hour_ny",
]
PAIRS = [
    ("delta_divergence_flag", "session"),
    ("vol_vs_hourly_median", "session"),
    ("volatility_regime", "delta_divergence_flag"),
    ("killzone_flag", "dir"),
    ("tf", "level_type"),
]
TEST_COLUMNS = [
    "feature", "feature2", "bin", "bin2", "horizon_min", "n", "n_days",
    "mean", "median", "hit_rate", "t_cluster_day", "p", "p_adj_bh",
    "survives_q10", "survives_q05",
]


def bh_adjust(p: np.ndarray) -> np.ndarray:
    """Applica Benjamini-Hochberg e ripristina l'ordine originale."""
    values = np.asarray(p, dtype=float)
    adjusted = np.ones(len(values), dtype=float)
    finite = np.isfinite(values)
    if not finite.any():
        return adjusted
    indices = np.flatnonzero(finite)
    order = indices[np.argsort(values[finite], kind="mergesort")]
    ranked = values[order] * len(order) / np.arange(1, len(order) + 1)
    monotone = np.minimum.accumulate(ranked[::-1])[::-1]
    adjusted[order] = np.clip(monotone, 0.0, 1.0)
    return adjusted


def _qcut_labels(series: pd.Series) -> pd.Series:
    result = pd.Series(pd.NA, index=series.index, dtype="string")
    finite = series.notna()
    if not finite.any():
        return result
    bins = pd.qcut(series[finite], q=5, duplicates="drop")
    categories = list(bins.cat.categories)
    labels = [f"q{index + 1}" for index in range(len(categories))]
    result.loc[finite] = bins.cat.rename_categories(labels).astype("string")
    return result


def _feature_bins(events: pd.DataFrame) -> tuple[dict[str, pd.Series], list[str]]:
    bins: dict[str, pd.Series] = {}
    skipped: list[str] = []
    for name in CONTINUOUS_FEATURES:
        if name not in events:
            skipped.append(name)
            continue
        values = pd.to_numeric(events[name], errors="coerce")
        result = _qcut_labels(values)
        missing_count = int(values.isna().sum())
        if missing_count >= 200:
            result.loc[values.isna()] = "nan"
        bins[name] = result
    for name in CATEGORICAL_FEATURES:
        if name == "hour_ny":
            if "event_ts_ny" not in events:
                skipped.append(name)
                continue
            values = pd.to_datetime(events["event_ts_ny"], utc=True).dt.tz_convert(
                "America/New_York"
            ).dt.hour
        elif name not in events:
            skipped.append(name)
            continue
        else:
            values = events[name]
        missing_count = int(values.isna().sum())
        if values.nunique(dropna=True) > 24:
            result = _qcut_labels(pd.to_numeric(values, errors="coerce"))
        else:
            result = values.astype("string")
        if missing_count >= 200:
            result.loc[values.isna()] = "nan"
        else:
            result.loc[values.isna()] = pd.NA
        bins[name] = result
    return bins, skipped


def _p_value(t_value: float | None) -> float:
    if t_value is None or not np.isfinite(t_value):
        return 1.0
    return float(2.0 * norm.sf(abs(t_value)))


def _segment_row(
    events: pd.DataFrame,
    mask: pd.Series,
    feature: str,
    bin_name: str,
    feature2: str,
    bin_name2: str,
    horizon: int,
    rng: np.random.Generator,
) -> dict | None:
    subset = events.loc[mask]
    outcome = subset[f"fwd_ret_{horizon}_dir_bp"].to_numpy(float)
    valid = np.isfinite(outcome)
    subset = subset.loc[valid]
    if len(subset) < 200:
        return None
    n_days = int(subset["ny_date"].nunique())
    if n_days < 30:
        return None
    stats = describe_returns(
        subset[f"fwd_ret_{horizon}_dir_bp"].to_numpy(float),
        subset["ny_date"].to_numpy(),
        rng,
        horizon,
        inferential=True,
        bootstrap=False,
    )
    t_value = stats.get("t_cluster_day")
    return {
        "feature": feature,
        "feature2": feature2,
        "bin": bin_name,
        "bin2": bin_name2,
        "horizon_min": horizon,
        "n": int(stats["n"]),
        "n_days": n_days,
        "mean": stats["mean"],
        "median": stats["median"],
        "hit_rate": stats["hit_rate"],
        "t_cluster_day": t_value,
        "p": _p_value(t_value),
    }


def _candidates(
    events: pd.DataFrame,
    bins: dict[str, pd.Series],
    cfg: Config,
    rng: np.random.Generator,
) -> tuple[list[dict], int]:
    rows: list[dict] = []
    dropped = 0
    horizons = [30, 120]

    def evaluate(feature: str, feature2: str = "") -> None:
        nonlocal dropped
        first = bins[feature]
        second = bins.get(feature2) if feature2 else None
        grouped = pd.DataFrame({"first": first, "second": second}).dropna(
            subset=["first"] + (["second"] if second is not None else [])
        )
        group_columns = ["first"] + (["second"] if second is not None else [])
        for values, _group in grouped.groupby(group_columns, sort=True, dropna=False):
            if not isinstance(values, tuple):
                values = (values,)
            mask = first.eq(values[0])
            if second is not None:
                mask &= second.eq(values[1])
            for horizon in horizons:
                row = _segment_row(
                    events, mask, feature, str(values[0]), feature2,
                    str(values[1]) if second is not None else "", horizon, rng
                )
                if row is None:
                    dropped += 1
                else:
                    rows.append(row)

    for feature in bins:
        evaluate(feature)
    for feature, feature2 in PAIRS:
        if feature in bins and feature2 in bins:
            evaluate(feature, feature2)
        else:
            dropped += 2
    return rows, dropped


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _write_manifest(cfg: Config, events_path: Path, output: Path) -> None:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    payload = {
        "config": asdict(cfg),
        "seed": cfg.seed,
        "data_sha256": {str(events_path): hashlib.sha256(events_path.read_bytes()).hexdigest()},
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "cli_command": "python -m sweep_research run --stage s2",
        "package_version": __version__,
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "git_commit": commit,
    }
    (output / "manifest_s2.json").write_text(json.dumps(_jsonable(payload), indent=2))


def _write_html(summary: dict, rows: list[dict], output: Path) -> None:
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plot_path = output / "p_values_histogram.png"
    values = np.asarray([row["p"] for row in rows], dtype=float)
    fig, axis = plt.subplots(figsize=(8, 4))
    if len(values):
        axis.hist(values, bins=20, range=(0, 1))
    axis.set_xlabel("raw p-value")
    axis.set_ylabel("tests")
    axis.set_title("S2 raw p-values")
    fig.tight_layout()
    fig.savefig(plot_path, dpi=130)
    plt.close(fig)
    encoded = base64.b64encode(plot_path.read_bytes()).decode("ascii")
    survivor_rows = [
        [row["feature"], row["bin"], row["horizon_min"], row["n"], row["mean"],
         row["t_cluster_day"], row["p_adj_bh"]]
        for row in summary["survivors"]
    ]
    table_rows = [
        [
            row["feature"], row["feature2"], row["bin"], row["bin2"], row["horizon_min"],
            row["n"], row["n_days"], row["mean"], row["median"], row["hit_rate"],
            row["t_cluster_day"], row["p"], row["p_adj_bh"],
            row["survives_q10"], row["survives_q05"],
        ]
        for row in rows
    ]
    def table(headers, values):
        head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>"
            for row in values
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>S2 Conditioning</title>
<style>body{{font-family:sans-serif;margin:2rem}} table{{border-collapse:collapse;font-size:.82rem}}
th,td{{border:1px solid #bbb;padding:.25rem .4rem}} img{{max-width:100%}}</style></head>
<body><h1>S2 — Condizionamento con FDR</h1>
<h2>Test totali: {summary["n_tests_s2"]}</h2>
<h2>Metodologia</h2>
<p>Popolazione: dedup_all. Outcome: 30 e 120 minuti, rendimenti lordi senza costi.</p>
<p>Segmenti: n ≥ 200 outcome finiti e almeno 30 giorni NY. I quintili sono fit
sull'intera popolazione; questa è analisi descrittiva, non una procedura train/test.</p>
<p>Cluster-day t-stat, p-value normale bilaterale, BH q=0.10 su tutta la famiglia.
Nessun bootstrap e nessuna curva di equity.</p>
<h2>Survivors BH q=0.10</h2>
{table(["feature", "bin", "h", "n", "mean", "t", "p_adj"], survivor_rows)
 if survivor_rows else "<p>nessun segmento sopravvive alla correzione FDR</p>"}
<h2>Tabella completa, ordinata per p</h2>
{table(["feature", "feature2", "bin", "bin2", "h", "n", "days", "mean", "median",
        "hit", "t", "p", "p_adj", "q10", "q05"], table_rows)}
<h2>Distribuzione p-value</h2>
<img src="data:image/png;base64,{encoded}" />
</body></html>"""
    (output / "report_s2.html").write_text(document)
    summary["plot"] = plot_path.name


def run_s2(cfg: Config) -> dict:
    output = Path(cfg.output_dir)
    events_path = output / "events.parquet"
    if not events_path.exists():
        raise SystemExit(f"S2: events.parquet non trovato: {events_path}")
    events = pd.read_parquet(events_path)
    events = dedup_events(events, ["event_ts_utc", "dir"])
    bins, skipped = _feature_bins(events)
    rng = np.random.default_rng(cfg.seed)
    rows, dropped = _candidates(events, bins, cfg, rng)
    adjusted = bh_adjust(np.asarray([row["p"] for row in rows], dtype=float))
    for row, adjusted_p in zip(rows, adjusted, strict=True):
        row["p_adj_bh"] = float(adjusted_p)
        row["survives_q10"] = bool(adjusted_p <= 0.10)
        row["survives_q05"] = bool(adjusted_p <= 0.05)
    rows.sort(key=lambda row: (row["p"], row["feature"], row["bin"], row["horizon_min"]))
    survivors = [row for row in rows if row["survives_q10"]]
    survivors_q05 = [row for row in rows if row["survives_q05"]]
    summary = {
        "n_tests_s2": len(rows),
        "n_segments_dropped": dropped,
        "skipped_features": skipped,
        "n_raw_p_lt_05": int(sum(row["p"] < 0.05 for row in rows)),
        "expected_false_positives_05": 0.05 * len(rows),
        "n_survivors_q10": len(survivors),
        "n_survivors_q05": len(survivors_q05),
        "tests": rows,
        "survivors": survivors,
        "survivors_q05": survivors_q05,
    }
    s2_output = output / "s2"
    s2_output.mkdir(parents=True, exist_ok=True)
    rows = _jsonable(rows)
    summary = _jsonable(summary)
    pd.DataFrame(rows, columns=TEST_COLUMNS).to_csv(s2_output / "tests_s2.csv", index=False)
    (s2_output / "s2_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    _write_manifest(cfg, events_path, s2_output)
    _write_html(summary, rows, s2_output)
    (s2_output / "s2_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    leakage_path = output / "leakage.json"
    missing_path = output / "missing_external.json"
    inventory_path = output / "inventory.json"
    if leakage_path.exists() and missing_path.exists() and inventory_path.exists():
        s1_path = output / "s1" / "s1_summary.json"
        s1_summary = json.loads(s1_path.read_text()) if s1_path.exists() else None
        write_reports(
            pd.read_parquet(events_path), cfg, json.loads(leakage_path.read_text()),
            json.loads(missing_path.read_text()), json.loads(inventory_path.read_text()),
            s1_summary=s1_summary, s2_summary=summary,
        )
    return summary
