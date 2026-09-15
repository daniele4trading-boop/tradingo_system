from __future__ import annotations

import numpy as np
import pandas as pd

from .sessions import session_context


def _interval_sum(ts: np.ndarray, values: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    prefix = np.r_[0.0, np.cumsum(np.nan_to_num(values, nan=0.0))]
    left = np.searchsorted(ts, starts, side="left")
    right = np.searchsorted(ts, ends, side="left")
    return prefix[right] - prefix[left]


def _hourly_ratio(bars: pd.DataFrame, column: str, cfg) -> np.ndarray:
    local = bars["ts"].dt.tz_localize("UTC").dt.tz_convert(cfg.session_tz)
    dates = local.dt.date
    hours = local.dt.hour
    daily = (
        pd.DataFrame({"date": dates, "hour": hours, "value": bars[column].to_numpy(float)})
        .groupby(["hour", "date"], sort=True)["value"]
        .median()
        .reset_index()
    )
    daily["prior"] = (
        daily.sort_values("date")
        .groupby("hour", sort=False)["value"]
        .transform(lambda s: s.shift(1).rolling(cfg.hourly_median_days, min_periods=1).median())
    )
    key = pd.MultiIndex.from_arrays([hours, dates])
    lookup = pd.Series(daily["prior"].to_numpy(), index=pd.MultiIndex.from_frame(daily[["hour", "date"]]))
    denominator = lookup.reindex(key).to_numpy(float)
    return bars[column].to_numpy(float) / denominator


def add_volume_features(events: pd.DataFrame, bars: pd.DataFrame, panel: pd.DataFrame, cfg) -> pd.DataFrame:
    out = events.copy()
    if out.empty:
        return out
    panel = panel.sort_values("ts").reset_index(drop=True).copy()
    panel["imbalance"] = panel["buy_vol"] - panel["sell_vol"]
    panel["abs_imbalance"] = panel["imbalance"].abs()
    panel["total"] = panel["buy_vol"] + panel["sell_vol"]
    p_ts = panel["ts"].to_numpy(dtype="datetime64[ns]")
    starts = out["bar_ts_utc"].to_numpy(dtype="datetime64[ns]")
    ends = out["event_ts_utc"].to_numpy(dtype="datetime64[ns]")
    p_delta = panel["delta"].to_numpy(float)
    out["delta_est"] = _interval_sum(p_ts, p_delta, starts, ends)
    p_total = panel["total"].to_numpy(float)
    p_abs = panel["abs_imbalance"].to_numpy(float)
    prefix_total = np.r_[0.0, np.cumsum(np.nan_to_num(p_total))]
    prefix_abs = np.r_[0.0, np.cumsum(np.nan_to_num(p_abs))]
    end_idx = np.searchsorted(p_ts, ends, side="left")
    left_vpin = np.maximum(0, end_idx - cfg.vpin_window_bars)
    imbalance_sum = prefix_abs[end_idx] - prefix_abs[left_vpin]
    volume_sum = prefix_total[end_idx] - prefix_total[left_vpin]
    out["vpin"] = np.divide(
        imbalance_sum, volume_sum, out=np.full(len(out), np.nan), where=volume_sum != 0
    )
    ctx_panel = session_context(panel, cfg.sessions, cfg.killzones)
    group_change = np.r_[
        True,
        (ctx_panel["ny_date"].to_numpy()[1:] != ctx_panel["ny_date"].to_numpy()[:-1])
        | (ctx_panel["session"].to_numpy()[1:] != ctx_panel["session"].to_numpy()[:-1]),
    ]
    group_starts = np.flatnonzero(group_change)
    group_ids = np.cumsum(group_change, dtype=np.int64) - 1
    cumulative_delta = np.cumsum(p_delta)
    group_bases = np.zeros(len(group_starts))
    group_bases[1:] = cumulative_delta[group_starts[1:] - 1]
    cvd = cumulative_delta - group_bases[group_ids]
    del ctx_panel, group_change, group_starts, group_ids, cumulative_delta, group_bases
    out["cvd_session"] = cvd[np.maximum(end_idx - 1, 0)]
    bar_ts = bars["ts"].to_numpy(dtype="datetime64[ns]")
    tf_minutes = int(bars.attrs.get("tf_minutes", 1))
    bar_delta = _interval_sum(
        p_ts, p_delta, bar_ts, bar_ts + np.timedelta64(tf_minutes, "m")
    )
    bar_delta_3 = pd.Series(bar_delta).rolling(3, min_periods=3).sum().to_numpy()
    bar_delta_std = pd.Series(bar_delta_3).rolling(100, min_periods=20).std().shift(1).to_numpy()
    bar_idx = np.searchsorted(bar_ts, starts, side="left")
    safe_idx = np.clip(bar_idx, 0, len(bars) - 1)
    prev_high = bars["high"].shift(1).rolling(cfg.er_period, min_periods=cfg.er_period).max().to_numpy()
    prev_low = bars["low"].shift(1).rolling(cfg.er_period, min_periods=cfg.er_period).min().to_numpy()
    movement = np.where(out["dir"].to_numpy() == "sweep_high", 1.0, -1.0)
    delta3 = bar_delta_3[np.clip(bar_idx, 0, len(bar_delta_3) - 1)]
    delta_std = bar_delta_std[np.clip(bar_idx, 0, len(bar_delta_std) - 1)]
    highs = bars["high"].to_numpy(float)[safe_idx]
    lows = bars["low"].to_numpy(float)[safe_idx]
    new_extreme = np.where(movement > 0, highs >= prev_high[safe_idx], lows <= prev_low[safe_idx])
    out["delta_divergence_flag"] = new_extreme & np.where(movement > 0, delta3 <= 0, delta3 >= 0)
    out["delta_divergence_mag"] = np.divide(
        -(delta3 * movement), delta_std,
        out=np.full(len(out), np.nan), where=np.isfinite(delta_std) & (delta_std != 0),
    )
    out["spread_at_event"] = out["bar_ts_utc"].map(bars.set_index("ts")["spread_med"])
    vol_ratio = _hourly_ratio(bars, "volume", cfg)
    spread_ratio = _hourly_ratio(bars, "spread_med", cfg)
    out["vol_vs_hourly_median"] = out["bar_ts_utc"].map(pd.Series(vol_ratio, index=bars["ts"]))
    out["spread_expansion"] = out["bar_ts_utc"].map(pd.Series(spread_ratio, index=bars["ts"]))
    return out


def session_cvd(panel: pd.DataFrame, ctx: pd.DataFrame) -> pd.Series:
    tmp = panel.copy()
    tmp["session"] = ctx["session"].to_numpy()
    tmp["day"] = ctx["ny_date"].to_numpy()
    return tmp.groupby(["day", "session"], sort=False)["delta"].cumsum()
