from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingo_lab.config import ensure_storage_dirs, load_config


SIGNAL_COLUMNS = [
    "signal_ict_reversal",
    "signal_rsi_reversion",
    "signal_trend_continuation",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a diagnostic report for exported bars/backtest results.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--bars", required=True, help="Bars parquet file, e.g. data/bars/XAUUSD/1min.parquet")
    parser.add_argument("--reports-dir", help="Defaults to data/reports/<symbol>")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_storage_dirs(config)
    bars = pd.read_parquet(args.bars)
    reports_dir = Path(args.reports_dir) if args.reports_dir else config.storage.reports_dir / config.symbol
    reports_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "symbol": config.symbol,
        "bars": summarize_bars(bars),
        "metrics": load_metrics(reports_dir),
        "trades": summarize_trades(reports_dir),
    }

    report = json_safe(report)
    output_path = reports_dir / "diagnostic_report.json"
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(json.dumps(report, indent=2, default=str))
    print(f"Diagnostic report: {output_path}")
    return 0


def summarize_bars(bars: pd.DataFrame) -> dict:
    if bars.empty:
        return {"rows": 0}

    data = bars.copy()
    data["time_utc"] = pd.to_datetime(data["time_utc"], utc=True)
    data = data.sort_values("time_utc")
    minute_diffs = data["time_utc"].diff().dropna()
    gaps = minute_diffs[minute_diffs > pd.Timedelta(minutes=1)]

    summary = {
        "rows": int(len(data)),
        "start_utc": data["time_utc"].iloc[0],
        "end_utc": data["time_utc"].iloc[-1],
        "duplicate_timestamps": int(data["time_utc"].duplicated().sum()),
        "gaps_over_1min": int(len(gaps)),
        "largest_gap": gaps.max() if len(gaps) else None,
        "price_min": float(data["low"].min()),
        "price_max": float(data["high"].max()),
        "price_last": float(data["close"].iloc[-1]),
    }

    if "spread_points_mean" in data.columns:
        spread = data["spread_points_mean"].dropna()
    elif "spread" in data.columns:
        spread = data["spread"].dropna()
    else:
        spread = pd.Series(dtype=float)

    if not spread.empty:
        summary.update(
            {
                "spread_mean": float(spread.mean()),
                "spread_median": float(spread.median()),
                "spread_p95": float(spread.quantile(0.95)),
                "spread_max": float(spread.max()),
            }
        )

    return summary


def load_metrics(reports_dir: Path) -> list[dict]:
    metrics_path = reports_dir / "first_backtest_metrics.csv"
    if not metrics_path.exists():
        return []
    return pd.read_csv(metrics_path).to_dict(orient="records")


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return value


def summarize_trades(reports_dir: Path) -> dict:
    summary = {}
    by_hour_frames = []

    for signal_name in SIGNAL_COLUMNS:
        trades_path = reports_dir / f"{signal_name}_trades.parquet"
        if not trades_path.exists():
            summary[signal_name] = {"trades": 0, "file_found": False}
            continue

        trades = pd.read_parquet(trades_path)
        if trades.empty:
            summary[signal_name] = {"trades": 0, "file_found": True}
            continue

        trades["time_utc"] = pd.to_datetime(trades["time_utc"], utc=True)
        trades["hour_utc"] = trades["time_utc"].dt.hour
        wins = trades["outcome"] == 1
        losses = trades["outcome"] == -1

        signal_summary = {
            "file_found": True,
            "trades": int(len(trades)),
            "wins": int(wins.sum()),
            "losses": int(losses.sum()),
            "win_rate": float(wins.mean()),
            "long_trades": int((trades["direction"] == "LONG").sum()) if "direction" in trades else None,
            "short_trades": int((trades["direction"] == "SHORT").sum()) if "direction" in trades else None,
            "avg_bars_to_exit": float(trades["bars_to_exit"].dropna().mean())
            if trades["bars_to_exit"].notna().any()
            else None,
        }
        summary[signal_name] = signal_summary

        hourly = (
            trades.groupby("hour_utc")
            .agg(trades=("outcome", "size"), wins=("outcome", lambda s: int((s == 1).sum())))
            .reset_index()
        )
        hourly["win_rate"] = hourly["wins"] / hourly["trades"]
        hourly["signal_name"] = signal_name
        by_hour_frames.append(hourly)

    if by_hour_frames:
        by_hour = pd.concat(by_hour_frames, ignore_index=True)
        by_hour.to_csv(reports_dir / "diagnostic_trades_by_hour.csv", index=False)

    return summary


if __name__ == "__main__":
    raise SystemExit(main())
