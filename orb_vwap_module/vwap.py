"""VWAP di sessione ricalcolato da zero (cumulata di typical price × volume)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .session import Session, SessionSpec


def typical_price(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def session_vwap(df: pd.DataFrame, sessions: list[Session], spec: SessionSpec) -> pd.Series:
    """VWAP cumulato dalla prima barra della sessione (apertura) fino alla barra
    corrente, reset a ogni apertura. Le barre fuori sessione restano NaN.
    La colonna `vwap` intra-barra del CSV NON viene usata."""
    tp = typical_price(df)
    vol = df["volume"].astype(float)
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for s in sessions:
        m = (df.index >= s.open_utc) & (df.index < s.cutoff_utc)
        if not m.any():
            continue
        pv = (tp[m] * vol[m]).cumsum()
        v = vol[m].cumsum()
        out[m] = (pv / v.replace(0.0, np.nan)).values
    return out


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR (Wilder) sulle barre del DataFrame; il valore alla barra i usa solo
    barre <= i (nessun lookahead)."""
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def volume_ma(df: pd.DataFrame, n: int) -> pd.Series:
    """Media del volume delle N barre PRECEDENTI (esclusa la corrente)."""
    return df["volume"].astype(float).shift(1).rolling(n, min_periods=n).mean()
