from __future__ import annotations

import numpy as np
import pandas as pd


def add_outcomes(events: pd.DataFrame, m1: pd.DataFrame, cfg) -> pd.DataFrame:
    out = events.copy()
    if out.empty:
        return out
    m1 = m1.sort_values("ts")
    ts = m1["ts"].to_numpy(dtype="datetime64[ns]")
    close = m1["close"].to_numpy(float)
    high = m1["high"].to_numpy(float)
    low = m1["low"].to_numpy(float)
    event_ts = out["event_ts_utc"].to_numpy(dtype="datetime64[ns]")
    entry = out["bar_close"].to_numpy(float)
    sign = out["sign"].to_numpy(float)
    for horizon in cfg.outcome_horizons_min:
        target = event_ts + np.timedelta64(horizon, "m")
        indices = np.searchsorted(ts, target, side="left")
        valid = indices < len(ts)
        raw = np.full(len(out), np.nan)
        raw[valid] = (close[indices[valid]] / entry[valid] - 1.0) * 1e4
        out[f"fwd_ret_{horizon}_bp"] = raw
        out[f"fwd_ret_{horizon}_dir_bp"] = raw * sign
    horizon = cfg.mfe_mae_horizon_min
    starts = np.searchsorted(ts, event_ts, side="right")
    ends = np.searchsorted(ts, event_ts + np.timedelta64(horizon, "m"), side="right")
    mfe = np.full(len(out), np.nan)
    mae = np.full(len(out), np.nan)
    for i, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if start >= end:
            continue
        future_high = high[start:end]
        future_low = low[start:end]
        mfe[i] = np.max(future_high - entry[i]) if sign[i] > 0 else np.max(entry[i] - future_low)
        mae[i] = np.max(entry[i] - future_low) if sign[i] > 0 else np.max(future_high - entry[i])
    out[f"mfe_{horizon}_pts"] = mfe
    out[f"mae_{horizon}_pts"] = mae
    atr = out["atr_tf"].to_numpy(float)
    out[f"mfe_{horizon}_atr"] = np.divide(mfe, atr, out=np.full(len(out), np.nan), where=atr != 0)
    out[f"mae_{horizon}_atr"] = np.divide(mae, atr, out=np.full(len(out), np.nan), where=atr != 0)
    return out
