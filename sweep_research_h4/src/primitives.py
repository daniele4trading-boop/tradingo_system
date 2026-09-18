"""Causal primitives adapted from QLAB mktdata/qlab/primitives.py."""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    previous = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - previous).abs(), (df["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def swing_flags(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    left_high = pd.Series(high).rolling(left, min_periods=left).max().shift(1).to_numpy()
    left_low = pd.Series(low).rolling(left, min_periods=left).min().shift(1).to_numpy()
    right_high = pd.Series(high[::-1]).rolling(right, min_periods=right).max().to_numpy()[::-1]
    right_low = pd.Series(low[::-1]).rolling(right, min_periods=right).min().to_numpy()[::-1]
    right_high = np.r_[right_high[1:], np.nan]
    right_low = np.r_[right_low[1:], np.nan]
    return pd.DataFrame(
        {
            "is_swing_high": (high > left_high) & (high >= right_high),
            "is_swing_low": (low < left_low) & (low <= right_low),
            "confirmed_idx": np.arange(len(df)) + right,
        },
        index=df.index,
    )


def swing_points(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    flags = swing_flags(df, left, right)
    rows = []
    for kind, flag, price_col in (("H", "is_swing_high", "high"), ("L", "is_swing_low", "low")):
        for idx in np.flatnonzero(flags[flag].to_numpy()):
            confirmed_idx = idx + right
            if confirmed_idx < len(df):
                rows.append(
                    {
                        "kind": kind,
                        "idx": int(idx),
                        "ts": df["ts"].iloc[idx],
                        "price": float(df[price_col].iloc[idx]),
                        "confirmed_idx": int(confirmed_idx),
                        "confirmed_ts": df["ts"].iloc[confirmed_idx],
                    }
                )
    return pd.DataFrame(rows, columns=["kind", "idx", "ts", "price", "confirmed_idx", "confirmed_ts"])
