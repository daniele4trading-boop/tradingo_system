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
    # La barra immediatamente successiva al break-in deve raggiungere il VWAP (livello noto
    # alla sua apertura = VWAP di chiusura della barra di break-in): ingresso intrabar al
    # tocco del livello. Se non accade il tentativo fallisce e la direzione torna IDLE.
    immediate_trigger: bool = False
    # Lateralità: le N barre che precedono il break-in devono aver chiuso dentro l'ORB.
    min_range_bars: int = 0
    # "inside": le N barre chiudono tutte dentro l'ORB; "not_beyond": nessuna delle N
    # barre ha chiuso oltre il livello che viene rotto (lateralità sopra/sotto il livello).
    range_mode: str = "inside"
    # "orb": break-in/rientro/trigger sull'ORB. "vwap_cross": nessun break-in, ingresso al
    # close della barra M5 che attraversa il VWAP di sessione (close oltre, close precedente
    # dal lato opposto) con volume > media×K; SL = min/max delle ultime `sl_lookback` barre.
    mode: str = "orb"
    sl_lookback: int = 6


@dataclass
class DirectionState:
    direction: str
    state: str = "IDLE"
    breakin_ts: pd.Timestamp | None = None
    reentry_ts: pd.Timestamp | None = None
    trigger_ts: pd.Timestamp | None = None
    end_ts: pd.Timestamp | None = None
    vol_checks_failed: int = 0   # barre che avevano il trigger di prezzo ma non il volume
    failed_attempts: int = 0     # break-in senza rientro+trigger immediato (solo immediate_trigger)
    range_rejects: int = 0       # break-in scartati per lateralità insufficiente


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
    intrabar: bool = False    # ingresso al tocco del VWAP dentro la barra (non al close)
    bar_high: float = float("nan")
    bar_low: float = float("nan")
    bar_close: float = float("nan")
    sl_level: float = float("nan")  # SL proprio del trigger (mode vwap_cross); NaN = ORB


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


def _inside(close: float, orb: ORB) -> bool:
    return orb.low < close < orb.high


def _lateral(direction: str, close: float, orb: ORB, p: SetupParams) -> bool:
    if p.range_mode == "not_beyond":
        return not _beyond(direction, close, orb)
    return _inside(close, orb)


def _volume_ok(p: SetupParams, vol: float, vol_ma: float) -> bool:
    if not p.volume_filter:
        return True
    if pd.isna(vol_ma):
        return False
    return vol > vol_ma * p.vol_k


def run_vwap_cross(bars: pd.DataFrame, s: Session, orb: ORB, p: SetupParams) -> SessionSetupResult:
    """Modalita' vwap_cross: la prima barra (dopo la fine dell'ORB, che qui vale solo come
    warm-up del VWAP) che chiude oltre il VWAP venendo dal lato opposto, con volume ok,
    apre nella direzione dell'attraversamento. I tentativi con volume insufficiente sono
    contati in vol_checks_failed; ogni attraversamento e' un break-in "armato"."""
    res = SessionSetupResult(s, orb, {LONG: DirectionState(LONG), SHORT: DirectionState(SHORT)})
    first = max(1, int((bars.index < s.orb_end_utc).sum()))
    n = p.sl_lookback
    for i in range(first, len(bars)):
        ts, row = bars.index[i], bars.iloc[i]
        if ts >= s.trigger_deadline_utc:
            break
        c, vwap = float(row["close"]), float(row["vwap"])
        pc, pv = float(bars.iloc[i - 1]["close"]), float(bars.iloc[i - 1]["vwap"])
        if pd.isna(vwap) or pd.isna(pv):
            continue
        if pc <= pv and c > vwap:
            d = LONG
        elif pc >= pv and c < vwap:
            d = SHORT
        else:
            continue
        st = res.dirs[d]
        vol_ok = _volume_ok(p, float(row["volume"]), float(row["vol_ma"]))
        if not vol_ok:
            st.vol_checks_failed += 1
            st.failed_attempts += 1
            continue
        win = bars.iloc[max(0, i - n + 1):i + 1]
        sl = float(win["low"].min()) if d == LONG else float(win["high"].max())
        st.state, st.breakin_ts, st.reentry_ts, st.trigger_ts, st.end_ts = "TRIGGERED", ts, ts, ts, ts
        res.trigger = Trigger(
            direction=d, ts=ts, breakin_ts=ts, reentry_ts=ts, same_bar=True, entry_close=c,
            vwap=vwap, spread=float(row["spread"]), volume=float(row["volume"]),
            vol_ma=float(row["vol_ma"]), vol_filter_on=p.volume_filter, vol_filter_passed=vol_ok,
            sl_level=sl)
        other = SHORT if d == LONG else LONG
        res.dirs[other].state = "DISARMED"
        break
    return res


def run_session_setup(bars: pd.DataFrame, s: Session, orb: ORB, p: SetupParams) -> SessionSetupResult:
    if p.mode == "vwap_cross":
        return run_vwap_cross(bars, s, orb, p)
    """`bars`: barre del timeframe trigger della sessione (colonne open, high, low,
    close, volume, spread, vwap, vol_ma), indice = apertura barra UTC.
    Vengono valutate solo le barre che aprono a partire dalla fine dell'ORB."""
    res = SessionSetupResult(s, orb, {LONG: DirectionState(LONG), SHORT: DirectionState(SHORT)})
    closes = bars["close"].astype(float).tolist()
    first = int((bars.index < s.orb_end_utc).sum())
    for i in range(first, len(bars)):
        ts, row = bars.index[i], bars.iloc[i]
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
                    n = p.min_range_bars
                    if n > 0 and (i < n or not all(_lateral(d, x, orb, p) for x in closes[i - n:i])):
                        st.range_rejects += 1
                        continue
                    st.state, st.breakin_ts = "ARMED", ts
                continue
            if st.state == "CONFIRMED" and beyond:
                st.state, st.end_ts = "INVALID", ts
                continue
            if st.state == "ARMED":
                if p.immediate_trigger:
                    level = float(bars.iloc[i - 1]["vwap"])
                    hi, lo = float(row["high"]), float(row["low"])
                    sgn = 1 if d == LONG else -1
                    # il VWAP deve stare dentro l'ORB (oltre il livello rotto): toccarlo
                    # significa essere rientrati, e la distanza entry-SL è positiva
                    bound = orb.low if d == LONG else orb.high
                    reachable = (not pd.isna(level)) and sgn * (level - bound) > 0
                    touched = (hi >= level) if d == LONG else (lo <= level)
                    vol_ok = _volume_ok(p, float(row["volume"]), float(row["vol_ma"]))
                    if not (reachable and touched):
                        st.failed_attempts += 1
                        st.state, st.breakin_ts = "IDLE", None
                        continue
                    if not vol_ok:
                        st.vol_checks_failed += 1
                        st.failed_attempts += 1
                        st.state, st.breakin_ts = "IDLE", None
                        continue
                    entry = max(o, level) if d == LONG else min(o, level)
                    st.state, st.reentry_ts, st.trigger_ts, st.end_ts = "TRIGGERED", ts, ts, ts
                    res.trigger = Trigger(
                        direction=d, ts=ts, breakin_ts=st.breakin_ts, reentry_ts=ts, same_bar=True,
                        entry_close=entry, vwap=level, spread=float(row["spread"]),
                        volume=float(row["volume"]), vol_ma=float(row["vol_ma"]),
                        vol_filter_on=p.volume_filter, vol_filter_passed=vol_ok,
                        intrabar=True, bar_high=hi, bar_low=lo, bar_close=c)
                    break
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
