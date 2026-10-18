from __future__ import annotations

import numpy as np
import pandas as pd


def label_target_before_stop(
    bars: pd.DataFrame,
    point: float,
    target_points: int = 300,
    stop_points: int = 150,
    horizon_bars: int = 60,
) -> pd.DataFrame:
    """Label whether +target is reached before stop for long and short entries.

    Long entry uses ask_close when available and exits against bid extremes.
    Short entry uses bid_close when available and exits against ask extremes.
    If bid/ask columns are unavailable, mid OHLC is used as a fallback.
    """
    if bars.empty:
        return bars

    result = bars.copy().reset_index(drop=True)
    target_distance = target_points * point
    stop_distance = stop_points * point

    long_entry = result["ask_close"] if "ask_close" in result.columns else result["close"]
    long_high = result["bid_high"] if "bid_high" in result.columns else result["high"]
    long_low = result["bid_low"] if "bid_low" in result.columns else result["low"]

    short_entry = result["bid_close"] if "bid_close" in result.columns else result["close"]
    short_high = result["ask_high"] if "ask_high" in result.columns else result["high"]
    short_low = result["ask_low"] if "ask_low" in result.columns else result["low"]

    long_outcome, long_wait = _scan_direction(
        entries=long_entry.to_numpy(float),
        highs=long_high.to_numpy(float),
        lows=long_low.to_numpy(float),
        target_distance=target_distance,
        stop_distance=stop_distance,
        horizon_bars=horizon_bars,
        direction=1,
    )
    short_outcome, short_wait = _scan_direction(
        entries=short_entry.to_numpy(float),
        highs=short_high.to_numpy(float),
        lows=short_low.to_numpy(float),
        target_distance=target_distance,
        stop_distance=stop_distance,
        horizon_bars=horizon_bars,
        direction=-1,
    )

    result["long_outcome"] = long_outcome
    result["long_bars_to_exit"] = long_wait
    result["short_outcome"] = short_outcome
    result["short_bars_to_exit"] = short_wait
    result["target_points"] = target_points
    result["stop_points"] = stop_points
    result["horizon_bars"] = horizon_bars
    return result


def _scan_direction(
    entries: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    target_distance: float,
    stop_distance: float,
    horizon_bars: int,
    direction: int,
) -> tuple[np.ndarray, np.ndarray]:
    n = len(entries)
    outcomes = np.zeros(n, dtype=int)
    waits = np.full(n, np.nan)

    for idx in range(n):
        entry = entries[idx]
        if np.isnan(entry):
            continue
        target = entry + target_distance if direction == 1 else entry - target_distance
        stop = entry - stop_distance if direction == 1 else entry + stop_distance
        last = min(n, idx + horizon_bars + 1)

        for future_idx in range(idx + 1, last):
            high = highs[future_idx]
            low = lows[future_idx]
            if np.isnan(high) or np.isnan(low):
                continue

            if direction == 1:
                hit_target = high >= target
                hit_stop = low <= stop
            else:
                hit_target = low <= target
                hit_stop = high >= stop

            # With bar data the intra-bar order is unknown. Use conservative
            # stop-first handling for ambiguous bars.
            if hit_stop:
                outcomes[idx] = -1
                waits[idx] = future_idx - idx
                break
            if hit_target:
                outcomes[idx] = 1
                waits[idx] = future_idx - idx
                break

    return outcomes, waits
