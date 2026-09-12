from __future__ import annotations

import argparse
import itertools
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
class Metrics:
    setup_id: str
    family: str
    params: str
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
    parser = argparse.ArgumentParser(description="Optimize complementary XAUUSD setup families around the ICT NY edge.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--bars", default="data/bars/XAUUSD/1min.parquet")
    parser.add_argument("--top-per-family", type=int, default=30)
    parser.add_argument("--slippage-points", type=int, default=5)
    args = parser.parse_args()

    config = load_config(args.config)
    point = first_setups.exported_point(config) or config.market.default_point
    bars = pd.read_parquet(args.bars).drop_duplicates("time_utc").sort_values("time_utc").reset_index(drop=True)
    bars["time_utc"] = pd.to_datetime(bars["time_utc"], utc=True)
    features = prepare_features(bars, config)
    active_days = features["date"].nunique()

    baseline_trades = build_baseline_trades(features, point, args.slippage_points)
    quality_baseline_trades = build_quality_baseline_trades(features, point, args.slippage_points)
    baseline_metrics = summarize_trades(
        "baseline_ict_ny_core_450_80_h15",
        "baseline",
        "target=450;stop=80;horizon=15;session=NY Open;spread<=20",
        baseline_trades,
        active_days,
    )
    quality_baseline_metrics = summarize_trades(
        "baseline_ict_ny_quality_450_80_h15_atr279_prev10",
        "baseline",
        "target=450;stop=80;horizon=15;session=NY Open;spread<=20;atr<=279;dir_prev10>-322",
        quality_baseline_trades,
        active_days,
    )

    candidate_trades: list[pd.DataFrame] = []
    candidate_trades.extend(scan_ict_london_reversal(features, point, args.slippage_points))
    candidate_trades.extend(scan_retracement_after_displacement(features, point, args.slippage_points))
    candidate_trades.extend(scan_failed_reversal_continuation(features, point, args.slippage_points))
    candidate_trades.extend(scan_asian_range_sweep_london(features, point, args.slippage_points))
    candidate_trades.extend(scan_rsi_sweep_london(features, point, args.slippage_points))

    metrics: list[Metrics] = []
    for trades in candidate_trades:
        if trades.empty:
            continue
        first = trades.iloc[0]
        metrics.append(
            summarize_trades(
                setup_id=str(first["setup_id"]),
                family=str(first["family"]),
                params=str(first["params"]),
                trades=trades,
                active_days=active_days,
            )
        )

    metrics_df = pd.DataFrame([asdict(metric) for metric in metrics])
    if metrics_df.empty:
        raise RuntimeError("No complementary setup candidates were generated.")

    metrics_df = metrics_df.sort_values(
        ["profit_factor", "expectancy_points", "profit_to_dd", "trades"],
        ascending=[False, False, False, False],
    )
    top_metrics = (
        metrics_df.groupby("family", group_keys=False)
        .head(args.top_per_family)
        .sort_values(["profit_factor", "expectancy_points"], ascending=[False, False])
    )

    trade_by_id = {str(trades.iloc[0]["setup_id"]): trades for trades in candidate_trades if not trades.empty}
    combo_rows = []
    baseline_days = daily_points(baseline_trades)
    baseline_trade_times = set(pd.to_datetime(baseline_trades["entry_time"], utc=True))
    for row in top_metrics.itertuples(index=False):
        candidate = trade_by_id[row.setup_id]
        replace_overlap = row.family == "failed_reversal_continuation"
        combo = combine_trades(baseline_trades, candidate, replace_overlap=replace_overlap)
        combo_metric = summarize_trades(
            setup_id=f"combo__{row.setup_id}",
            family="combo",
            params=f"baseline + {row.params}",
            trades=combo,
            active_days=active_days,
        )
        candidate_days = daily_points(candidate)
        shared_loss_days = int(len(set(baseline_days[baseline_days < 0].index) & set(candidate_days[candidate_days < 0].index)))
        overlap_entries = int(sum(pd.to_datetime(candidate["entry_time"], utc=True).isin(baseline_trade_times)))
        combo_payload = asdict(combo_metric)
        overlap_allowed = overlap_entries <= 1 or replace_overlap
        combo_payload.update(
            {
                "candidate_setup_id": row.setup_id,
                "candidate_family": row.family,
                "candidate_pf": row.profit_factor,
                "candidate_expectancy": row.expectancy_points,
                "candidate_profit_to_dd": row.profit_to_dd,
                "candidate_trades": row.trades,
                "overlap_entries": overlap_entries,
                "overlap_handling": "replace" if replace_overlap else "add",
                "shared_losing_days": shared_loss_days,
                "delta_total_points": combo_metric.total_points - baseline_metrics.total_points,
                "delta_max_dd_points": combo_metric.max_dd_points - baseline_metrics.max_dd_points,
                "delta_losing_streak": combo_metric.max_losing_streak - baseline_metrics.max_losing_streak,
                "passes_core_filters": bool(
                    row.profit_factor > 1.30
                    and row.expectancy_points > 20
                    and row.profit_to_dd > baseline_metrics.profit_to_dd
                    and overlap_allowed
                    and combo_metric.max_losing_streak <= baseline_metrics.max_losing_streak
                    and shared_loss_days == 0
                ),
            }
        )
        combo_rows.append(combo_payload)

    combo_df = pd.DataFrame(combo_rows).sort_values(
        [
            "passes_core_filters",
            "profit_to_dd",
            "profit_factor",
            "delta_max_dd_points",
            "expectancy_points",
        ],
        ascending=[False, False, False, True, False],
    )

    output_dir = config.storage.reports_dir / config.symbol
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_df.to_csv(output_dir / "complementary_setup_candidates.csv", index=False)
    top_metrics.to_csv(output_dir / "complementary_setup_best_by_family.csv", index=False)
    combo_df.to_csv(output_dir / "complementary_setup_combo_summary.csv", index=False)
    pd.DataFrame([asdict(baseline_metrics), asdict(quality_baseline_metrics)]).to_csv(output_dir / "complementary_setup_baseline.csv", index=False)
    write_top_trades(output_dir, trade_by_id, combo_df)

    print("Baseline")
    print(pd.DataFrame([asdict(baseline_metrics), asdict(quality_baseline_metrics)]).to_string(index=False))
    print("\nBest by family")
    print(top_metrics.groupby("family", group_keys=False).head(5).to_string(index=False))
    print("\nBest combinations")
    print(combo_df.head(20).to_string(index=False))
    print(f"\nReports written to: {output_dir}")
    return 0


def prepare_features(bars: pd.DataFrame, config) -> pd.DataFrame:
    features = add_indicators(bars, config.features)
    features = first_setups.add_session_columns(features, config.raw.get("sessions", {}))
    features = features.reset_index(drop=True)
    features["bar_index"] = features.index
    features["date"] = features["time_utc"].dt.date
    features["local_minutes"] = features["local_hour"] * 60 + features["local_minute"]
    point = first_setups.exported_point(config) or config.market.default_point

    candle_range = (features["high"] - features["low"]).replace(0, np.nan)
    body = features["close"] - features["open"]
    features["body_points"] = body.abs() / point
    features["range_points"] = candle_range / point
    features["atr_points"] = features["atr"] / point
    features["body_ratio"] = body.abs() / candle_range
    features["range_atr"] = candle_range / features["atr"]
    features["body_atr"] = body.abs() / features["atr"]
    features["upper_wick_ratio"] = (features["high"] - features[["open", "close"]].max(axis=1)) / candle_range
    features["lower_wick_ratio"] = (features[["open", "close"]].min(axis=1) - features["low"]) / candle_range
    features["prev5_ret_points"] = (features["close"] - features["close"].shift(5)) / point
    features["prev10_ret_points"] = (features["close"] - features["close"].shift(10)) / point
    features["prev20_ret_points"] = (features["close"] - features["close"].shift(20)) / point
    features["vwap_dist_points"] = (features["close"] - features["vwap"]) / point
    features["ema_slow_dist_points"] = (features["close"] - features["ema_slow"]) / point
    features["vol_rel20"] = features["tick_volume"] / features["tick_volume"].rolling(20, min_periods=10).mean()
    features["asian_high"], features["asian_low"], features["asian_range_points"] = asian_range_columns(features, point)
    return features


def asian_range_columns(df: pd.DataFrame, point: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    asia = df[(df["local_minutes"] >= 0) & (df["local_minutes"] < 9 * 60)]
    grouped = asia.groupby("date").agg(asian_high=("high", "max"), asian_low=("low", "min"))
    result = df[["date"]].join(grouped, on="date")
    asian_range = (result["asian_high"] - result["asian_low"]) / point
    return result["asian_high"], result["asian_low"], asian_range


def build_baseline_trades(features: pd.DataFrame, point: float, slippage_points: int) -> pd.DataFrame:
    df = first_setups.filter_spread(first_setups.filter_session(features, "NY Open"), 20)
    signal = first_setups.generate_signal_variants(df)["ict_reversal:displacement"]
    side = signal.reindex(df.index).fillna(0).astype(int)
    mask = side != 0
    entries = df[mask].copy()
    entries["signal"] = side[mask]
    return simulate_entries(
        features,
        entries,
        target_points=450,
        stop_points=80,
        horizon_bars=15,
        point=point,
        slippage_points=slippage_points,
        setup_id="baseline_ict_ny_core_450_80_h15",
        family="baseline",
        params="target=450;stop=80;horizon=15;session=NY Open;spread<=20",
    )


def build_quality_baseline_trades(features: pd.DataFrame, point: float, slippage_points: int) -> pd.DataFrame:
    df = first_setups.filter_spread(first_setups.filter_session(features, "NY Open"), 20)
    signal = first_setups.generate_signal_variants(df)["ict_reversal:displacement"]
    side = signal.reindex(df.index).fillna(0).astype(int)
    dir_prev10 = df["prev10_ret_points"] * side
    mask = (side != 0) & (df["atr_points"] <= 279) & (dir_prev10 > -322)
    entries = df[mask].copy()
    entries["signal"] = side[mask]
    return simulate_entries(
        features,
        entries,
        target_points=450,
        stop_points=80,
        horizon_bars=15,
        point=point,
        slippage_points=slippage_points,
        setup_id="baseline_ict_ny_quality_450_80_h15_atr279_prev10",
        family="baseline",
        params="target=450;stop=80;horizon=15;session=NY Open;spread<=20;atr<=279;dir_prev10>-322",
    )


def scan_ict_london_reversal(features: pd.DataFrame, point: float, slippage_points: int) -> list[pd.DataFrame]:
    outputs = []
    df = first_setups.filter_spread(first_setups.filter_session(features, "London Open"), 20)
    signal = first_setups.generate_signal_variants(df)["ict_reversal:displacement"]
    side = signal.reindex(df.index).fillna(0).astype(int)
    dir_prev10 = df["prev10_ret_points"] * side
    for target, stop, horizon, atr_max, prev10_min, body_max in itertools.product(
        [300, 350, 400, 450],
        [60, 80, 100, 120],
        [10, 15, 30],
        [279, 350, 450, None],
        [-500, -322, -100, None],
        [450, 650, 850, None],
    ):
        mask = side != 0
        if atr_max is not None:
            mask &= df["atr_points"] <= atr_max
        if prev10_min is not None:
            mask &= dir_prev10 > prev10_min
        if body_max is not None:
            mask &= df["body_points"] <= body_max
        entries = df[mask].copy()
        if len(entries) < 5:
            continue
        entries["signal"] = side[mask]
        params = f"target={target};stop={stop};horizon={horizon};session=London Open;spread<=20;atr<={atr_max};dir_prev10>{prev10_min};body<={body_max}"
        outputs.append(
            simulate_entries(features, entries, target, stop, horizon, point, slippage_points, make_id("ict_london_reversal", params), "ict_london_reversal", params)
        )
    return outputs


def scan_retracement_after_displacement(features: pd.DataFrame, point: float, slippage_points: int) -> list[pd.DataFrame]:
    outputs = []
    for session_name in ["London Open", "NY Open"]:
        df = first_setups.filter_spread(first_setups.filter_session(features, session_name), 20)
        signal = first_setups.generate_signal_variants(df)["ict_reversal:displacement"]
        side = signal.reindex(df.index).fillna(0).astype(int)
        dir_prev10 = df["prev10_ret_points"] * side
        for target, stop, horizon, retrace, timeout, atr_max, prev10_min in itertools.product(
            [300, 350, 400, 450, 500],
            [60, 80, 100],
            [15, 30, 45],
            [0.25, 0.50, 0.618],
            [3, 5, 10],
            [279, 350, 450, None],
            [-500, -322, -100, None],
        ):
            mask = side != 0
            if atr_max is not None:
                mask &= df["atr_points"] <= atr_max
            if prev10_min is not None:
                mask &= dir_prev10 > prev10_min
            entries = df[mask].copy()
            if len(entries) < 5:
                continue
            entries["signal"] = side[mask]
            params = f"target={target};stop={stop};horizon={horizon};session={session_name};spread<=20;retrace={retrace};timeout={timeout};atr<={atr_max};dir_prev10>{prev10_min}"
            trades = simulate_retracement_entries(features, entries, target, stop, horizon, retrace, timeout, point, slippage_points, make_id("displacement_retrace", params), "displacement_retrace", params)
            if len(trades) >= 5:
                outputs.append(trades)
    return outputs


def scan_failed_reversal_continuation(features: pd.DataFrame, point: float, slippage_points: int) -> list[pd.DataFrame]:
    outputs = []
    for session_name in ["London Open", "NY Open"]:
        df = first_setups.filter_spread(first_setups.filter_session(features, session_name), 20)
        reversal = first_setups.generate_signal_variants(df)["ict_reversal:displacement"].reindex(df.index).fillna(0).astype(int)
        dir_prev10 = df["prev10_ret_points"] * reversal
        for target, stop, horizon, atr_min, prev10_max, body_atr_min, require_trend in itertools.product(
            [250, 300, 350, 400, 500],
            [80, 100, 120, 150, 200],
            [15, 30, 45, 60],
            [279, 350, 450],
            [-500, -322, -100],
            [1.2, 1.5, 1.8, None],
            [False, True],
        ):
            mask = (reversal != 0) & (df["atr_points"] >= atr_min) & (dir_prev10 <= prev10_max)
            if body_atr_min is not None:
                mask &= df["body_atr"] >= body_atr_min
            continuation = -reversal
            if require_trend:
                mask &= np.where(continuation == 1, df["trend_up"] & (df["close"] > df["vwap"]), df["trend_down"] & (df["close"] < df["vwap"]))
            entries = df[mask].copy()
            if len(entries) < 5:
                continue
            entries["signal"] = continuation[mask]
            params = f"target={target};stop={stop};horizon={horizon};session={session_name};spread<=20;atr>={atr_min};dir_prev10<={prev10_max};body_atr>={body_atr_min};trend={require_trend}"
            outputs.append(
                simulate_entries(features, entries, target, stop, horizon, point, slippage_points, make_id("failed_reversal_continuation", params), "failed_reversal_continuation", params)
            )
    return outputs


def scan_asian_range_sweep_london(features: pd.DataFrame, point: float, slippage_points: int) -> list[pd.DataFrame]:
    outputs = []
    df = first_setups.filter_spread(first_setups.filter_session(features, "London Open"), 20).copy()
    base_long = (df["low"] < df["asian_low"]) & (df["close"] > df["asian_low"])
    base_short = (df["high"] > df["asian_high"]) & (df["close"] < df["asian_high"])
    for confirm in ["none", "candle", "displacement"]:
        if confirm == "displacement":
            long_signal = base_long & df["displacement_up"]
            short_signal = base_short & df["displacement_down"]
        elif confirm == "candle":
            long_signal = base_long & (df["close"] > df["open"])
            short_signal = base_short & (df["close"] < df["open"])
        else:
            long_signal = base_long
            short_signal = base_short
        side = pd.Series(0, index=df.index)
        side[long_signal] = 1
        side[short_signal] = -1
        dir_prev10 = df["prev10_ret_points"] * side
        for target, stop, horizon, asia_max, atr_max, prev10_min in itertools.product(
            [250, 300, 350, 400, 450],
            [60, 80, 100, 120],
            [10, 15, 30, 45],
            [800, 1200, 1800, 2400, None],
            [279, 350, 450, None],
            [-500, -322, -100, None],
        ):
            mask = (side != 0) & df["asian_high"].notna() & df["asian_low"].notna()
            if asia_max is not None:
                mask &= df["asian_range_points"] <= asia_max
            if atr_max is not None:
                mask &= df["atr_points"] <= atr_max
            if prev10_min is not None:
                mask &= dir_prev10 > prev10_min
            entries = df[mask].copy()
            if len(entries) < 5:
                continue
            entries["signal"] = side[mask]
            params = f"target={target};stop={stop};horizon={horizon};session=London Open;spread<=20;confirm={confirm};asian_range<={asia_max};atr<={atr_max};dir_prev10>{prev10_min}"
            outputs.append(
                simulate_entries(features, entries, target, stop, horizon, point, slippage_points, make_id("asian_range_sweep_london", params), "asian_range_sweep_london", params)
            )
    return outputs


def scan_rsi_sweep_london(features: pd.DataFrame, point: float, slippage_points: int) -> list[pd.DataFrame]:
    outputs = []
    df = first_setups.filter_spread(first_setups.filter_session(features, "London Open"), 20).copy()
    for rsi_low, rsi_high, target, stop, horizon, atr_max, range_max, prev10_min in itertools.product(
        [20, 25, 30],
        [80, 75, 70],
        [250, 300, 350, 400],
        [60, 80, 100, 120],
        [10, 15, 30],
        [279, 350, 450, None],
        [450, 650, 850, None],
        [-500, -322, -100, None],
    ):
        if rsi_low + rsi_high != 100:
            continue
        side = pd.Series(0, index=df.index)
        side[(df["rsi"] < rsi_low) & df["sweep_low"]] = 1
        side[(df["rsi"] > rsi_high) & df["sweep_high"]] = -1
        dir_prev10 = df["prev10_ret_points"] * side
        mask = side != 0
        if atr_max is not None:
            mask &= df["atr_points"] <= atr_max
        if range_max is not None:
            mask &= df["range_points"] <= range_max
        if prev10_min is not None:
            mask &= dir_prev10 > prev10_min
        entries = df[mask].copy()
        if len(entries) < 5:
            continue
        entries["signal"] = side[mask]
        params = f"target={target};stop={stop};horizon={horizon};session=London Open;spread<=20;rsi={rsi_low}/{rsi_high};atr<={atr_max};range<={range_max};dir_prev10>{prev10_min}"
        outputs.append(
            simulate_entries(features, entries, target, stop, horizon, point, slippage_points, make_id("rsi_sweep_london", params), "rsi_sweep_london", params)
        )
    return outputs


def simulate_entries(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    point: float,
    slippage_points: int,
    setup_id: str,
    family: str,
    params: str,
) -> pd.DataFrame:
    return simulate_from_entry_rows(features, entries, entries["close"].to_numpy(float), entries["bar_index"].to_numpy(int), target_points, stop_points, horizon_bars, point, slippage_points, setup_id, family, params)


def simulate_retracement_entries(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    retrace: float,
    timeout_bars: int,
    point: float,
    slippage_points: int,
    setup_id: str,
    family: str,
    params: str,
) -> pd.DataFrame:
    highs = features["high"].to_numpy(float)
    lows = features["low"].to_numpy(float)
    entry_prices = []
    entry_bars = []
    filled_rows = []
    for row in entries.itertuples(index=False):
        signal = int(row.signal)
        body = abs(float(row.close) - float(row.open))
        if body <= 0:
            continue
        level = float(row.close) - signal * body * retrace
        start = int(row.bar_index) + 1
        last = min(len(features), start + timeout_bars)
        fill_bar = None
        for bar_idx in range(start, last):
            if signal == 1 and lows[bar_idx] <= level:
                fill_bar = bar_idx
                break
            if signal == -1 and highs[bar_idx] >= level:
                fill_bar = bar_idx
                break
        if fill_bar is None:
            continue
        filled_rows.append(row)
        entry_prices.append(level)
        entry_bars.append(fill_bar)
    if not filled_rows:
        return pd.DataFrame()
    filled = pd.DataFrame(filled_rows)
    filled["bar_index"] = entry_bars
    filled["entry_time_override"] = features.loc[entry_bars, "time_utc"].to_numpy()
    return simulate_from_entry_rows(features, filled, np.array(entry_prices, dtype=float), np.array(entry_bars, dtype=int), target_points, stop_points, horizon_bars, point, slippage_points, setup_id, family, params)


def simulate_from_entry_rows(
    features: pd.DataFrame,
    entries: pd.DataFrame,
    entry_prices: np.ndarray,
    entry_bars: np.ndarray,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    point: float,
    slippage_points: int,
    setup_id: str,
    family: str,
    params: str,
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
        bars_to_exit = np.nan
        for future_idx in range(entry_bar + 1, min(len(features), entry_bar + horizon_bars + 1)):
            if signal == 1:
                hit_target = highs[future_idx] >= target
                hit_stop = lows[future_idx] <= stop
            else:
                hit_target = lows[future_idx] <= target
                hit_stop = highs[future_idx] >= stop
            if hit_stop:
                outcome = -1
                exit_bar = future_idx
                bars_to_exit = future_idx - entry_bar
                break
            if hit_target:
                outcome = 1
                exit_bar = future_idx
                bars_to_exit = future_idx - entry_bar
                break
        net_points = target_points - slippage_points * 2 if outcome == 1 else -stop_points - slippage_points * 2 if outcome == -1 else 0.0
        entry_time = getattr(row, "entry_time_override", getattr(row, "time_utc"))
        records.append(
            {
                "setup_id": setup_id,
                "family": family,
                "params": params,
                "entry_time": entry_time,
                "exit_time": features.loc[exit_bar, "time_utc"],
                "date": pd.to_datetime(entry_time, utc=True).date(),
                "signal": signal,
                "direction": "LONG" if signal == 1 else "SHORT",
                "entry_price": entry_price,
                "target_points": target_points,
                "stop_points": stop_points,
                "horizon_bars": horizon_bars,
                "outcome": outcome,
                "net_points": net_points,
                "bars_to_exit": bars_to_exit,
                "source_bar_index": getattr(row, "bar_index"),
            }
        )
    return pd.DataFrame.from_records(records).sort_values("entry_time").reset_index(drop=True)


def summarize_trades(setup_id: str, family: str, params: str, trades: pd.DataFrame, active_days: int) -> Metrics:
    trades = trades.sort_values("entry_time").copy()
    if trades.empty:
        return Metrics(setup_id, family, params, 0, 0.0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0.0, 0.0)
    wins = int((trades["outcome"] == 1).sum())
    losses = int((trades["outcome"] == -1).sum())
    unresolved = int((trades["outcome"] == 0).sum())
    gross_profit = float(trades.loc[trades["net_points"] > 0, "net_points"].sum())
    gross_loss = float(-trades.loc[trades["net_points"] < 0, "net_points"].sum())
    total = float(trades["net_points"].sum())
    max_dd = max_drawdown(trades["net_points"].to_numpy(float))
    daily = daily_points(trades)
    return Metrics(
        setup_id=setup_id,
        family=family,
        params=params,
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


def combine_trades(left: pd.DataFrame, right: pd.DataFrame, replace_overlap: bool = False) -> pd.DataFrame:
    if replace_overlap:
        right_times = set(pd.to_datetime(right["entry_time"], utc=True))
        left = left[~pd.to_datetime(left["entry_time"], utc=True).isin(right_times)].copy()
    combined = pd.concat([left, right], ignore_index=True)
    return combined.sort_values(["entry_time", "setup_id"]).reset_index(drop=True)


def daily_points(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(trades["entry_time"], utc=True).dt.date
    return trades.groupby(dates)["net_points"].sum()


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


def make_id(family: str, params: str) -> str:
    safe = (
        params.replace(";", "__")
        .replace("=", "-")
        .replace(" ", "")
        .replace("/", "_")
        .replace("<", "lt")
        .replace(">", "gt")
        .replace(".", "p")
    )
    return f"{family}__{safe}"


def write_top_trades(output_dir: Path, trade_by_id: dict[str, pd.DataFrame], combo_df: pd.DataFrame) -> None:
    top_ids = combo_df.head(20)["candidate_setup_id"].tolist()
    frames = []
    for setup_id in top_ids:
        trades = trade_by_id.get(setup_id)
        if trades is not None and not trades.empty:
            frames.append(trades)
    if frames:
        pd.concat(frames, ignore_index=True).to_csv(output_dir / "complementary_setup_top_trades.csv", index=False)


if __name__ == "__main__":
    raise SystemExit(main())
