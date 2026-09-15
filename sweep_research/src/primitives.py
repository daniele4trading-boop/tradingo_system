"""Vendored from QLAB C:\\quantlab (mktdata/qlab), primitives.py, adattato."""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def swing_flags(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    h, low = df["high"].to_numpy(float), df["low"].to_numpy(float)
    left_h = pd.Series(h).rolling(left, min_periods=left).max().shift(1).to_numpy()
    left_l = pd.Series(low).rolling(left, min_periods=left).min().shift(1).to_numpy()
    right_h = pd.Series(h[::-1]).rolling(right, min_periods=right).max().to_numpy()[::-1]
    right_l = pd.Series(low[::-1]).rolling(right, min_periods=right).min().to_numpy()[::-1]
    right_h = np.r_[right_h[1:], np.nan]
    right_l = np.r_[right_l[1:], np.nan]
    return pd.DataFrame({
        "is_swing_high": (h > left_h) & (h >= right_h),
        "is_swing_low": (low < left_l) & (low <= right_l),
        "confirmed_idx": np.arange(len(df)) + right,
    }, index=df.index)


def swing_points(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    flags = swing_flags(df, left, right)
    rows = []
    for kind, flag, col in [("H", "is_swing_high", "high"), ("L", "is_swing_low", "low")]:
        for i in np.flatnonzero(flags[flag].to_numpy()):
            ci = i + right
            if ci < len(df):
                rows.append({
                    "kind": kind, "idx": int(i), "ts": df["ts"].iloc[i],
                    "price": float(df[col].iloc[i]), "confirmed_idx": int(ci),
                    "confirmed_ts": df["ts"].iloc[ci],
                })
    return pd.DataFrame(rows, columns=["kind", "idx", "ts", "price", "confirmed_idx", "confirmed_ts"])
