from __future__ import annotations

import numpy as np
import pandas as pd


def _returns(frame: pd.DataFrame, timestamps: np.ndarray, lookback_min: int) -> np.ndarray:
    if frame.empty:
        return np.full(len(timestamps), np.nan)
    frame = frame.sort_values("ts")
    ts = frame["ts"].to_numpy(dtype="datetime64[ns]")
    close = frame["close"].to_numpy(float)
    end = np.searchsorted(ts + np.timedelta64(1, "m"), timestamps, side="right") - 1
    old = np.searchsorted(ts, timestamps - np.timedelta64(lookback_min, "m"), side="right") - 1
    valid = (end >= 0) & (old >= 0) & np.isfinite(close[np.clip(end, 0, len(close) - 1)])
    result = np.full(len(timestamps), np.nan)
    result[valid] = (close[end[valid]] / close[old[valid]] - 1.0) * 1e4
    return result


def add_external_features(
    events: pd.DataFrame, m1: pd.DataFrame, external: dict[str, pd.DataFrame], cfg
) -> tuple[pd.DataFrame, dict[str, bool]]:
    out = events.copy()
    symbols = [cfg.external.dxy, cfg.external.ust_proxy, cfg.external.silver]
    flags = {symbol: symbol not in external or external[symbol].empty for symbol in symbols}
    timestamps = out["event_ts_utc"].to_numpy(dtype="datetime64[ns]")
    xau = _returns(m1, timestamps, cfg.external.lookback_min)
    dxy = _returns(external.get(cfg.external.dxy, pd.DataFrame()), timestamps, cfg.external.lookback_min)
    ust = _returns(
        external.get(cfg.external.ust_proxy, pd.DataFrame()), timestamps, cfg.external.lookback_min
    )
    xag = _returns(external.get(cfg.external.silver, pd.DataFrame()), timestamps, cfg.external.lookback_min)
    out["dxy_intraday_trend"] = dxy
    out["ust_proxy_change"] = ust
    out["xagusd_divergence"] = xau - xag
    return out, flags
