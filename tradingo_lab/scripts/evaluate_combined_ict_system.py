from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import scripts.optimize_first_setups as first_setups
from tradingo_lab.config import load_config
from tradingo_lab.indicators import add_indicators


@dataclass(frozen=True)
class VariantMetrics:
    variant: str
    description: str
    spread_threshold: int
    trades: int
    trades_per_active_day: float
    wins: int
    losses: int
    unresolved: int
    win_rate: float
    profit_factor: float
    expectancy_points: float
    total_points: float
    max_dd_points: float
    profit_to_dd: float
    max_losing_streak: int
    active_trade_days: int
    worst_day_points: float
    best_day_points: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate combined ICT reversal and continuation rules.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--bars", default="data/bars/XAUUSD/1min.parquet")
    parser.add_argument("--slippage-points", type=int, default=5)
    args = parser.parse_args()

    config = load_config(args.config)
    point = first_setups.exported_point(config) or config.market.default_point
    bars = pd.read_parquet(args.bars).drop_duplicates("time_utc").sort_values("time_utc").reset_index(drop=True)
    bars["time_utc"] = pd.to_datetime(bars["time_utc"], utc=True)
    features = prepare_features(bars, config, point)
    active_days = features["date"].nunique()

    all_metrics: list[VariantMetrics] = []
    all_trades: list[pd.DataFrame] = []

    for spread_threshold in [20, 22, 23, 25]:
        components = build_components(features, point, args.slippage_points, spread_threshold)
        variants = {
            f"reversal_core_spread_{spread_threshold}": (
                "ICT NY reversal su tutti i segnali displacement",
                components["core_reversal"],
            ),
            f"reversal_quality_spread_{spread_threshold}": (
                "Solo reversal ICT NY con ATR basso e movimento non esteso",
                components["quality_reversal"],
            ),
            f"failed_continuation_spread_{spread_threshold}": (
                "Continuation opposta sui casi dove il reversal e' sfavorevole",
                components["failed_continuation"],
            ),
            f"quality_plus_failed_spread_{spread_threshold}": (
                "Reversal solo alta qualita' + continuation sui casi sfavorevoli",
                combine_components(components["quality_reversal"], components["failed_continuation"]),
            ),
            f"core_with_failed_replacement_spread_{spread_threshold}": (
                "Core reversal, ma sostituisce i casi sfavorevoli con continuation",
                combine_components(components["core_reversal_non_failed"], components["failed_continuation"]),
            ),
            f"core_plus_london_reversal_spread_{spread_threshold}": (
                "Core reversal + miglior ICT London reversal standalone",
                combine_components(components["core_reversal"], components["london_reversal"]),
            ),
            f"core_plus_retrace_spread_{spread_threshold}": (
                "Core reversal + miglior displacement retracement standalone",
                combine_components(components["core_reversal"], components["displacement_retrace"]),
            ),
            f"core_plus_rsi_london_spread_{spread_threshold}": (
                "Core reversal + miglior RSI sweep London standalone",
                combine_components(components["core_reversal"], components["rsi_london"]),
            ),
        }

        for variant, (description, trades) in variants.items():
            metrics = summarize(variant, description, spread_threshold, trades, active_days)
            all_metrics.append(metrics)
            if not trades.empty:
                tagged = trades.copy()
                tagged["variant"] = variant
                all_trades.append(tagged)

    metrics_df = pd.DataFrame([asdict(metric) for metric in all_metrics]).sort_values(
        ["profit_to_dd", "profit_factor", "expectancy_points"],
        ascending=[False, False, False],
    )

    output_dir = config.storage.reports_dir / config.symbol
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_df.to_csv(output_dir / "combined_ict_system_variants.csv", index=False)
    if all_trades:
        trades_df = pd.concat(all_trades, ignore_index=True).sort_values(["variant", "entry_time"])
        trades_df.to_csv(output_dir / "combined_ict_system_trades.csv", index=False)
        daily = (
            trades_df.groupby(["variant", "date"], as_index=False)["net_points"]
            .agg(["count", "sum"])
            .reset_index()
            .rename(columns={"count": "trades", "sum": "net_points"})
        )
        daily.to_csv(output_dir / "combined_ict_system_daily.csv", index=False)

    print(metrics_df.to_string(index=False))
    print(f"\nReports written to: {output_dir}")
    return 0


def prepare_features(bars: pd.DataFrame, config, point: float) -> pd.DataFrame:
    features = add_indicators(bars, config.features)
    features = first_setups.add_session_columns(features, config.raw.get("sessions", {}))
    features = features.reset_index(drop=True)
    features["bar_index"] = features.index
    features["date"] = features["time_utc"].dt.date
    features["atr_points"] = features["atr"] / point
    features["prev10_ret_points"] = (features["close"] - features["close"].shift(10)) / point
    features["body_atr"] = (features["close"] - features["open"]).abs() / features["atr"]
    features["body_points"] = (features["close"] - features["open"]).abs() / point
    features["range_points"] = (features["high"] - features["low"]) / point
    return features


def build_components(features: pd.DataFrame, point: float, slippage_points: int, spread_threshold: int) -> dict[str, pd.DataFrame]:
    ny = first_setups.filter_spread(first_setups.filter_session(features, "NY Open"), spread_threshold)
    signal = first_setups.generate_signal_variants(ny)["ict_reversal:displacement"].reindex(ny.index).fillna(0).astype(int)
    signal_mask = signal != 0
    dir_prev10 = ny["prev10_ret_points"] * signal
    quality_mask = signal_mask & (ny["atr_points"] <= 279) & (dir_prev10 > -322)
    failed_mask = signal_mask & (ny["atr_points"] >= 450) & (dir_prev10 <= -100)

    core_entries = ny[signal_mask].copy()
    core_entries["signal"] = signal[signal_mask]

    quality_entries = ny[quality_mask].copy()
    quality_entries["signal"] = signal[quality_mask]

    failed_entries = ny[failed_mask].copy()
    failed_entries["signal"] = -signal[failed_mask]

    non_failed_entries = ny[signal_mask & ~failed_mask].copy()
    non_failed_entries["signal"] = signal[signal_mask & ~failed_mask]

    london_entries = build_london_reversal_entries(features, spread_threshold)
    retrace_entries = build_retrace_entries(features, spread_threshold)
    rsi_entries = build_rsi_london_entries(features, spread_threshold)

    return {
        "core_reversal": simulate_entries(features, core_entries, 450, 80, 15, point, slippage_points, "ict_reversal_core"),
        "quality_reversal": simulate_entries(features, quality_entries, 450, 80, 15, point, slippage_points, "ict_reversal_quality"),
        "failed_continuation": simulate_entries(features, failed_entries, 500, 150, 15, point, slippage_points, "failed_reversal_continuation"),
        "core_reversal_non_failed": simulate_entries(features, non_failed_entries, 450, 80, 15, point, slippage_points, "ict_reversal_non_failed"),
        "london_reversal": simulate_entries(features, london_entries, 450, 80, 10, point, slippage_points, "ict_london_reversal"),
        "displacement_retrace": simulate_retracement(features, retrace_entries, 500, 60, 15, 0.5, 10, point, slippage_points, "displacement_retrace"),
        "rsi_london": simulate_entries(features, rsi_entries, 400, 60, 15, point, slippage_points, "rsi_sweep_london"),
    }


def build_london_reversal_entries(features: pd.DataFrame, spread_threshold: int) -> pd.DataFrame:
    london = first_setups.filter_spread(first_setups.filter_session(features, "London Open"), spread_threshold)
    signal = first_setups.generate_signal_variants(london)["ict_reversal:displacement"].reindex(london.index).fillna(0).astype(int)
    mask = (signal != 0) & (london["body_atr"].notna()) & (london["atr_points"] <= 279) & (london["body_points"] <= 450)
    entries = london[mask].copy()
    entries["signal"] = signal[mask]
    return entries


def build_retrace_entries(features: pd.DataFrame, spread_threshold: int) -> pd.DataFrame:
    ny = first_setups.filter_spread(first_setups.filter_session(features, "NY Open"), spread_threshold)
    signal = first_setups.generate_signal_variants(ny)["ict_reversal:displacement"].reindex(ny.index).fillna(0).astype(int)
    mask = signal != 0
    entries = ny[mask].copy()
    entries["signal"] = signal[mask]
    return entries


def build_rsi_london_entries(features: pd.DataFrame, spread_threshold: int) -> pd.DataFrame:
    london = first_setups.filter_spread(first_setups.filter_session(features, "London Open"), spread_threshold)
    side = pd.Series(0, index=london.index)
    side[(london["rsi"] < 30) & london["sweep_low"]] = 1
    side[(london["rsi"] > 70) & london["sweep_high"]] = -1
    mask = (side != 0) & (london["atr_points"] <= 450) & (london["range_points"] <= 650)
    entries = london[mask].copy()
    entries["signal"] = side[mask]
    return entries


def simulate_entries(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    point: float,
    slippage_points: int,
    component: str,
) -> pd.DataFrame:
    if entries.empty:
        return pd.DataFrame()
    return simulate_from_prices(
        features=features,
        entries=entries,
        entry_prices=entries["close"].to_numpy(float),
        entry_bars=entries["bar_index"].to_numpy(int),
        target_points=target_points,
        stop_points=stop_points,
        horizon_bars=horizon_bars,
        point=point,
        slippage_points=slippage_points,
        component=component,
    )


def simulate_retracement(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    retrace: float,
    timeout_bars: int,
    point: float,
    slippage_points: int,
    component: str,
) -> pd.DataFrame:
    if entries.empty:
        return pd.DataFrame()
    highs = features["high"].to_numpy(float)
    lows = features["low"].to_numpy(float)
    filled_rows = []
    entry_prices = []
    entry_bars = []
    for row in entries.itertuples(index=False):
        signal = int(row.signal)
        body = abs(float(row.close) - float(row.open))
        if body <= 0:
            continue
        level = float(row.close) - signal * body * retrace
        start = int(row.bar_index) + 1
        for bar_idx in range(start, min(len(features), start + timeout_bars)):
            if signal == 1 and lows[bar_idx] <= level:
                filled_rows.append(row)
                entry_prices.append(level)
                entry_bars.append(bar_idx)
                break
            if signal == -1 and highs[bar_idx] >= level:
                filled_rows.append(row)
                entry_prices.append(level)
                entry_bars.append(bar_idx)
                break
    if not filled_rows:
        return pd.DataFrame()
    filled = pd.DataFrame(filled_rows)
    filled["entry_time_override"] = features.loc[entry_bars, "time_utc"].to_numpy()
    return simulate_from_prices(
        features=features,
        entries=filled,
        entry_prices=np.array(entry_prices, dtype=float),
        entry_bars=np.array(entry_bars, dtype=int),
        target_points=target_points,
        stop_points=stop_points,
        horizon_bars=horizon_bars,
        point=point,
        slippage_points=slippage_points,
        component=component,
    )


def simulate_from_prices(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    entry_prices: np.ndarray,
    entry_bars: np.ndarray,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    point: float,
    slippage_points: int,
    component: str,
) -> pd.DataFrame:
    highs = features["high"].to_numpy(float)
    lows = features["low"].to_numpy(float)
    records = []
    for pos, row in enumerate(entries.itertuples(index=False)):
        signal = int(row.signal)
        entry_bar = int(entry_bars[pos])
        entry_price = float(entry_prices[pos])
        target = entry_price + signal * target_points * point
        stop = entry_price - signal * stop_points * point
        outcome = 0
        exit_bar = min(len(features) - 1, entry_bar + horizon_bars)
        for future_idx in range(entry_bar + 1, min(len(features), entry_bar + horizon_bars + 1)):
            if signal == 1:
                hit_stop = lows[future_idx] <= stop
                hit_target = highs[future_idx] >= target
            else:
                hit_stop = highs[future_idx] >= stop
                hit_target = lows[future_idx] <= target
            if hit_stop:
                outcome = -1
                exit_bar = future_idx
                break
            if hit_target:
                outcome = 1
                exit_bar = future_idx
                break
        net_points = target_points - slippage_points * 2 if outcome == 1 else -stop_points - slippage_points * 2 if outcome == -1 else 0.0
        entry_time = getattr(row, "entry_time_override", getattr(row, "time_utc"))
        records.append(
            {
                "component": component,
                "entry_time": entry_time,
                "exit_time": features.loc[exit_bar, "time_utc"],
                "date": pd.to_datetime(entry_time, utc=True).date(),
                "signal": signal,
                "direction": "LONG" if signal == 1 else "SHORT",
                "target_points": target_points,
                "stop_points": stop_points,
                "horizon_bars": horizon_bars,
                "outcome": outcome,
                "net_points": net_points,
                "source_bar_index": getattr(row, "bar_index"),
            }
        )
    return pd.DataFrame.from_records(records).sort_values(["entry_time", "component"]).reset_index(drop=True)


def combine_components(*frames: pd.DataFrame) -> pd.DataFrame:
    non_empty = [frame for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return pd.DataFrame()
    return pd.concat(non_empty, ignore_index=True).sort_values(["entry_time", "component"]).reset_index(drop=True)


def summarize(variant: str, description: str, spread_threshold: int, trades: pd.DataFrame, active_days: int) -> VariantMetrics:
    if trades.empty:
        return VariantMetrics(variant, description, spread_threshold, 0, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)
    trades = trades.sort_values(["entry_time", "component"]).copy()
    wins = int((trades["outcome"] == 1).sum())
    losses = int((trades["outcome"] == -1).sum())
    unresolved = int((trades["outcome"] == 0).sum())
    gross_profit = float(trades.loc[trades["net_points"] > 0, "net_points"].sum())
    gross_loss = float(-trades.loc[trades["net_points"] < 0, "net_points"].sum())
    total = float(trades["net_points"].sum())
    max_dd = max_drawdown(trades["net_points"].to_numpy(float))
    daily = trades.groupby("date")["net_points"].sum()
    return VariantMetrics(
        variant=variant,
        description=description,
        spread_threshold=spread_threshold,
        trades=int(len(trades)),
        trades_per_active_day=float(len(trades) / active_days) if active_days else 0.0,
        wins=wins,
        losses=losses,
        unresolved=unresolved,
        win_rate=float(wins / len(trades)),
        profit_factor=float(gross_profit / gross_loss) if gross_loss else float("inf") if gross_profit else 0.0,
        expectancy_points=float(trades["net_points"].mean()),
        total_points=total,
        max_dd_points=max_dd,
        profit_to_dd=float(total / max_dd) if max_dd else float("inf") if total > 0 else 0.0,
        max_losing_streak=max_losing_streak(trades["net_points"].to_numpy(float)),
        active_trade_days=int(len(daily)),
        worst_day_points=float(daily.min()) if not daily.empty else 0.0,
        best_day_points=float(daily.max()) if not daily.empty else 0.0,
    )


def max_drawdown(points: np.ndarray) -> float:
    equity = np.r_[0.0, np.cumsum(points)]
    peaks = np.maximum.accumulate(equity)
    return float(np.max(peaks - equity))


def max_losing_streak(points: np.ndarray) -> int:
    streak = 0
    maximum = 0
    for value in points:
        if value < 0:
            streak += 1
            maximum = max(maximum, streak)
        elif value > 0:
            streak = 0
    return maximum


if __name__ == "__main__":
    raise SystemExit(main())
