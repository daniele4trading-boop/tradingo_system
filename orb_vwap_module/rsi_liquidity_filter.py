"""Filtri di conferma opzionali per il falso break-out (SMC / price action).

Filtro 1 — divergenza RSI su livello liquido (`require_rsi_divergence`):
  l'estremo del range rotto (ORB high/low, H1 high/low) e' un "livello RSI estremo"
  se l'RSI del timeframe `rsi_tf` alla chiusura della barra che ha fatto l'estremo era
  > `overbought` (massimo) o < `oversold` (minimo). Allo sweep (falso break-out) il
  prezzo fa un nuovo estremo oltre il livello ma l'RSI non conferma (RSI piu' basso
  del livello per lo sweep rialzista, piu' alto per quello ribassista). Con
  `strict=False` la condizione RSI estremo sul livello non e' richiesta.

Filtro 2 — test del 50 % della zona (`require_50pct_test`):
  lo sweep deve penetrare oltre il livello rotto per almeno `min_pct` dell'ampiezza
  del range prima del rientro. Non sposta l'ingresso, che resta quello del trigger.

L'RSI usato su una barra del timeframe trigger e' quello dell'ultima barra `rsi_tf`
gia' chiusa alla sua apertura (nessun lookahead).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60, "H4": 240}


@dataclass
class ConfirmParams:
    require_rsi_divergence: bool = False
    require_50pct_test: bool = False
    rsi_tf: str = "M15"
    rsi_period: int = 14
    rsi_strict: bool = True      # il livello deve avere RSI > overbought / < oversold
    overbought: float = 70.0
    oversold: float = 30.0
    min_pct: float = 0.5         # profondita' minima dello sweep in frazione del range

    @property
    def active(self) -> bool:
        return self.require_rsi_divergence or self.require_50pct_test


class HtfHolder:
    """Contenitore per il DataFrame `rsi_tf` da agganciare a `DataFrame.attrs` (un DataFrame
    nudo in attrs rompe il confronto degli attrs fatto da pandas nelle concat)."""

    def __init__(self, df: pd.DataFrame):
        self.df = df


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI di Wilder; il valore alla barra i usa solo barre <= i."""
    delta = close.astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    ag = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    al = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = ag / al.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out[(al == 0) & (ag > 0)] = 100.0
    return out


def align_closed(htf: pd.Series, tf: str, target_index: pd.Index) -> pd.Series:
    """Valore della serie `htf` (indice = apertura barra) dell'ultima barra chiusa
    all'apertura di ogni barra di `target_index`."""
    closed = htf.copy()
    closed.index = closed.index + pd.Timedelta(minutes=TF_MINUTES[tf])
    closed = closed[~closed.index.duplicated(keep="last")].sort_index()
    return closed.reindex(target_index, method="ffill")


def level_rsi(htf: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, side: str) -> float:
    """RSI alla chiusura della barra `rsi_tf` che ha fatto l'estremo del range
    [start, end). `side`: "UP" = massimo, "DOWN" = minimo. NaN se nessuna barra."""
    win = htf[(htf.index >= start) & (htf.index < end)]
    if win.empty or "rsi" not in win.columns:
        return float("nan")
    i = win["high"].idxmax() if side == "UP" else win["low"].idxmin()
    return float(win.loc[i, "rsi"])


def divergence_ok(side: str, lvl_rsi: float, sweep_rsi: float, p: ConfirmParams) -> bool:
    """`side` = lato del falso break-out ("UP": sweep sopra il massimo, trade short).
    True se il prezzo ha fatto un nuovo estremo (implicito nello sweep) mentre l'RSI
    e' rimasto sotto (UP) / sopra (DOWN) quello del livello."""
    if pd.isna(lvl_rsi) or pd.isna(sweep_rsi):
        return False
    if side == "UP":
        if p.rsi_strict and lvl_rsi <= p.overbought:
            return False
        return sweep_rsi < lvl_rsi
    if p.rsi_strict and lvl_rsi >= p.oversold:
        return False
    return sweep_rsi > lvl_rsi


def depth_ok(side: str, level: float, sweep_extreme: float, width: float, p: ConfirmParams) -> bool:
    """Profondita' dello sweep oltre il livello rotto >= min_pct * ampiezza del range."""
    if width <= 0 or pd.isna(sweep_extreme):
        return False
    depth = (sweep_extreme - level) if side == "UP" else (level - sweep_extreme)
    return depth >= p.min_pct * width
