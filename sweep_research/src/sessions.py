"""Vendored from QLAB C:\quantlab (mktdata/qlab), context/sessions.py, adattato."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
LDN = ZoneInfo("Europe/London")


def _minutes(ts: pd.Series, zone: ZoneInfo) -> tuple[pd.Series, pd.Series]:
    local = ts.dt.tz_localize("UTC").dt.tz_convert(zone)
    return local.dt.hour * 60 + local.dt.minute, local


def _window(minutes: pd.Series, start: str, end: str) -> pd.Series:
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    a, b = h1 * 60 + m1, h2 * 60 + m2
    return (minutes >= a) & (minutes < b) if a < b else (minutes >= a) | (minutes < b)


def session_context(df: pd.DataFrame, sessions: dict[str, list[str]] | None = None,
                    killzones: dict[str, list[str]] | None = None) -> pd.DataFrame:
    sessions = sessions or {
        "asia": ["18:00", "03:00"], "london": ["03:00", "08:30"], "ny": ["08:30", "17:00"]
    }
    killzones = killzones or {"kz_asia": ["20:00", "00:00"], "kz_london": ["02:00", "05:00"],
                              "kz_ny_am": ["08:30", "11:00"], "kz_ny_pm": ["13:30", "16:00"]}
    minutes, local = _minutes(df["ts"], NY)
    out = pd.DataFrame(index=df.index)
    out["ny_hour"] = local.dt.hour.to_numpy()
    out["day_of_week"] = local.dt.dayofweek.to_numpy()
    sess = pd.Series("off", index=df.index, dtype="string")
    for name, bounds in sessions.items():
        sess = sess.mask(_window(minutes, *bounds), name)
    out["session"] = sess.to_numpy()
    kz = pd.Series("none", index=df.index, dtype="string")
    for name, bounds in killzones.items():
        kz = kz.mask(_window(minutes, *bounds), name)
    out["killzone_flag"] = kz.to_numpy()
    trading_day = local.dt.normalize() - pd.to_timedelta(
        (local.dt.hour < 18).astype("int8"), unit="D"
    )
    out["ny_date"] = trading_day.dt.tz_localize(None).to_numpy()
    out["trading_day"] = out["ny_date"]
    return out


def minutes_from_open(ts: pd.Series, value: str, zone: str) -> pd.Series:
    local = ts.dt.tz_localize("UTC").dt.tz_convert(zone)
    h, m = map(int, value.split(":"))
    return (local.dt.hour * 60 + local.dt.minute - h * 60 - m).astype(float)
