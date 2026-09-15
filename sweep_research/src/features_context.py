from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from .primitives import atr
from .profile import prior_day_profile
from .sessions import minutes_from_open, session_context


def session_vwap(m1: pd.DataFrame, cfg) -> pd.Series:
    local = m1["ts"].dt.tz_localize("UTC").dt.tz_convert(cfg.session_tz)
    hour, minute = map(int, cfg.ny_open.split(":"))
    reset = local.dt.hour * 60 + local.dt.minute >= hour * 60 + minute
    day = local.dt.normalize() - pd.to_timedelta((~reset).astype("int8"), unit="D")
    day = day.dt.tz_localize(None)
    typical = (m1["high"] + m1["low"] + m1["close"]) / 3
    volume = m1["volume"].fillna(m1.get("n_ticks", 1)).astype(float)
    return (typical * volume).groupby(day).cumsum() / volume.groupby(day).cumsum().replace(0, np.nan)


def _asof_values(values: pd.Series, source_ts: pd.Series, query_ts: pd.Series) -> np.ndarray:
    order = np.argsort(source_ts.to_numpy(dtype="datetime64[ns]"))
    source = source_ts.to_numpy(dtype="datetime64[ns]")[order]
    data = values.to_numpy(float)[order]
    indices = np.searchsorted(source, query_ts.to_numpy(dtype="datetime64[ns]"), side="right") - 1
    result = np.full(len(query_ts), np.nan)
    valid = indices >= 0
    result[valid] = data[indices[valid]]
    return result


def _last_weekday(year: int, month: int) -> tuple[int, int, int]:
    last = calendar.monthrange(year, month)[1]
    while pd.Timestamp(year, month, last).weekday() >= 5:
        last -= 1
    return year, month, last


def _overnight_features(
    m1: pd.DataFrame, events: pd.DataFrame, cfg, atr_map: pd.Series
) -> tuple[np.ndarray, np.ndarray]:
    local = m1["ts"].dt.tz_localize("UTC").dt.tz_convert(cfg.session_tz)
    hour = local.dt.hour
    calendar_date = local.dt.normalize().dt.tz_localize(None)
    trade_day = calendar_date - pd.to_timedelta((hour < 18).astype("int8"), unit="D")
    asia_mask = (hour >= 18) | (hour < 3)
    ranges_high = m1["high"].where(asia_mask).groupby(trade_day, sort=True).max()
    ranges_low = m1["low"].where(asia_mask).groupby(trade_day, sort=True).min()
    first = m1["open"].where(hour >= 18).groupby(calendar_date, sort=True).first()
    last_close = m1["close"].where(hour < 17).groupby(calendar_date, sort=True).last()
    gaps = first - last_close
    event_local = events["event_ts_utc"].dt.tz_localize("UTC").dt.tz_convert(cfg.session_tz)
    event_dates = (
        event_local.dt.normalize().dt.tz_localize(None)
        - pd.to_timedelta((event_local.dt.hour < 18).astype("int8"), unit="D")
    )
    event_atr = events["bar_ts_utc"].map(atr_map).to_numpy(float)
    asia_range = ranges_high.reindex(event_dates).to_numpy(float) - ranges_low.reindex(
        event_dates
    ).to_numpy(float)
    range_value = np.divide(
        asia_range, event_atr, out=np.full(len(events), np.nan), where=event_atr != 0
    )
    incomplete_asia = (event_local.dt.hour < 3) | (event_local.dt.hour >= 18)
    range_value[incomplete_asia.to_numpy()] = np.nan
    gap_value = gaps.reindex(event_dates).to_numpy(float)
    gap_value = np.divide(
        gap_value, event_atr, out=np.full(len(events), np.nan), where=event_atr != 0
    )
    return range_value, gap_value


def add_context_features(events: pd.DataFrame, bars: pd.DataFrame, m1: pd.DataFrame, cfg) -> pd.DataFrame:
    out = events.copy()
    if out.empty:
        return out
    atr_values = atr(bars, cfg.atr_period)
    atr_map = pd.Series(atr_values.to_numpy(), index=bars["ts"])
    ctx = session_context(bars, cfg.sessions, cfg.killzones)
    ctx.index = bars["ts"]
    out["minutes_from_london_open"] = minutes_from_open(out["event_ts_utc"], cfg.london_open, cfg.london_tz)
    out["minutes_from_ny_open"] = minutes_from_open(out["event_ts_utc"], cfg.ny_open, cfg.session_tz)
    for col in ["session", "killzone_flag", "ny_date", "day_of_week"]:
        out[col] = out["bar_ts_utc"].map(ctx[col])
    out["is_month_end"] = out["ny_date"].map(
        lambda x: bool(pd.notna(x) and x == _last_weekday(x.year, x.month))
    )
    out["news_high_impact_within_30min"] = np.nan
    avwap = session_vwap(m1, cfg)
    vwap_at_event = _asof_values(
        avwap, m1["ts"], out["event_ts_utc"] - pd.Timedelta(minutes=1)
    )
    out["vs_session_vwap"] = (
        out["bar_close"] - vwap_at_event
    ) / out["bar_ts_utc"].map(atr_map).to_numpy(float)
    profile = prior_day_profile(m1, session_context(m1, cfg.sessions, cfg.killzones)["ny_date"], cfg)
    profile.index = m1["ts"]
    for name in ["poc", "vah", "val"]:
        profile_at_event = _asof_values(
            profile[name], m1["ts"], out["event_ts_utc"] - pd.Timedelta(minutes=1)
        )
        out[f"vs_{name}"] = (
            out["bar_close"] - profile_at_event
        ) / out["bar_ts_utc"].map(atr_map)
    close = bars["close"]
    er = (close - close.shift(cfg.er_period)).abs() / close.diff().abs().rolling(cfg.er_period).sum()
    burst = (atr(bars, cfg.burst_fast_atr) / atr(bars, cfg.burst_slow_atr)) > cfg.burst_threshold
    out["efficiency_ratio"] = out["bar_ts_utc"].map(pd.Series(er.to_numpy(), index=bars["ts"]))
    out["vol_burst_flag"] = out["bar_ts_utc"].map(pd.Series(burst.to_numpy(), index=bars["ts"])).fillna(False)
    out["volatility_regime"] = np.select(
        [out["vol_burst_flag"], out["efficiency_ratio"] > 0.5], ["burst", "trend"], default="chop"
    )
    overnight, gap = _overnight_features(m1, out, cfg, atr_map)
    out["overnight_range"] = overnight
    out["asia_gap"] = gap
    return out
