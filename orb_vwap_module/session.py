"""Sessioni di trading: apertura/cutoff in fuso locale (DST corretto) → UTC.

Le barre hanno indice `ts` = UTC naive = ora di APERTURA della barra.
Tutti i parametri (fuso, orario apertura, cutoff, timeframe ORB e trigger,
finestra di validità del trigger) sono espliciti per consentire estensioni
(H1/H4 come ORB, indici USA, BTC, ...).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


@dataclass(frozen=True)
class SessionSpec:
    tz: str = "America/New_York"
    open_time: time = time(9, 30)
    cutoff_time: time = time(22, 0)
    orb_tf: str = "15min"        # durata della finestra ORB
    trigger_tf: str = "5min"     # timeframe di setup/trigger
    trigger_window: str | None = None  # es. "30min": trigger valido solo entro N min dal termine ORB; None = nessun limite
    # None = un solo ORB all'apertura di sessione. "1h"/"4h" = ORB "rolling": la sessione
    # e' divisa in finestre consecutive di 1h/4h a partire dall'apertura (9:30, 10:30, ...),
    # l'ORB sono i primi `orb_tf` (es. 30min / 2h) e il setup vale fino alla fine della finestra.
    # VWAP, cutoff e chiusura forzata restano quelli della sessione giornaliera.
    rolling_tf: str | None = None

    @property
    def orb_td(self) -> timedelta:
        return pd.Timedelta(self.orb_tf).to_pytimedelta()

    @property
    def trigger_td(self) -> timedelta:
        return pd.Timedelta(self.trigger_tf).to_pytimedelta()

    @property
    def trigger_window_td(self) -> timedelta | None:
        return None if self.trigger_window is None else pd.Timedelta(self.trigger_window).to_pytimedelta()

    def local_to_utc(self, day: date, t: time) -> datetime:
        loc = datetime.combine(day, t, tzinfo=ZoneInfo(self.tz))
        return loc.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

    def open_utc(self, day: date) -> datetime:
        return self.local_to_utc(day, self.open_time)

    def cutoff_utc(self, day: date) -> datetime:
        return self.local_to_utc(day, self.cutoff_time)

    def orb_end_utc(self, day: date) -> datetime:
        return self.open_utc(day) + self.orb_td


@dataclass(frozen=True)
class Session:
    day: date            # data locale della sessione (giorno di trading)
    open_utc: datetime
    orb_end_utc: datetime
    cutoff_utc: datetime
    trigger_deadline_utc: datetime  # oltre questo istante (apertura barra) nessun trigger
    day_cutoff_utc: datetime | None = None  # sessione giornaliera (chiusura forzata); None = cutoff_utc

    @property
    def sim_end_utc(self) -> datetime:
        return self.day_cutoff_utc or self.cutoff_utc


def local_days(index: pd.DatetimeIndex, spec: SessionSpec) -> list[date]:
    """Giorni locali (weekday) coperti dall'indice UTC."""
    loc = index.tz_localize("UTC").tz_convert(spec.tz)
    days = sorted({d for d in loc.date})
    return [d for d in days if d.weekday() < 5]


def build_sessions(index: pd.DatetimeIndex, spec: SessionSpec) -> list[Session]:
    out: list[Session] = []
    for d in local_days(index, spec):
        o = spec.open_utc(d)
        e = spec.orb_end_utc(d)
        c = spec.cutoff_utc(d)
        w = spec.trigger_window_td
        deadline = c if w is None else min(c, e + w)
        out.append(Session(d, o, e, c, deadline))
    return out


def build_windows(day_sessions: list[Session], spec: SessionSpec) -> list[Session]:
    """Finestre ORB rolling dentro ogni sessione giornaliera (vedi SessionSpec.rolling_tf).
    Una finestra e' inclusa se il suo ORB termina prima del cutoff."""
    if spec.rolling_tf is None:
        return day_sessions
    step = pd.Timedelta(spec.rolling_tf)
    out: list[Session] = []
    for s in day_sessions:
        w0 = pd.Timestamp(s.open_utc)
        while w0 < s.cutoff_utc:
            w_start, w_end = w0.to_pydatetime(), (w0 + step).to_pydatetime()
            orb_end = w_start + spec.orb_td
            if orb_end < s.cutoff_utc:
                end = min(w_end, s.cutoff_utc)
                out.append(Session(s.day, w_start, orb_end, end, end, s.cutoff_utc))
            w0 += step
    return out


def session_bars(df: pd.DataFrame, s: Session, spec: SessionSpec) -> pd.DataFrame:
    """Barre del timeframe trigger appartenenti alla sessione: dall'apertura
    (inclusa: la prima barra M5 è anche parte dell'ORB) fino all'ultima barra
    che CHIUDE entro il cutoff."""
    last_open = s.sim_end_utc - spec.trigger_td
    return df[(df.index >= s.open_utc) & (df.index <= last_open)]
