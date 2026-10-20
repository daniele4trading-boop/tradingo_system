from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pandas as pd

from .bars import aggregate_h4
from .config import Config


def _root(cfg: Config, symbol: str) -> Path:
    source = cfg.sources.get(symbol, "dukascopy")
    return Path(cfg.data_root) / f"bars/symbol={symbol}/source={source}/tf={cfg.h4.source_tf}/anchor=0"


def list_m1_files(cfg: Config, symbol: str) -> list[Path]:
    root = _root(cfg, symbol)
    if not root.exists():
        return []
    symbol_start = cfg.symbol_period_start.get(symbol)
    start = max(
        [pd.Timestamp(value) for value in (cfg.start, symbol_start) if value],
        default=None,
    )
    start_year = start.year if start is not None else None
    end_year = pd.Timestamp(cfg.end).year if cfg.end else None
    return sorted(
        p
        for p in root.glob("year=*/*.parquet")
        if (start_year is None or int(p.parent.name.split("=")[1]) >= start_year)
        and (end_year is None or int(p.parent.name.split("=")[1]) <= end_year)
    )


def read_m1(cfg: Config, symbol: str) -> pd.DataFrame:
    frame, _ = read_m1_with_stats(cfg, symbol)
    return frame


def read_m1_with_stats(cfg: Config, symbol: str) -> tuple[pd.DataFrame, dict]:
    files = list_m1_files(cfg, symbol)
    if not files:
        empty = pd.DataFrame(
            columns=["ts", "open", "high", "low", "close", "n_ticks", "volume", "spread_med"]
        )
        return empty, {
            "rows_before": 0,
            "rows_dropped": 0,
            "drop_pct": 0.0,
            "weekday_before": {},
            "weekday_after": {},
        }
    glob = str(_root(cfg, symbol) / "year=*/*.parquet")
    where: list[str] = []
    params: list[object] = []
    symbol_start = cfg.symbol_period_start.get(symbol)
    start = max(
        [pd.Timestamp(value) for value in (cfg.start, symbol_start) if value],
        default=None,
    )
    if start is not None:
        where.append("ts >= ?")
        params.append(start.to_pydatetime())
    if cfg.end:
        where.append("ts < ?")
        params.append((pd.Timestamp(cfg.end) + pd.Timedelta(days=1)).to_pydatetime())
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    with duckdb.connect() as con:
        frame = con.execute("SELECT * FROM read_parquet(?)" + clause + " ORDER BY ts", [glob, *params]).df()
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_localize(None)
    rows_before = len(frame)
    weekday_before = frame["ts"].dt.dayofweek.value_counts().sort_index().astype(int).to_dict()
    dropped = (
        frame["volume"].eq(0) & frame["high"].eq(frame["low"])
        if cfg.drop_flat_zero_volume_m1
        else pd.Series(False, index=frame.index)
    )
    frame = frame.loc[~dropped].reset_index(drop=True)
    weekday_after = frame["ts"].dt.dayofweek.value_counts().sort_index().astype(int).to_dict()
    return frame, {
        "rows_before": int(rows_before),
        "rows_dropped": int(dropped.sum()) if cfg.drop_flat_zero_volume_m1 else 0,
        "drop_pct": float(dropped.mean() * 100.0) if rows_before else 0.0,
        "weekday_before": {str(k): int(v) for k, v in weekday_before.items()},
        "weekday_after": {str(k): int(v) for k, v in weekday_after.items()},
    }


def read_h4(cfg: Config, symbol: str) -> pd.DataFrame:
    m1 = read_m1(cfg, symbol)
    return aggregate_h4(m1, cfg.h4.anchor_utc_hour)


def sha256_files(files: list[Path]) -> dict[str, str]:
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
