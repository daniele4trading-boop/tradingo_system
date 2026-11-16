from __future__ import annotations

import numpy as np
import pandas as pd


def drift_table(bars: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    rows = []
    for h in horizons:
        values = (bars["close"].shift(-h) - bars["open"].shift(-1)) / bars["open"].shift(-1) * 1e4
        frame = pd.DataFrame(
            {"symbol": bars.get("symbol", "UNKNOWN"), "year": bars["year"], "h": h, "value": values}
        )
        rows.append(frame.dropna().groupby(["symbol", "year", "h"], as_index=False)["value"].mean())
    if not rows:
        return pd.DataFrame(columns=["symbol", "year", "h", "drift_bp"])
    out = pd.concat(rows, ignore_index=True).rename(columns={"value": "drift_bp"})
    return out


def compute_outcomes(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    horizons: list[int],
    mfe_horizon: int = 12,
    drift: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, int]:
    if events.empty:
        return events.copy(), 0
    drift_map = {}
    if drift is not None and not drift.empty:
        drift_map = {
            (r.symbol, int(r.year), int(r.h)): float(r.drift_bp) for r in drift.itertuples(index=False)
        }
    rows = []
    dropped = 0
    for event in events.itertuples(index=False):
        t = int(event.bar_idx)
        if t + 1 >= len(bars):
            dropped += 1
            continue
        entry = float(bars.iloc[t + 1].open)
        close_t = float(bars.iloc[t].close)
        sign = int(event.trade_sign)
        row = event._asdict()
        row["entry_bar_close"] = close_t
        row["entry_next_open"] = entry
        row["exec_cost_bp"] = (entry - close_t) / close_t * 1e4 * sign
        row["next_bar_gap_h"] = (
            bars.iloc[t + 1].ts - (bars.iloc[t].ts + pd.Timedelta(hours=4))
        ).total_seconds() / 3600.0
        row["n_m1_bar"] = int(bars.iloc[t]["n_m1"]) if "n_m1" in bars else np.nan
        row["spread_bp_entry"] = float(bars.iloc[t + 1].spread_med) / entry * 1e4
        row["cost_rt_bp"] = row["spread_bp_entry"]
        for h in horizons:
            value = np.nan
            if t + h < len(bars):
                value = (float(bars.iloc[t + h].close) - entry) / entry * 1e4
            row[f"fwd_ret_{h}_bp"] = value
            row[f"fwd_ret_{h}_dir_bp"] = value * sign if np.isfinite(value) else np.nan
            drift_value = drift_map.get((event.symbol, int(bars.iloc[t].year), h), np.nan)
            row[f"fwd_ret_{h}_ex_bp"] = (
                (value - drift_value) * sign if np.isfinite(value) and np.isfinite(drift_value) else np.nan
            )
        end = t + mfe_horizon
        row["label_start_ts"] = bars.iloc[t + 1].ts
        row["label_end_ts"] = bars.iloc[min(end, len(bars) - 1)].ts + pd.Timedelta(hours=4)
        row["label_truncated"] = end >= len(bars)
        if end < len(bars):
            highs = bars.iloc[t + 1 : end + 1].high.to_numpy(float)
            lows = bars.iloc[t + 1 : end + 1].low.to_numpy(float)
            if sign > 0:
                row["mfe_12_pts"] = float(highs.max() - entry)
                row["mae_12_pts"] = float(entry - lows.min())
            else:
                row["mfe_12_pts"] = float(entry - lows.min())
                row["mae_12_pts"] = float(highs.max() - entry)
            row["mfe_12_atr"] = row["mfe_12_pts"] / float(bars.iloc[t].atr14)
            row["mae_12_atr"] = row["mae_12_pts"] / float(bars.iloc[t].atr14)
        else:
            row["mfe_12_pts"] = np.nan
            row["mae_12_pts"] = np.nan
            row["mfe_12_atr"] = np.nan
            row["mae_12_atr"] = np.nan
        rows.append(row)
    return pd.DataFrame(rows), dropped
