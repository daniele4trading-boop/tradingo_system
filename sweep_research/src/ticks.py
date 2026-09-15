from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .data import tick_days


def lee_ready(ticks: pd.DataFrame) -> pd.DataFrame:
    out = ticks.sort_values("ts", ignore_index=True).copy()
    mid = (out["bid"] + out["ask"]) / 2
    move = mid.diff()
    sign = np.sign(move).replace(0, np.nan).ffill().fillna(1.0)
    out["mid"] = mid
    out["sign"] = sign.astype("int8")
    out["volume_tick"] = out["bid_vol"].fillna(0) + out["ask_vol"].fillna(0)
    out["delta_tick"] = out["sign"] * out["volume_tick"]
    out["spread"] = out["ask"] - out["bid"]
    return out


def aggregate_m1(ticks: pd.DataFrame) -> pd.DataFrame:
    if ticks.empty:
        return pd.DataFrame(columns=["ts", "buy_vol", "sell_vol", "delta", "n_ticks", "spread_med"])
    q = lee_ready(ticks)
    q["ts"] = q["ts"].dt.floor("min")
    q["buy"] = q["volume_tick"].where(q["sign"] > 0, 0.0)
    q["sell"] = q["volume_tick"].where(q["sign"] < 0, 0.0)
    out = q.groupby("ts", sort=True).agg(
        buy_vol=("buy", "sum"), sell_vol=("sell", "sum"), delta=("delta_tick", "sum"),
        n_ticks=("mid", "size"), spread_med=("spread", "median"), mid=("mid", "last"),
    ).reset_index()
    return out


def build_tick_panel(
    cfg: Config, start=None, end=None, output: str | Path | None = None
) -> tuple[pd.DataFrame, set[str]]:
    frames = []
    present: set[str] = set()
    for day, ticks in tick_days(cfg, cfg.symbol, start, end):
        present.add(str(day))
        frames.append(aggregate_m1(ticks))
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["ts", "buy_vol", "sell_vol", "delta", "n_ticks", "spread_med", "mid"])
    if not panel.empty:
        panel = panel.drop_duplicates("ts").sort_values("ts", ignore_index=True)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(output, compression="zstd", index=False)
    return panel, present


def tick_event_features(
    cfg: Config, events: pd.DataFrame, ticks_by_day, atr_values: pd.Series | None = None
) -> pd.DataFrame:
    result = pd.DataFrame(index=events.index)
    numeric = ["time_beyond_level_sec", "displacement_60s", "displacement_180s"]
    result[numeric] = np.nan
    result["t_extreme"] = pd.Series(pd.NaT, index=events.index, dtype="datetime64[ns]")
    result["displacement_60s_truncated"] = False
    result["displacement_180s_truncated"] = False
    result["ticks_missing"] = True
    if events.empty:
        return result
    atr_arr = (
        atr_values.reindex(events.index).to_numpy(float)
        if atr_values is not None else np.ones(len(events))
    )
    atr_by_pos = dict(zip(events.index, atr_arr, strict=True))
    day_keys = pd.to_datetime(events["bar_ts_utc"]).dt.date
    for day, positions in events.groupby(day_keys, sort=False).groups.items():
        ticks = ticks_by_day(day)
        if ticks.empty:
            continue
        q = lee_ready(ticks)
        ts = q["ts"].to_numpy(dtype="datetime64[ns]")
        mid = q["mid"].to_numpy(float)
        for pos in positions:
            event = events.loc[pos]
            lo = np.searchsorted(ts, np.datetime64(event.bar_ts_utc), side="left")
            hi = np.searchsorted(ts, np.datetime64(event.event_ts_utc), side="left")
            if hi <= lo:
                continue
            event_ts = ts[lo:hi]
            event_mid = mid[lo:hi]
            beyond = event_mid > event.level_price if event.sign < 0 else event_mid < event.level_price
            result.loc[pos, "ticks_missing"] = False
            if hi - lo > 1:
                dt = (event_ts[1:] - event_ts[:-1]).astype("timedelta64[ns]").astype(float) / 1e9
                result.loc[pos, "time_beyond_level_sec"] = float(dt[beyond[:-1]].sum())
            beyond_idx = np.flatnonzero(beyond)
            if not len(beyond_idx):
                continue
            extreme_idx = beyond_idx[np.argmax(event_mid[beyond] * -event.sign)]
            extreme_ts = event_ts[extreme_idx]
            extreme_mid = event_mid[extreme_idx]
            result.loc[pos, "t_extreme"] = pd.Timestamp(extreme_ts)
            atr = atr_by_pos[pos]
            for window in (60, 180):
                target = min(
                    pd.Timestamp(event.event_ts_utc),
                    pd.Timestamp(extreme_ts) + pd.Timedelta(seconds=window),
                )
                target_i = np.searchsorted(event_ts, np.datetime64(target), side="right") - 1
                if target_i < extreme_idx:
                    target_i = extreme_idx
                effective = max((target - pd.Timestamp(extreme_ts)).total_seconds(), 1.0)
                result.loc[pos, f"displacement_{window}s_truncated"] = (
                    target < pd.Timestamp(extreme_ts) + pd.Timedelta(seconds=window)
                )
                result.loc[pos, f"displacement_{window}s"] = (
                    (extreme_mid - event_mid[target_i]) * (-event.sign) / atr / effective
                    if np.isfinite(atr) and atr > 0 else np.nan
                )
    return result
