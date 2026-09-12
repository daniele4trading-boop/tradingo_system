from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingo_lab.backtest import add_baseline_signals, evaluate_signal, metrics_to_frame
from tradingo_lab.config import ensure_storage_dirs, load_config
from tradingo_lab.indicators import add_indicators
from tradingo_lab.labeling import label_target_before_stop


SIGNAL_COLUMNS = [
    "signal_ict_reversal",
    "signal_rsi_reversion",
    "signal_trend_continuation",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the first objective 300-point XAUUSD backtest.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--bars", required=True, help="Bars parquet file built from ticks.")
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_storage_dirs(config)
    point = load_exported_point(config) or config.market.default_point

    bars = pd.read_parquet(args.bars)
    featured = add_indicators(bars, config.features)
    labeled = label_target_before_stop(
        featured,
        point=point,
        target_points=config.market.target_points,
        stop_points=config.market.stop_points,
        horizon_bars=config.backtest.horizon_bars,
    )
    signals = add_baseline_signals(labeled)

    metrics = []
    report_dir = config.storage.reports_dir / config.symbol
    report_dir.mkdir(parents=True, exist_ok=True)
    for signal_col in SIGNAL_COLUMNS:
        metric, trades = evaluate_signal(
            signals,
            signal_col=signal_col,
            slippage_points=config.backtest.slippage_points,
        )
        metrics.append(metric)
        trades.to_parquet(report_dir / f"{signal_col}_trades.parquet", index=False)

    metrics_df = metrics_to_frame(metrics)
    metrics_path = report_dir / "first_backtest_metrics.csv"
    dataset_path = report_dir / "first_backtest_dataset.parquet"
    metrics_df.to_csv(metrics_path, index=False)
    signals.to_parquet(dataset_path, index=False)
    print(metrics_df.to_string(index=False))
    print(f"Metrics: {metrics_path}")
    print(f"Dataset: {dataset_path}")
    return 0


def load_exported_point(config) -> float | None:
    metadata_path = config.storage.root / "metadata" / f"{config.symbol}_mt5_metadata.json"
    if not metadata_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    symbol_info = metadata.get("symbol") or {}
    point = symbol_info.get("point")
    return float(point) if point else None


if __name__ == "__main__":
    raise SystemExit(main())
