from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestMetrics:
    signal_name: str
    trades: int
    wins: int
    losses: int
    unresolved: int
    win_rate: float
    profit_factor: float
    expectancy_points: float
    avg_bars_to_exit: float


def add_baseline_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Create objective starter signals to validate the research pipeline."""
    result = df.copy()
    result["signal_ict_reversal"] = 0
    result.loc[result["sweep_low"] & (result["displacement_up"] | result["fvg_bull"]), "signal_ict_reversal"] = 1
    result.loc[result["sweep_high"] & (result["displacement_down"] | result["fvg_bear"]), "signal_ict_reversal"] = -1

    result["signal_rsi_reversion"] = 0
    result.loc[(result["rsi"] < 25) & result["sweep_low"], "signal_rsi_reversion"] = 1
    result.loc[(result["rsi"] > 75) & result["sweep_high"], "signal_rsi_reversion"] = -1

    result["signal_trend_continuation"] = 0
    result.loc[
        result["trend_up"] & (result["close"] > result["vwap"]) & (result["adx"] > 20) & result["displacement_up"],
        "signal_trend_continuation",
    ] = 1
    result.loc[
        result["trend_down"] & (result["close"] < result["vwap"]) & (result["adx"] > 20) & result["displacement_down"],
        "signal_trend_continuation",
    ] = -1
    return result


def evaluate_signal(
    df: pd.DataFrame,
    signal_col: str,
    slippage_points: int = 0,
) -> tuple[BacktestMetrics, pd.DataFrame]:
    trades = df[df[signal_col] != 0].copy()
    if trades.empty:
        metrics = BacktestMetrics(signal_col, 0, 0, 0, 0, 0.0, 0.0, 0.0, np.nan)
        return metrics, trades

    long_mask = trades[signal_col] == 1
    short_mask = trades[signal_col] == -1
    trades["outcome"] = np.where(long_mask, trades["long_outcome"], trades["short_outcome"])
    trades["bars_to_exit"] = np.where(long_mask, trades["long_bars_to_exit"], trades["short_bars_to_exit"])
    trades["direction"] = np.where(long_mask, "LONG", "SHORT")

    target_points = trades["target_points"].astype(float)
    stop_points = trades["stop_points"].astype(float)
    trades["net_points"] = np.select(
        [trades["outcome"] == 1, trades["outcome"] == -1],
        [target_points - slippage_points * 2, -stop_points - slippage_points * 2],
        default=0.0,
    )

    wins = int((trades["outcome"] == 1).sum())
    losses = int((trades["outcome"] == -1).sum())
    unresolved = int((trades["outcome"] == 0).sum())
    gross_profit = float(trades.loc[trades["net_points"] > 0, "net_points"].sum())
    gross_loss = float(-trades.loc[trades["net_points"] < 0, "net_points"].sum())
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf") if gross_profit else 0.0
    metrics = BacktestMetrics(
        signal_name=signal_col,
        trades=int(len(trades)),
        wins=wins,
        losses=losses,
        unresolved=unresolved,
        win_rate=wins / len(trades),
        profit_factor=profit_factor,
        expectancy_points=float(trades["net_points"].mean()),
        avg_bars_to_exit=float(trades["bars_to_exit"].dropna().mean())
        if trades["bars_to_exit"].notna().any()
        else np.nan,
    )
    return metrics, trades


def metrics_to_frame(metrics: list[BacktestMetrics]) -> pd.DataFrame:
    return pd.DataFrame([asdict(metric) for metric in metrics])
