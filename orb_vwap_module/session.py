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


def session_bars(df: pd.DataFrame, s: Session, spec: SessionSpec) -> pd.DataFrame:
    """Barre del timeframe trigger appartenenti alla sessione: dall'apertura
    (inclusa: la prima barra M5 è anche parte dell'ORB) fino all'ultima barra
    che CHIUDE entro il cutoff."""
    last_open = s.cutoff_utc - spec.trigger_td
    return df[(df.index >= s.open_utc) & (df.index <= last_open)]
