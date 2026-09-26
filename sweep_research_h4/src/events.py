from __future__ import annotations

import hashlib
from collections import defaultdict

import numpy as np
import pandas as pd

from .config import Config
from .levels import Level, build_levels
from .primitives import atr

_LEVEL_PRIORITY = {
    "equal_high": 0,
    "equal_low": 0,
    "swing_high_20": 1,
    "swing_low_20": 1,
    "swing_high_10": 2,
    "swing_low_10": 2,
    "swing_high_5": 3,
    "swing_low_5": 3,
}


def _level_type(level: Level) -> str:
    return level.kind if level.kind.startswith("equal") else f"{level.kind}_{level.swing_n}"


def detect_events(bars: pd.DataFrame, cfg: Config, symbol: str) -> pd.DataFrame:
    columns = [
        "symbol",
        "bar_ts_utc",
        "event_ts_utc",
        "dir",
        "level_type",
        "level_price",
        "extreme_price",
        "penetration_atr",
        "below_min_penetration",
        "swing_n",
        "event_id",
        "bar_idx",
        "level_form_idx",
        "level_confirm_idx",
        "trade_sign",
    ]
    if bars.empty:
        return pd.DataFrame(columns=columns)
    levels = build_levels(bars, cfg)
    by_confirm: dict[int, list[Level]] = defaultdict(list)
    for level in levels:
        by_confirm[level.confirm_idx].append(level)
    atr_values = atr(bars, cfg.atr_period).to_numpy(float)
    active: list[Level] = []
    rows: list[dict[str, object]] = []
    for idx, bar in enumerate(bars.itertuples(index=False)):
        active.extend(by_confirm.get(idx - 1, []))
        kept: list[Level] = []
        for level in active:
            age = idx - level.confirm_idx
            if age > cfg.max_level_age_bars:
                continue
            if level.kind.endswith("high"):
                if bar.close > level.price:
                    continue
                swept = bar.high > level.price
                direction = "sweep_high"
                extreme = float(bar.high)
            else:
                if bar.close < level.price:
                    continue
                swept = bar.low < level.price
                direction = "sweep_low"
                extreme = float(bar.low)
            if swept:
                denominator = atr_values[idx - 1] if idx > 0 else np.nan
                penetration = abs(extreme - level.price) / denominator if denominator > 0 else np.nan
                rows.append(
                    {
                        "symbol": symbol,
                        "bar_ts_utc": bar.ts,
                        "event_ts_utc": bar.ts + pd.Timedelta(hours=4),
                        "dir": direction,
                        "level_type": _level_type(level),
                        "level_price": float(level.price),
                        "extreme_price": extreme,
                        "penetration_atr": float(penetration),
                        "below_min_penetration": bool(
                            not np.isfinite(penetration) or penetration < cfg.min_penetration_atr
                        ),
                        "swing_n": int(level.swing_n),
                        "event_id": hashlib.sha1(
                            f"{symbol}|{bar.ts}|{direction}|{_level_type(level)}|{level.price}".encode()
                        ).hexdigest()[:16],
                        "bar_idx": idx,
                        "level_form_idx": int(level.form_idx),
                        "level_confirm_idx": int(level.confirm_idx),
                        "trade_sign": 1 if direction == "sweep_high" else -1,
                    }
                )
            kept.append(level)
        active = kept
    return pd.DataFrame(rows, columns=columns)


def dedup_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = events.copy()
    out["_priority"] = out["level_type"].map(_LEVEL_PRIORITY).fillna(99)
    out = out.sort_values(["symbol", "event_ts_utc", "dir", "_priority", "level_price"])
    return out.drop_duplicates(["symbol", "event_ts_utc", "dir"], keep="first").drop(columns="_priority")
