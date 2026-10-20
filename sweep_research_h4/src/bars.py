from __future__ import annotations

import pandas as pd

from .primitives import atr


def aggregate_h4(m1: pd.DataFrame, anchor_utc_hour: int = 21) -> pd.DataFrame:
    """Aggregate M1 bid bars to a fixed UTC H4 grid, using M1 mid OHLC."""
    columns = [
        "ts",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "n_ticks",
        "spread_med",
        "n_m1",
        "bar_slot",
        "atr14",
        "year",
    ]
    if m1.empty:
        return pd.DataFrame(columns=columns)
    frame = m1.copy()
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_localize(None)
    spread = frame["spread_med"].fillna(0.0).astype(float) / 2.0
    for col in ("open", "high", "low", "close"):
        frame[f"mid_{col}"] = frame[col].astype(float) + spread
    frame = frame.set_index("ts").sort_index()
    grouped = frame.resample(
        "4h", origin="epoch", offset=pd.Timedelta(hours=anchor_utc_hour), label="left", closed="left"
    )
    out = grouped.agg(
        open=("mid_open", "first"),
        high=("mid_high", "max"),
        low=("mid_low", "min"),
        close=("mid_close", "last"),
        volume=("volume", "sum"),
        n_ticks=("n_ticks", "sum"),
        spread_med=("spread_med", "median"),
        n_m1=("mid_close", "count"),
    ).dropna(subset=["open", "high", "low", "close"])
    out.index.name = "ts"
    out = out.reset_index()
    out["bar_slot"] = out["ts"].dt.hour
    out["atr14"] = atr(out, 14)
    out["year"] = out["ts"].dt.year
    return out[columns]
