"""Vendored from QLAB C:\\quantlab (mktdata/qlab), context/profile.py, adattato."""
from __future__ import annotations

import numpy as np
import pandas as pd


def profile_levels(high: np.ndarray, low: np.ndarray, vol: np.ndarray, bin_ticks: int = 10,
                   tick_size: float = 0.01, value_area: float = 0.7) -> dict[str, float]:
    lo, hi = float(np.nanmin(low)), float(np.nanmax(high))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return {"poc": np.nan, "vah": np.nan, "val": np.nan}
    step = max(bin_ticks * tick_size, np.finfo(float).eps)
    edges = np.arange(np.floor(lo / step) * step, np.ceil(hi / step) * step + step, step)
    if len(edges) < 2:
        return {"poc": np.nan, "vah": np.nan, "val": np.nan}
    hist = np.zeros(len(edges) - 1)
    for high_value, low_value, volume in zip(high, low, vol, strict=True):
        a = int(np.clip(np.searchsorted(edges, low_value, side="right") - 1, 0, len(hist) - 1))
        b = int(np.clip(np.searchsorted(edges, high_value, side="right") - 1, 0, len(hist) - 1))
        hist[a:b + 1] += float(volume) / (b - a + 1)
    if hist.sum() <= 0:
        return {"poc": np.nan, "vah": np.nan, "val": np.nan}
    centers = (edges[:-1] + edges[1:]) / 2
    poc = int(np.argmax(hist))
    target = hist.sum() * value_area
    left = right = poc
    total = hist[poc]
    while total < target and (left or right < len(hist) - 1):
        down = hist[left - 1] if left else -1
        up = hist[right + 1] if right < len(hist) - 1 else -1
        if up >= down:
            right += 1
            total += hist[right]
        else:
            left -= 1
            total += hist[left]
    return {"poc": float(centers[poc]), "vah": float(centers[right]), "val": float(centers[left])}


def prior_day_profile(df: pd.DataFrame, trade_day: pd.Series | np.ndarray, cfg) -> pd.DataFrame:
    day = pd.Series(trade_day, index=df.index)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    volume = df["volume"].fillna(df.get("n_ticks", 1)).to_numpy()
    levels: dict[object, dict[str, float]] = {}
    for key, positions in day.groupby(day, sort=False).indices.items():
        positions = np.asarray(positions)
        levels[key] = profile_levels(
            high[positions], low[positions], volume[positions],
            cfg.profile_bin_ticks, cfg.tick_size, cfg.profile_value_area
        )
    keys = list(levels)
    previous = {keys[i]: levels[keys[i - 1]] for i in range(1, len(keys))}
    return pd.DataFrame({
        "poc": [previous.get(k, {}).get("poc", np.nan) for k in day],
        "vah": [previous.get(k, {}).get("vah", np.nan) for k in day],
        "val": [previous.get(k, {}).get("val", np.nan) for k in day],
    }, index=df.index)
