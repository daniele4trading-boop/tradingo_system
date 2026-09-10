"""Macchina a stati del setup break-in / rientro / trigger VWAP, per sessione.

Stati per direzione:
  IDLE      → nessun break-in ancora
  ARMED     → chiusura oltre il livello ORB (long: close <= ORB_Low)
  CONFIRMED → rientro: chiusura di nuovo dentro (long: close > ORB_Low), in attesa di trigger
  TRIGGERED → candela con corpo interamente oltre il VWAP (long: min(open,close) > vwap)
  INVALID   → dopo il rientro una chiusura torna oltre il livello (rientro fallito):
              la direzione non si riarma più nella sessione
  EXPIRED   → cutoff (o fine finestra trigger) senza trigger
  DISARMED  → l'altra direzione ha aperto il trade (una sola operazione per sessione)

Il trigger può coincidere con la barra di rientro (ARMED → TRIGGERED sulla stessa barra).
L'invalidazione prevale: una chiusura oltre il livello non può mai essere anche un rientro.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .orb import ORB
from .session import Session

LONG, SHORT = "long", "short"


@dataclass
class SetupParams:
    volume_filter: bool = False
    vol_ma_n: int = 20
    vol_k: float = 1.2


@dataclass
class DirectionState:
    direction: str
    state: str = "IDLE"
    breakin_ts: pd.Timestamp | None = None
    reentry_ts: pd.Timestamp | None = None
    trigger_ts: pd.Timestamp | None = None
    end_ts: pd.Timestamp | None = None
    vol_checks_failed: int = 0   # barre che avevano il trigger di prezzo ma non il volume


@dataclass
class Trigger:
    direction: str
    ts: pd.Timestamp          # apertura della barra trigger (ingresso al suo close)
    breakin_ts: pd.Timestamp
    reentry_ts: pd.Timestamp
    same_bar: bool            # rientro e trigger sulla stessa barra
    entry_close: float
    vwap: float
    spread: float
    volume: float
    vol_ma: float
    vol_filter_on: bool
    vol_filter_passed: bool


@dataclass
class SessionSetupResult:
    session: Session
    orb: ORB
    dirs: dict[str, DirectionState] = field(default_factory=dict)
    trigger: Trigger | None = None


def _beyond(direction: str, close: float, orb: ORB) -> bool:
    return close <= orb.low if direction == LONG else close >= orb.high


def _body_beyond_vwap(direction: str, o: float, c: float, vwap: float) -> bool:
    if pd.isna(vwap):
        return False
    return min(o, c) > vwap if direction == LONG else max(o, c) < vwap


def _volume_ok(p: SetupParams, vol: float, vol_ma: float) -> bool:
    if not p.volume_filter:
        return True
    if pd.isna(vol_ma):
        return False
    return vol > vol_ma * p.vol_k


def run_session_setup(bars: pd.DataFrame, s: Session, orb: ORB, p: SetupParams) -> SessionSetupResult:
    """`bars`: barre del timeframe trigger della sessione (colonne open, high, low,
    close, volume, spread, vwap, vol_ma), indice = apertura barra UTC.
    Vengono valutate solo le barre che aprono a partire dalla fine dell'ORB."""
    res = SessionSetupResult(s, orb, {LONG: DirectionState(LONG), SHORT: DirectionState(SHORT)})
    work = bars[bars.index >= s.orb_end_utc]
    for ts, row in work.iterrows():
        o, c = float(row["open"]), float(row["close"])
        vwap = float(row["vwap"])
        for d in (LONG, SHORT):
            st = res.dirs[d]
            if st.state not in ("IDLE", "ARMED", "CONFIRMED"):
                continue
            if ts >= s.trigger_deadline_utc:
                if st.state != "IDLE":
                    st.state, st.end_ts = "EXPIRED", ts
                continue
            beyond = _beyond(d, c, orb)
            if st.state == "IDLE":
                if beyond:
                    st.state, st.breakin_ts = "ARMED", ts
                continue
            if st.state == "CONFIRMED" and beyond:
                st.state, st.end_ts = "INVALID", ts
                continue
            if st.state == "ARMED":
                if beyond:
                    continue
                st.state, st.reentry_ts = "CONFIRMED", ts
            # CONFIRMED (eventualmente sulla stessa barra del rientro): cerca il trigger
            if _body_beyond_vwap(d, o, c, vwap):
                vol_ok = _volume_ok(p, float(row["volume"]), float(row["vol_ma"]))
                if not vol_ok:
                    st.vol_checks_failed += 1
                    continue
                st.state, st.trigger_ts, st.end_ts = "TRIGGERED", ts, ts
                res.trigger = Trigger(
                    direction=d, ts=ts, breakin_ts=st.breakin_ts, reentry_ts=st.reentry_ts,
                    same_bar=(st.reentry_ts == ts), entry_close=c, vwap=vwap,
                    spread=float(row["spread"]), volume=float(row["volume"]),
                    vol_ma=float(row["vol_ma"]), vol_filter_on=p.volume_filter, vol_filter_passed=vol_ok)
        if res.trigger is not None:
            other = SHORT if res.trigger.direction == LONG else LONG
            if res.dirs[other].state in ("ARMED", "CONFIRMED"):
                res.dirs[other].state, res.dirs[other].end_ts = "DISARMED", ts
            elif res.dirs[other].state == "IDLE":
                res.dirs[other].state = "DISARMED"
            break
    # cutoff raggiunto senza trigger
    for st in res.dirs.values():
        if st.state in ("ARMED", "CONFIRMED"):
            st.state, st.end_ts = "EXPIRED", s.cutoff_utc
    return res
