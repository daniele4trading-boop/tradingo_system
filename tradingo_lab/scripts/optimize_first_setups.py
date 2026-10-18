from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradingo_lab.backtest import evaluate_signal
from tradingo_lab.config import ensure_storage_dirs, load_config
from tradingo_lab.indicators import add_indicators
from tradingo_lab.labeling import label_target_before_stop


@dataclass(frozen=True)
class Candidate:
    setup: str
    variant: str
    target_points: int
    stop_points: int
    horizon_bars: int
    session: str
    max_spread: int | None
    trades: int
    wins: int
    losses: int
    unresolved: int
    resolved_rate: float
    win_rate: float
    profit_factor: float
    expectancy_points: float
    avg_bars_to_exit: float


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize first objective setup families on XAUUSD bars.")
    parser.add_argument("--config", default="config/research_config.json")
    parser.add_argument("--bars", required=True, help="Bars parquet file, e.g. data/bars/XAUUSD/1min.parquet")
    parser.add_argument("--min-trades", type=int, default=15)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    config = load_config(args.config)
    ensure_storage_dirs(config)
    point = exported_point(config) or config.market.default_point

    bars = pd.read_parquet(args.bars)
    features = add_indicators(bars, config.features)
    features = add_session_columns(features, config.raw.get("sessions", {}))

    candidates: list[Candidate] = []
    for target_points, stop_points, horizon_bars in itertools.product(
        [200, 250, 300, 350, 400],
        [100, 150, 200, 250, 300],
        [15, 30, 60, 120],
    ):
        labeled = label_target_before_stop(
            features,
            point=point,
            target_points=target_points,
            stop_points=stop_points,
            horizon_bars=horizon_bars,
        )
        for session_name in ["all", "London Open", "NY Open"]:
            session_df = filter_session(labeled, session_name)
            for max_spread in [None, 20, 25, 30]:
                filtered = filter_spread(session_df, max_spread)
                candidates.extend(evaluate_variants(filtered, target_points, stop_points, horizon_bars, session_name, max_spread))

    result = pd.DataFrame([asdict(candidate) for candidate in candidates])
    if result.empty:
        raise RuntimeError("No optimization candidates were generated.")

    result = result[(result["trades"] >= args.min_trades) & ((result["wins"] + result["losses"]) > 0)].copy()
    if result.empty:
        raise RuntimeError("No candidates passed the minimum trade/resolved filters.")
    result = result.sort_values(
        ["profit_factor", "expectancy_points", "trades"],
        ascending=[False, False, False],
    )

    output_dir = config.storage.reports_dir / config.symbol
    output_dir.mkdir(parents=True, exist_ok=True)
    all_path = output_dir / "optimization_candidates.csv"
    top_path = output_dir / "optimization_top.csv"
    result.to_csv(all_path, index=False)
    result.head(args.top).to_csv(top_path, index=False)

    print(result.head(args.top).to_string(index=False))
    print(f"All candidates: {all_path}")
    print(f"Top candidates: {top_path}")
    return 0


def evaluate_variants(
    df: pd.DataFrame,
    target_points: int,
    stop_points: int,
    horizon_bars: int,
    session_name: str,
    max_spread: int | None,
) -> list[Candidate]:
    if df.empty:
        return []

    variants = []
    for variant_name, signal in generate_signal_variants(df).items():
        test_df = df.copy()
        test_df["candidate_signal"] = signal
        metric, _ = evaluate_signal(test_df, "candidate_signal", slippage_points=5)
        variants.append(
            Candidate(
                setup=variant_name.split(":", 1)[0],
                variant=variant_name,
                target_points=target_points,
                stop_points=stop_points,
                horizon_bars=horizon_bars,
                session=session_name,
                max_spread=max_spread,
                trades=metric.trades,
                wins=metric.wins,
                losses=metric.losses,
                unresolved=metric.unresolved,
                resolved_rate=(metric.wins + metric.losses) / metric.trades if metric.trades else 0.0,
                win_rate=metric.win_rate,
                profit_factor=metric.profit_factor,
                expectancy_points=metric.expectancy_points,
                avg_bars_to_exit=metric.avg_bars_to_exit,
            )
        )
    return variants


def generate_signal_variants(df: pd.DataFrame) -> dict[str, pd.Series]:
    variants: dict[str, pd.Series] = {}

    for confirm in ["any", "displacement", "fvg", "both"]:
        long_confirm, short_confirm = confirmation_masks(df, confirm)
        signal = pd.Series(0, index=df.index)
        signal[df["sweep_low"] & long_confirm] = 1
        signal[df["sweep_high"] & short_confirm] = -1
        variants[f"ict_reversal:{confirm}"] = signal

    for rsi_low, rsi_high in [(20, 80), (25, 75), (30, 70), (35, 65)]:
        signal = pd.Series(0, index=df.index)
        signal[(df["rsi"] < rsi_low) & df["sweep_low"]] = 1
        signal[(df["rsi"] > rsi_high) & df["sweep_high"]] = -1
        variants[f"rsi_reversion:sweep_{rsi_low}_{rsi_high}"] = signal

        signal_no_sweep = pd.Series(0, index=df.index)
        signal_no_sweep[df["rsi"] < rsi_low] = 1
        signal_no_sweep[df["rsi"] > rsi_high] = -1
        variants[f"rsi_reversion:plain_{rsi_low}_{rsi_high}"] = signal_no_sweep

    for adx_min in [12, 15, 20, 25]:
        for require_displacement in [False, True]:
            signal = pd.Series(0, index=df.index)
            long_mask = df["trend_up"] & (df["close"] > df["vwap"]) & (df["adx"] > adx_min)
            short_mask = df["trend_down"] & (df["close"] < df["vwap"]) & (df["adx"] > adx_min)
            if require_displacement:
                long_mask &= df["displacement_up"]
                short_mask &= df["displacement_down"]
            signal[long_mask] = 1
            signal[short_mask] = -1
            suffix = "disp" if require_displacement else "nodisp"
            variants[f"trend_continuation:adx{adx_min}_{suffix}"] = signal

    return variants


def confirmation_masks(df: pd.DataFrame, confirm: str) -> tuple[pd.Series, pd.Series]:
    if confirm == "any":
        return pd.Series(True, index=df.index), pd.Series(True, index=df.index)
    if confirm == "displacement":
        return df["displacement_up"], df["displacement_down"]
    if confirm == "fvg":
        return df["fvg_bull"], df["fvg_bear"]
    if confirm == "both":
        return df["displacement_up"] & df["fvg_bull"], df["displacement_down"] & df["fvg_bear"]
    raise ValueError(f"Unsupported confirm variant: {confirm}")


def add_session_columns(df: pd.DataFrame, session_config: dict) -> pd.DataFrame:
    result = df.copy()
    result["time_utc"] = pd.to_datetime(result["time_utc"], utc=True)
    timezone_name = session_config.get("timezone", "Europe/Rome")
    local_time = result["time_utc"].dt.tz_convert(ZoneInfo(timezone_name))
    result["local_hour"] = local_time.dt.hour
    result["local_minute"] = local_time.dt.minute
    result["session"] = "off"

    minutes = result["local_hour"] * 60 + result["local_minute"]
    for zone in session_config.get("kill_zones", []):
        start = parse_minutes(zone["start"])
        end = parse_minutes(zone["end"])
        mask = (minutes >= start) & (minutes < end)
        result.loc[mask, "session"] = zone["name"]
    return result


def filter_session(df: pd.DataFrame, session_name: str) -> pd.DataFrame:
    if session_name == "all":
        return df
    return df[df["session"] == session_name]


def filter_spread(df: pd.DataFrame, max_spread: int | None) -> pd.DataFrame:
    if max_spread is None:
        return df
    if "spread_points_mean" in df.columns:
        return df[df["spread_points_mean"] <= max_spread]
    if "spread" in df.columns:
        return df[df["spread"] <= max_spread]
    return df


def parse_minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def exported_point(config) -> float | None:
    metadata_path = config.storage.root / "metadata" / f"{config.symbol}_mt5_metadata.json"
    if not metadata_path.exists():
        return None
    import json

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    symbol_info = metadata.get("symbol") or {}
    point = symbol_info.get("point")
    return float(point) if point else None


if __name__ == "__main__":
    raise SystemExit(main())
