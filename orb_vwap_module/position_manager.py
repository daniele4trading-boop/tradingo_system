"""Gestione della posizione sulle barre del timeframe trigger dopo l'ingresso.

Variante A: un solo ticket, rischio 1%, TP fisso RR 1:2, nessun trailing.
Variante B: due gambe da 0.5% con lo stesso SL iniziale; gamba 1 TP a RR 1:2;
            al TP1 la gamba 2 passa a BE e parte un trailing k×ATR mai arretrato.

Convenzioni:
- ingresso al close della barra trigger; i prezzi sono bid MT5; il costo dello
  spread (spread_med della barra) viene applicato sia in ingresso sia in uscita;
- su una stessa barra che tocca sia stop sia TP prevale lo stop (conservativo);
- se la barra apre oltre un livello (gap) il fill è al prezzo di apertura;
- posizione ancora aperta all'ultima barra della sessione → chiusa al suo close.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor

import pandas as pd

from .setup_engine import LONG


@dataclass
class RiskParams:
    risk_pct: float = 1.0            # % del capitale per trade (totale)
    rr_tp1: float = 2.0
    sl_buffer: float = 0.0           # in prezzo (0 = disattivo)
    contract_size: float = 100.0     # once per lotto
    lot_step: float = 0.01
    min_lot: float = 0.01
    compound: bool = True            # rischio sul capitale corrente (True) o iniziale (False)
    min_risk_dist: float = 0.0       # in prezzo: sotto questa distanza entry-SL il trigger non apre (0 = off)


@dataclass
class TrailParams:
    atr_period: int = 14
    atr_mult: float = 2.0


@dataclass
class LegResult:
    lots: float
    exit_ts: pd.Timestamp
    exit_price: float
    reason: str          # TP1 | SL | BE | TRAIL | CUTOFF
    pnl: float


@dataclass
class TradeResult:
    variant: str
    direction: str
    entry_ts: pd.Timestamp
    entry_price: float
    sl: float
    tp1: float
    risk_dist: float
    risk_money: float
    legs: list[LegResult]

    @property
    def pnl(self) -> float:
        return sum(l.pnl for l in self.legs)

    @property
    def rr(self) -> float:
        return self.pnl / self.risk_money if self.risk_money else 0.0

    @property
    def exit_ts(self) -> pd.Timestamp:
        return max(l.exit_ts for l in self.legs)

    @property
    def outcome(self) -> str:
        be_tol = 0.05 * self.risk_money
        if self.pnl > be_tol:
            return "win"
        if self.pnl < -be_tol:
            return "loss"
        return "be"


def size_lots(risk_money: float, risk_dist: float, rp: RiskParams) -> float:
    per_lot = risk_dist * rp.contract_size
    if per_lot <= 0:
        return 0.0
    lots = floor(risk_money / per_lot / rp.lot_step + 1e-9) * rp.lot_step
    return max(round(lots, 2), rp.min_lot)


def _sign(direction: str) -> int:
    return 1 if direction == LONG else -1


def _pnl(direction: str, entry: float, exit_: float, lots: float, rp: RiskParams) -> float:
    return _sign(direction) * (exit_ - entry) * lots * rp.contract_size


def _fill(direction: str, level: float, bar: pd.Series, spread: float, side: str) -> float:
    """Prezzo di uscita su un livello toccato dalla barra, spread applicato contro il
    trade. Uno stop saltato dal gap in apertura viene eseguito all'open (peggiore);
    un TP viene sempre eseguito al livello (conservativo, nessun beneficio dal gap)."""
    o = float(bar["open"])
    sgn = _sign(direction)
    px = level
    if side == "stop" and sgn * (o - level) < 0:
        px = o
    return px - sgn * spread


def _touched(direction: str, level: float, bar: pd.Series, side: str) -> bool:
    """side='stop': livello contro il trade; side='tp': livello a favore."""
    lo, hi = float(bar["low"]), float(bar["high"])
    if direction == LONG:
        return lo <= level if side == "stop" else hi >= level
    return hi >= level if side == "stop" else lo <= level


def entry_price(direction: str, close: float, spread: float) -> float:
    """Spread contro il trade in ingresso (e di nuovo in uscita in `_fill`):
    costo round-trip = 2×spread_med, scelta conservativa richiesta."""
    return close + _sign(direction) * spread


def simulate(direction: str, entry_ts: pd.Timestamp, entry_close: float, entry_spread: float,
             orb_low: float, orb_high: float, bars_after: pd.DataFrame, capital: float,
             variant: str, rp: RiskParams, tp: TrailParams | None = None,
             tp_level: float | None = None) -> TradeResult:
    """`bars_after`: barre della sessione successive alla barra trigger, con
    colonne open/high/low/close/spread/atr. `tp_level`: TP a livello fisso
    (es. estremo opposto dell'ORB) al posto di RR×rischio."""
    sgn = _sign(direction)
    entry = entry_price(direction, entry_close, entry_spread)
    sl = (orb_low - rp.sl_buffer) if direction == LONG else (orb_high + rp.sl_buffer)
    risk_dist = sgn * (entry - sl)
    risk_money = capital * rp.risk_pct / 100.0
    tp1 = tp_level if tp_level is not None else entry + sgn * rp.rr_tp1 * risk_dist

    if variant == "A":
        lots = size_lots(risk_money, risk_dist, rp)
        legs = [_run_leg(direction, entry, sl, tp1, lots, bars_after, rp, None, entry_ts)]
    else:
        lots = size_lots(risk_money / 2.0, risk_dist, rp)
        leg1 = _run_leg(direction, entry, sl, tp1, lots, bars_after, rp, None, entry_ts)
        tp1_ts = leg1.exit_ts if leg1.reason == "TP1" else None
        legs = [leg1, _run_leg(direction, entry, sl, None, lots, bars_after, rp,
                               (tp1_ts, tp or TrailParams()), entry_ts)]
    return TradeResult(variant, direction, entry_ts, entry, sl, tp1, risk_dist, risk_money, legs)


def _run_leg(direction: str, entry: float, sl: float, tp1: float | None, lots: float,
             bars: pd.DataFrame, rp: RiskParams, trail: tuple | None,
             entry_ts: pd.Timestamp) -> LegResult:
    """Gamba singola. `trail=(tp1_ts, TrailParams)`: gamba 2 della variante B —
    a partire dalla barra in cui la gamba 1 ha preso TP1 lo stop passa a BE e poi
    segue max(BE, estremo favorevole − k·ATR) aggiornato a ogni chiusura di barra."""
    sgn = _sign(direction)
    stop = sl
    trailing = False
    extreme = None
    tp1_ts, tparams = (trail if trail else (None, None))
    last = None
    for ts, bar in bars.iterrows():
        last = (ts, bar)
        spread = float(bar["spread"])
        if _touched(direction, stop, bar, "stop"):
            if not trailing:
                reason = "SL"
            else:
                reason = "BE" if abs(stop - entry) < 1e-9 else "TRAIL"
            px = _fill(direction, stop, bar, spread, "stop")
            return LegResult(lots, ts, px, reason, _pnl(direction, entry, px, lots, rp))
        if tp1 is not None and _touched(direction, tp1, bar, "tp"):
            px = _fill(direction, tp1, bar, spread, "tp")
            return LegResult(lots, ts, px, "TP1", _pnl(direction, entry, px, lots, rp))
        if trail and tp1_ts is not None and ts >= tp1_ts:
            if not trailing:
                trailing = True
                extreme = float(bar["high"]) if direction == LONG else float(bar["low"])
                stop = entry
            else:
                extreme = max(extreme, float(bar["high"])) if direction == LONG else min(extreme, float(bar["low"]))
            a = float(bar["atr"])
            if not pd.isna(a):
                cand = extreme - sgn * tparams.atr_mult * a
                stop = max(stop, cand) if direction == LONG else min(stop, cand)
    if last is None:
        # nessuna barra dopo il trigger: chiusura immediata al prezzo d'ingresso teorico
        return LegResult(lots, entry_ts, entry, "CUTOFF", 0.0)
    ts, bar = last
    px = float(bar["close"]) - sgn * float(bar["spread"])
    return LegResult(lots, ts, px, "CUTOFF", _pnl(direction, entry, px, lots, rp))
