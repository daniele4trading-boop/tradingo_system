from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def _ny_year_hour(values: pd.Series | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    local = pd.DatetimeIndex(pd.to_datetime(values, utc=True)).tz_convert(
        "America/New_York"
    )
    return local.year.to_numpy(), local.hour.to_numpy()


def _forward_close(close: np.ndarray, ts: np.ndarray, targets: np.ndarray) -> np.ndarray:
    indices = np.searchsorted(ts, targets, side="left")
    result = np.full(len(targets), np.nan)
    valid = indices < len(close)
    result[valid] = close[indices[valid]]
    return result


def _drift_table(
    ts: np.ndarray, close: np.ndarray, horizons: list[int]
) -> dict[str, dict[str, float]]:
    years, hours = _ny_year_hour(ts)
    table: dict[str, dict[str, float]] = {}
    for horizon in horizons:
        future = _forward_close(close, ts, ts + np.timedelta64(horizon, "m"))
        values = (future / close - 1.0) * 1e4
        frame = pd.DataFrame({"year": years, "ny_hour": hours, "value": values})
        frame = frame[np.isfinite(frame["value"])]
        grouped = frame.groupby(["year", "ny_hour"], sort=True)["value"].mean()
        table[str(horizon)] = {
            f"{int(year)}|{int(hour)}": float(value)
            for (year, hour), value in grouped.items()
        }
    return table


def _spread_by_hour(m1: pd.DataFrame) -> dict[str, float]:
    if "spread_med" not in m1:
        return {}
    _, hours = _ny_year_hour(m1["ts"])
    frame = pd.DataFrame({"hour": hours, "spread": m1["spread_med"].to_numpy(float)})
    frame = frame[np.isfinite(frame["spread"])]
    return {
        str(int(hour)): float(value)
        for hour, value in frame.groupby("hour", sort=True)["spread"].median().items()
    }


def _write_tables(cfg, drift: dict, spread: dict) -> None:
    output = Path(cfg.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "drift_table.json").write_text(
        json.dumps(drift, indent=2, ensure_ascii=False)
    )
    (output / "spread_by_hour.json").write_text(
        json.dumps(spread, indent=2, ensure_ascii=False)
    )


def add_outcomes(events: pd.DataFrame, m1: pd.DataFrame, cfg) -> pd.DataFrame:
    out = events.copy()
    if out.empty:
        _write_tables(cfg, {}, {})
        return out
    m1 = m1.sort_values("ts").reset_index(drop=True)
    ts = m1["ts"].to_numpy(dtype="datetime64[ns]")
    close = m1["close"].to_numpy(float)
    high = m1["high"].to_numpy(float)
    low = m1["low"].to_numpy(float)
    event_ts = out["event_ts_utc"].to_numpy(dtype="datetime64[ns]")
    bar_close = out["bar_close"].to_numpy(float)
    trade_sign = out.get("trade_sign", out["sign"]).to_numpy(float)

    entry_indices = np.searchsorted(ts, event_ts, side="left")
    entry_valid = entry_indices < len(ts)
    entry_c1 = np.full(len(out), np.nan)
    entry_c1[entry_valid] = close[entry_indices[entry_valid]]
    entry_ts = np.full(len(out), np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
    entry_ts[entry_valid] = ts[entry_indices[entry_valid]] + np.timedelta64(1, "m")
    out["entry_c1"] = entry_c1
    out["entry_c1_ts"] = pd.to_datetime(entry_ts)
    out["exec_slip_bp"] = (
        (entry_c1 / bar_close - 1.0) * 1e4 * trade_sign
    )

    drift = _drift_table(ts, close, cfg.outcome_horizons_min)
    spread_by_hour = _spread_by_hour(m1)
    event_years, event_hours = _ny_year_hour(out["event_ts_utc"])
    for horizon in cfg.outcome_horizons_min:
        future = _forward_close(close, ts, event_ts + np.timedelta64(horizon, "m"))
        raw = (future / bar_close - 1.0) * 1e4
        out[f"fwd_ret_{horizon}_bp"] = raw
        out[f"fwd_ret_{horizon}_dir_bp"] = raw * trade_sign
        c1_future = _forward_close(
            close, ts, entry_ts + np.timedelta64(horizon, "m")
        )
        c1_raw = (c1_future / entry_c1 - 1.0) * 1e4
        c1_raw[~entry_valid] = np.nan
        out[f"fwd_ret_{horizon}_c1_bp"] = c1_raw
        out[f"fwd_ret_{horizon}_c1_dir_bp"] = c1_raw * trade_sign
        drift_values = np.array([
            drift[str(horizon)].get(f"{year}|{hour}", np.nan)
            for year, hour in zip(event_years, event_hours, strict=True)
        ])
        ex = c1_raw - drift_values
        out[f"fwd_ret_{horizon}_c1_ex_bp"] = ex
        out[f"fwd_ret_{horizon}_c1_ex_dir_bp"] = ex * trade_sign

    out["cost_rt_bp"] = np.array([
        spread_by_hour.get(str(int(hour)), np.nan) / entry * 1e4
        if np.isfinite(entry) and entry != 0 else np.nan
        for hour, entry in zip(event_hours, entry_c1, strict=True)
    ])

    horizon = cfg.mfe_mae_horizon_min
    starts = np.searchsorted(ts, entry_ts, side="left")
    ends = np.searchsorted(ts, entry_ts + np.timedelta64(horizon, "m"), side="left")
    mfe = np.full(len(out), np.nan)
    mae = np.full(len(out), np.nan)
    for i, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if not entry_valid[i] or start >= end:
            continue
        future_high = high[start:end]
        future_low = low[start:end]
        if trade_sign[i] > 0:
            mfe[i] = np.max(future_high - entry_c1[i])
            mae[i] = np.max(entry_c1[i] - future_low)
        else:
            mfe[i] = np.max(entry_c1[i] - future_low)
            mae[i] = np.max(future_high - entry_c1[i])
    out[f"mfe_{horizon}_pts"] = mfe
    out[f"mae_{horizon}_pts"] = mae
    atr = out["atr_tf"].to_numpy(float)
    out[f"mfe_{horizon}_atr"] = np.divide(
        mfe, atr, out=np.full(len(out), np.nan), where=atr != 0
    )
    out[f"mae_{horizon}_atr"] = np.divide(
        mae, atr, out=np.full(len(out), np.nan), where=atr != 0
    )
    _write_tables(cfg, drift, spread_by_hour)
    return out
