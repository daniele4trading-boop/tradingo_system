from __future__ import annotations

import numpy as np
import pandas as pd

from .primitives import atr, swing_points


def add_price_features(events: pd.DataFrame, bars: pd.DataFrame, cfg) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = events.copy()
    a = atr(bars, cfg.atr_period)
    atr_by_ts = pd.Series(a.to_numpy(), index=bars["ts"])
    out["wick_ratio"] = out["penetration_pts"] / (out["bar_high"] - out["bar_low"]).replace(0, np.nan)
    atr_name = "atr_tf"
    out[atr_name] = out["bar_ts_utc"].map(atr_by_ts)
    out["dist_from_level_atr"] = out["penetration_pts"] / out[atr_name]
    points = swing_points(bars, 5, 5)
    sorted_points = points.sort_values("price")
    prices = sorted_points["price"].to_numpy(float)
    confirms = sorted_points["confirmed_idx"].to_numpy(int)
    tolerance = cfg.liquidity_density_ticks * cfg.tick_size
    bar_ts = bars["ts"].to_numpy(dtype="datetime64[ns]")
    event_ts = out["bar_ts_utc"].to_numpy(dtype="datetime64[ns]")
    indices = np.searchsorted(bar_ts, event_ts, side="left")
    densities = np.zeros(len(out), dtype="int64")
    for j, (price, bar_idx) in enumerate(zip(out["level_price"].to_numpy(float), indices, strict=True)):
        left = np.searchsorted(prices, price - tolerance, side="left")
        right = np.searchsorted(prices, price + tolerance, side="right")
        densities[j] = np.count_nonzero(confirms[left:right] <= bar_idx)
    out["liquidity_density"] = densities
    return out
