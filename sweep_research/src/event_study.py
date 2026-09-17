from __future__ import annotations

import base64
import hashlib
import html
import json
import math
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
from .reports import write_reports

LEVEL_PRIORITY = {
    "equal_high": 0,
    "equal_low": 0,
    "swing_high_20": 1,
    "swing_low_20": 1,
    "swing_high_10": 2,
    "swing_low_10": 2,
    "swing_high_5": 3,
    "swing_low_5": 3,
}
TF_PRIORITY = {"M15": 0, "M5": 1}


def dedup_events(events: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Seleziona una riga deterministica per chiave di evento."""
    missing = sorted(set(key) - set(events.columns))
    if missing:
        raise ValueError(f"chiavi dedup mancanti: {missing}")
    if events.empty:
        return events.copy()
    work = events.copy()
    work["_level_priority"] = work["level_type"].map(LEVEL_PRIORITY).fillna(99)
    work["_tf_priority"] = work.get("tf", pd.Series("", index=work.index)).map(TF_PRIORITY).fillna(99)
    work["_event_priority"] = pd.to_numeric(
        work.get("event_id", pd.Series(np.arange(len(work)), index=work.index)),
        errors="coerce",
    ).fillna(np.inf)
    ordered = work.sort_values(
        key + ["_level_priority", "_tf_priority", "_event_priority"],
        kind="mergesort",
    )
    return ordered.drop_duplicates(key, keep="first").drop(
        columns=["_level_priority", "_tf_priority", "_event_priority"]
    ).reset_index(drop=True)


def _finite_arrays(x: np.ndarray, day_ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(x, dtype=float)
    days = np.asarray(day_ids)
    valid = np.isfinite(values) & pd.notna(days)
    return values[valid], days[valid]


def describe_returns(
    x: np.ndarray,
    day_ids: np.ndarray,
    rng: np.random.Generator,
    horizon_min: int | None = None,
    inferential: bool = True,
    bootstrap: bool = True,
    cost_bp: np.ndarray | None = None,
) -> dict:
    """Statistiche descrittive e inferenziali cluster-day per un rendimento."""
    raw_values = np.asarray(x, dtype=float)
    raw_days = np.asarray(day_ids)
    valid = np.isfinite(raw_values) & pd.notna(raw_days)
    values, days = raw_values[valid], raw_days[valid]
    n = len(values)
    if n == 0:
        result = {"n": 0}
        if inferential:
            result.update({"t_naive": None, "t_cluster_day": None, "boot_ci95": [None, None],
                           "boot_p_two_sided": None})
        if horizon_min is not None:
            result["mean_bp_per_sqrt_min"] = None
        return result
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=0))
    centered = values - mean
    skew = float(np.mean(centered**3) / std**3) if std > 0 else 0.0
    result = {
        "n": n,
        "mean": mean,
        "median": float(np.median(values)),
        "std": std,
        "skew": skew,
        "hit_rate": float(np.mean(values > 0)),
        "p05": float(np.quantile(values, 0.05)),
        "p25": float(np.quantile(values, 0.25)),
        "p75": float(np.quantile(values, 0.75)),
        "p95": float(np.quantile(values, 0.95)),
    }
    if cost_bp is not None:
        costs = np.asarray(cost_bp, dtype=float)[valid]
        costs = costs[np.isfinite(costs)]
        result["mean_cost_bp"] = float(np.mean(costs)) if len(costs) else None
        result["mean_net_bp"] = (
            mean - result["mean_cost_bp"]
            if result["mean_cost_bp"] is not None else None
        )
    if inferential:
        result["t_naive"] = float(mean / (std / math.sqrt(n))) if std > 0 else None
        _, inverse = np.unique(days, return_inverse=True)
        day_sums = np.bincount(inverse, weights=centered)
        se_cluster = float(np.sqrt(np.sum(day_sums**2)) / n)
        result["t_cluster_day"] = float(mean / se_cluster) if se_cluster > 0 else None
        if bootstrap:
            day_values, day_counts = np.unique(days, return_counts=True)
            sums = np.bincount(inverse, weights=values)
            n_days = len(day_values)
            weights = rng.multinomial(n_days, np.full(n_days, 1.0 / n_days), size=2000)
            bootstrap_means = (weights @ sums) / (weights @ day_counts)
            ci = np.quantile(bootstrap_means, [0.025, 0.975])
            result["boot_ci95"] = [float(ci[0]), float(ci[1])]
            centered_bootstrap = bootstrap_means - mean
            result["boot_p_two_sided"] = float(
                np.mean(np.abs(centered_bootstrap) >= abs(mean))
            )
    if horizon_min is not None:
        result["mean_bp_per_sqrt_min"] = float(mean / math.sqrt(horizon_min))
    return result


def cluster_diff_test(
    x_a: np.ndarray,
    days_a: np.ndarray,
    x_b: np.ndarray,
    days_b: np.ndarray,
) -> dict:
    values_a, groups_a = _finite_arrays(x_a, days_a)
    values_b, groups_b = _finite_arrays(x_b, days_b)
    mean_a = float(np.mean(values_a)) if len(values_a) else np.nan
    mean_b = float(np.mean(values_b)) if len(values_b) else np.nan
    diff = mean_a - mean_b
    influence_a = (values_a - mean_a) / len(values_a) if len(values_a) else np.array([])
    influence_b = -(values_b - mean_b) / len(values_b) if len(values_b) else np.array([])
    day_sums: dict[object, float] = {}
    for day, value in zip(groups_a, influence_a, strict=True):
        day_sums[day] = day_sums.get(day, 0.0) + float(value)
    for day, value in zip(groups_b, influence_b, strict=True):
        day_sums[day] = day_sums.get(day, 0.0) + float(value)
    se = float(np.sqrt(np.sum(np.square(list(day_sums.values())))))
    t_value = diff / se if se > 0 else None
    p_value = float(2.0 * norm.sf(abs(t_value))) if t_value is not None else 1.0
    return {
        "n_a": int(len(values_a)),
        "n_b": int(len(values_b)),
        "mean_a": mean_a,
        "mean_b": mean_b,
        "diff": float(diff),
        "se_cluster_day": se,
        "t": t_value,
        "p": p_value,
    }


def _returns_table(frame: pd.DataFrame, cfg: Config, rng: np.random.Generator,
                   inferential: bool, test_entries: list[dict], population: str,
                   count_tests: bool = True) -> dict:
    output = {}
    days = frame["ny_date"].to_numpy()
    for horizon in cfg.outcome_horizons_min:
        column = f"fwd_ret_{horizon}_dir_bp"
        stats = describe_returns(
            frame[column].to_numpy(), days, rng, horizon, inferential=inferential
        )
        output[str(horizon)] = stats
        if inferential and count_tests and stats.get("n", 0):
            test_entries.append({
                "population": population, "split": "pooled", "horizon_min": horizon, **stats
            })
    return output


def _mfe_mae_table(frame: pd.DataFrame, horizon: int) -> dict:
    output = {}
    for label, subset in [("pooled", frame), *[(str(tf), frame[frame["tf"] == tf]) for tf in ["M5", "M15"]]]:
        metrics = {}
        for name in [f"mfe_{horizon}_atr", f"mae_{horizon}_atr",
                     f"mfe_{horizon}_pts", f"mae_{horizon}_pts"]:
            values = subset[name].to_numpy(float)
            values = values[np.isfinite(values)]
            metrics[name] = {
                f"p{q}": float(np.quantile(values, q / 100)) if len(values) else None
                for q in [50, 75, 90]
            }
        mfe = subset[f"mfe_{horizon}_atr"].to_numpy(float)
        mae = subset[f"mae_{horizon}_atr"].to_numpy(float)
        valid = np.isfinite(mfe) & np.isfinite(mae)
        metrics["quota_mfe_gt_mae"] = float(np.mean(mfe[valid] > mae[valid])) if valid.any() else None
        if valid.any():
            cut1, cut2 = np.quantile(mfe[valid], [1 / 3, 2 / 3])
            tertile = np.digitize(mfe[valid], [cut1, cut2], right=True)
            conditional = []
            for index in range(3):
                selected = mae[valid][tertile == index]
                conditional.append({
                    "mfe_tertile": index + 1,
                    "n": int(len(selected)),
                    "mae_p50": float(np.quantile(selected, 0.5)) if len(selected) else None,
                    "mae_p75": float(np.quantile(selected, 0.75)) if len(selected) else None,
                    "mae_p90": float(np.quantile(selected, 0.9)) if len(selected) else None,
                })
            metrics["mae_by_mfe_tertile"] = conditional
        else:
            metrics["mae_by_mfe_tertile"] = []
        output[label] = metrics
    return output


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _plot_outputs(frame: pd.DataFrame, cfg: Config, output: Path) -> list[dict]:
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.mkdir(parents=True, exist_ok=True)
    paths = []
    horizons = cfg.outcome_horizons_min
    fig, axes = plt.subplots(1, len(horizons), figsize=(18, 4), squeeze=False)
    for axis, horizon in zip(axes[0], horizons, strict=True):
        column = f"fwd_ret_{horizon}_c1_ex_dir_bp"
        if column not in frame:
            column = f"fwd_ret_{horizon}_dir_bp"
        values = frame[column].to_numpy(float)
        values = values[np.isfinite(values)]
        if len(values):
            low, high = np.quantile(values, [0.01, 0.99])
            axis.hist(np.clip(values, low, high), bins=40)
        axis.set_title(f"{horizon} min")
        axis.set_xlabel("bp, clipped p01/p99")
    axes[0][0].set_ylabel("count")
    fig.tight_layout()
    hist_path = output / "returns_histograms.png"
    fig.savefig(hist_path, dpi=130)
    plt.close(fig)
    paths.append({"name": hist_path.name, "path": str(hist_path)})

    mfe = frame["mfe_120_atr"].to_numpy(float)
    mae = frame["mae_120_atr"].to_numpy(float)
    valid = np.isfinite(mfe) & np.isfinite(mae)
    fig, axis = plt.subplots(figsize=(7, 5))
    if valid.any():
        axis.hexbin(mfe[valid], mae[valid], gridsize=40, mincnt=1)
    axis.set_xlabel("MFE 120 min (ATR)")
    axis.set_ylabel("MAE 120 min (ATR)")
    axis.set_title("MFE vs MAE")
    fig.tight_layout()
    hex_path = output / "mfe_mae_hexbin.png"
    fig.savefig(hex_path, dpi=130)
    plt.close(fig)
    paths.append({"name": hex_path.name, "path": str(hex_path)})
    return paths


def _s1_stats(frame: pd.DataFrame, horizon: int, rng: np.random.Generator) -> dict:
    outcome = frame[f"fwd_ret_{horizon}_c1_ex_dir_bp"].to_numpy(float)
    costs = frame["cost_rt_bp"].to_numpy(float)
    return describe_returns(
        outcome, frame["ny_date"].to_numpy(), rng, horizon,
        inferential=True, cost_bp=costs,
    )


def _s1_test_row(stats: dict, population: str, split: str, horizon: int) -> dict:
    return {
        "population": population,
        "split": split,
        "horizon_min": horizon,
        **stats,
    }


def _write_html_revised(summary: dict, plot_paths: list[dict], output: Path) -> None:
    def table(headers: list[str], rows: list[list[object]]) -> str:
        return _html_table(headers, rows)

    comparison_rows = [
        [
            row["horizon_min"], row["dir"], row["population"], row["n"],
            row["mean"], row["t_cluster_day"],
        ]
        for row in summary["comparison"]
    ]
    pooled_rows = [
        [
            row["horizon_min"], row["n"], row["mean"], row["mean_net_bp"],
            row["mean_cost_bp"], row["t_cluster_day"], row["boot_ci95"],
            row["bar_close_raw_dir"], row["c1_raw_dir"],
        ]
        for row in summary["pooled_dedup_all"]
    ]
    asymmetry_rows = [
        [row["horizon_min"], row["diff"], row["se_cluster_day"], row["t"], row["p"]]
        for row in summary["asymmetry"]
    ]
    images = "".join(
        f'<h2>{html.escape(path["name"])}</h2><img src="{_embed(Path(path["path"]))}" />'
        for path in plot_paths
    )
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>S1 Event Study</title>
<style>body{{font-family:sans-serif;margin:2rem}} table{{border-collapse:collapse;margin:1rem 0}}
th,td{{border:1px solid #bbb;padding:.35rem .6rem}} img{{max-width:100%}}</style></head>
<body><h1>S1 Event Study</h1>
<p>Ipotesi dichiarata: continuazione (long dopo sweep_high, short dopo sweep_low),
one-sided; cambiata dopo la prima lettura S1 — conta come una configurazione
aggiuntiva provata.</p>
<p>Popolazione primaria: dedup_all, esclusi gli sweep sub-spread. Outcome primario:
rendimenti C1 de-trended, orientati con trade_sign.</p>
<p>Test statistici S1: {summary["n_tests_s1"]}; ipotesi provate:
{summary["n_hypotheses_tried"]}.</p>
<h2>Confronto popolazione vecchia/nuova</h2>
{table(["h", "dir", "population", "n", "mean", "t"], comparison_rows)}
<h2>Pooled dedup_all</h2>
{table(["h", "n", "mean", "mean_net", "cost", "t", "CI", "bar raw", "C1 raw"], pooled_rows)}
<h2>Test asimmetria</h2>
{table(["h", "diff H-L", "SE", "t", "p"], asymmetry_rows)}
<h2>Dettagli split e MFE/MAE</h2>
<pre>{html.escape(json.dumps(summary["splits"], indent=2, ensure_ascii=False))}</pre>
<pre>{html.escape(json.dumps(summary["mfe_mae"], indent=2, ensure_ascii=False))}</pre>
{images}</body></html>"""
    (output / "report_s1.html").write_text(document)


def _embed(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _html_table(headers: list[str], rows: list[list[object]]) -> str:
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _write_html(summary: dict, plot_paths: list[dict], output: Path) -> None:
    pooled = summary["pooled_dedup_all"]
    pooled_rows = [
        [row["horizon_min"], row["n"], f'{row["mean"]:.6g}', f'{row["t_cluster_day"]:.6g}',
         row["boot_ci95"], f'{row["hit_rate"]:.6g}']
        for row in pooled
    ]
    population_rows = []
    for population, horizons in summary["populations"].items():
        for horizon, stats in horizons.items():
            population_rows.append([
                population, horizon, stats.get("n"), stats.get("mean"),
                stats.get("median"), stats.get("std"), stats.get("t_cluster_day"),
                stats.get("boot_ci95"),
            ])
    split_rows = []
    for split_kind, splits in summary["splits"].items():
        for split, horizons in splits.items():
            for horizon, stats in horizons.items():
                split_rows.append([
                    split_kind, split, horizon, stats.get("n"), stats.get("mean"),
                    stats.get("t_cluster_day"), stats.get("boot_ci95"),
                ])
    annual_rows = []
    for year, horizons in summary["annual"].items():
        for horizon in ["30", "120"]:
            if horizon in horizons:
                stats = horizons[horizon]
                annual_rows.append([
                    year, horizon, stats.get("n"), stats.get("mean"),
                    stats.get("t_cluster_day"),
                ])
    mfe_rows = []
    for population, metrics in summary["mfe_mae"].items():
        for metric, quantiles in metrics.items():
            if metric.startswith(("mfe_", "mae_")) and metric.endswith(("_atr", "_pts")):
                mfe_rows.append([
                    population, metric, quantiles.get("p50"), quantiles.get("p75"),
                    quantiles.get("p90"),
                ])
    tables = (
        "<h2>Pooled dedup_all</h2>"
        + _html_table(["horizon_min", "n", "mean", "t_cluster_day", "boot_ci95", "hit_rate"], pooled_rows)
        + "<h2>Popolazioni</h2>"
        + _html_table(
            ["population", "horizon", "n", "mean", "median", "std", "t_cluster_day", "boot_ci95"],
            population_rows,
        )
        + "<h2>Split tf e direzione</h2>"
        + _html_table(
            ["split", "value", "horizon", "n", "mean", "t_cluster_day", "boot_ci95"],
            split_rows,
        )
        + "<h2>Distribuzione annuale</h2>"
        + _html_table(["year", "horizon", "n", "mean", "t_cluster_day"], annual_rows)
        + "<h2>MFE/MAE</h2>"
        + _html_table(["population", "metric", "p50", "p75", "p90"], mfe_rows)
    )
    images = "".join(
        f'<h2>{html.escape(path["name"])}</h2><img src="{_embed(Path(path["path"]))}" />'
        for path in plot_paths
    )
    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>S1 Event Study</title>
<style>body{{font-family:sans-serif;margin:2rem}} table{{border-collapse:collapse;margin:1rem 0}}
th,td{{border:1px solid #bbb;padding:.35rem .6rem}} img{{max-width:100%}}</style></head>
<body><h1>S1 Event Study</h1>
<p>Popolazione primaria: dedup_all. Rendimenti lordi, senza costi.</p>
<p>La popolazione raw contiene duplicati: ogni inferenza su raw sarebbe gonfiata;
raw è riportata solo descrittivamente.</p>
<p>Test statistici: {summary["n_tests_s1"]} t cluster-day; nessuna correzione per test multipli.
S1 non autorizza alcuna conclusione di edge.</p>
{tables}
{images}</body></html>"""
    (output / "report_s1.html").write_text(document)


def _manifest(cfg: Config, events_path: Path, output: Path) -> None:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    digest = hashlib.sha256(events_path.read_bytes()).hexdigest()
    payload = {
        "config": asdict(cfg),
        "seed": cfg.seed,
        "data_sha256": {str(events_path): digest},
        "run_timestamp_utc": datetime.now(UTC).isoformat(),
        "cli_command": "python -m sweep_research run --stage s1",
        "package_version": __version__,
        "versions": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "matplotlib": matplotlib.__version__,
        },
        "git_commit": commit,
    }
    (output / "manifest_s1.json").write_text(json.dumps(_jsonable(payload), indent=2))


def run_s1(cfg: Config) -> dict:
    output = Path(cfg.output_dir)
    events_path = output / "events.parquet"
    if not events_path.exists():
        raise SystemExit(f"S1: events.parquet non trovato: {events_path}")
    events = pd.read_parquet(events_path)
    rng = np.random.default_rng(cfg.seed)
    dedup_all_full = dedup_events(events, ["event_ts_utc", "dir"])
    dedup_all = dedup_all_full[~dedup_all_full["subspread_sweep"].astype(bool)].copy()
    tests: list[dict] = []
    populations = {"dedup_all_full": {}, "dedup_all": {}}
    pooled = []
    for horizon in cfg.outcome_horizons_min:
        full_stats = _s1_stats(dedup_all_full, horizon, rng)
        primary_stats = _s1_stats(dedup_all, horizon, rng)
        populations["dedup_all_full"][str(horizon)] = full_stats
        populations["dedup_all"][str(horizon)] = primary_stats
        pooled.append({
            "horizon_min": horizon,
            **primary_stats,
            "bar_close_raw_dir": float(
                np.nanmean(dedup_all[f"fwd_ret_{horizon}_dir_bp"])
            ),
            "c1_raw_dir": float(
                np.nanmean(dedup_all[f"fwd_ret_{horizon}_c1_dir_bp"])
            ),
        })
        tests.append(_s1_test_row(primary_stats, "dedup_all", "pooled", horizon))

    split_tf = {}
    for tf in ["M5", "M15"]:
        subset = dedup_all[dedup_all["tf"] == tf]
        split_tf[tf] = {}
        for horizon in cfg.outcome_horizons_min:
            stats = _s1_stats(subset, horizon, rng)
            split_tf[tf][str(horizon)] = stats
            tests.append(_s1_test_row(stats, f"tf={tf}", "tf", horizon))

    split_dir = {}
    for direction in ["sweep_high", "sweep_low"]:
        subset = dedup_all[dedup_all["dir"] == direction]
        split_dir[direction] = {}
        for horizon in cfg.outcome_horizons_min:
            stats = _s1_stats(subset, horizon, rng)
            split_dir[direction][str(horizon)] = stats
            tests.append(_s1_test_row(stats, f"dir={direction}", "dir", horizon))

    annual = {}
    years = pd.to_datetime(dedup_all["event_ts_utc"]).dt.year
    for year in range(2022, 2027):
        annual[str(year)] = {}
        subset = dedup_all[years == year]
        for horizon in [30, 120]:
            stats = _s1_stats(subset, horizon, rng)
            annual[str(year)][str(horizon)] = stats
            tests.append(_s1_test_row(stats, f"year={year}", "annual", horizon))

    asymmetry = []
    for horizon in cfg.outcome_horizons_min:
        high = dedup_all[dedup_all["dir"] == "sweep_high"]
        low = dedup_all[dedup_all["dir"] == "sweep_low"]
        result = cluster_diff_test(
            high[f"fwd_ret_{horizon}_c1_ex_bp"].to_numpy(float),
            high["ny_date"].to_numpy(),
            low[f"fwd_ret_{horizon}_c1_ex_bp"].to_numpy(float),
            low["ny_date"].to_numpy(),
        )
        result["horizon_min"] = horizon
        asymmetry.append(result)
        tests.append({
            "population": "asymmetry", "split": "H-L",
            "horizon_min": horizon, **result,
        })

    mfe_mae = _mfe_mae_table(dedup_all, cfg.mfe_mae_horizon_min)
    plots = _plot_outputs(dedup_all, cfg, output / "s1")
    comparison = []
    for horizon in [30, 120]:
        for direction in ["sweep_high", "sweep_low"]:
            for name, frame in [
                ("dedup_all_full", dedup_all_full),
                ("dedup_all", dedup_all),
                ("subspread_only", dedup_all_full[dedup_all_full["subspread_sweep"].astype(bool)]),
            ]:
                subset = frame[frame["dir"] == direction]
                stats = _s1_stats(subset, horizon, rng)
                comparison.append({
                    "horizon_min": horizon, "dir": direction, "population": name,
                    "n": stats.get("n", 0), "mean": stats.get("mean"),
                    "t_cluster_day": stats.get("t_cluster_day"),
                })
    summary = {
        "n_tests_s1": len(tests),
        "n_hypotheses_tried": 2,
        "n_events": {
            "raw": len(events), "dedup_all_full": len(dedup_all_full),
            "dedup_all": len(dedup_all),
        },
        "n_excluded_subspread": {
            direction: int(
                (dedup_all_full["subspread_sweep"].astype(bool)
                 & (dedup_all_full["dir"] == direction)).sum()
            )
            for direction in ["sweep_high", "sweep_low"]
        },
        "populations": populations,
        "splits": {"tf": split_tf, "dir": split_dir},
        "annual": annual,
        "mfe_mae": mfe_mae,
        "pooled_dedup_all": pooled,
        "comparison": comparison,
        "asymmetry": asymmetry,
        "tests": tests,
        "plots": [{"name": item["name"]} for item in plots],
    }
    summary = _jsonable(summary)
    s1_output = output / "s1"
    s1_output.mkdir(parents=True, exist_ok=True)
    (s1_output / "s1_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    _manifest(cfg, events_path, s1_output)
    _write_html_revised(summary, plots, s1_output)
    leakage_path = output / "leakage.json"
    missing_path = output / "missing_external.json"
    inventory_path = output / "inventory.json"
    if leakage_path.exists() and missing_path.exists() and inventory_path.exists():
        write_reports(
            events, cfg, json.loads(leakage_path.read_text()),
            json.loads(missing_path.read_text()), json.loads(inventory_path.read_text()),
            s1_summary=summary,
        )
    return summary
