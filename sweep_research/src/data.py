from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from .config import Config

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}


def _glob(root: Path, symbol: str, tf: str) -> str:
    return str(root / f"bars/symbol={symbol}/source=dukascopy/tf={tf}/anchor=0/year=*/*.parquet")


def list_data_files(
    cfg: Config,
    symbol: str | None,
    tf: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
) -> list[Path]:
    """Elenca i file parquet bar che intersecano il periodo richiesto."""
    symbol = symbol or cfg.symbol
    root = Path(cfg.data_root) / f"bars/symbol={symbol}/source=dukascopy/tf={tf}/anchor=0"
    if not root.exists():
        return []
    lo = pd.Timestamp(start or cfg.start).year
    hi = pd.Timestamp(end or cfg.end).year
    return sorted(
        path for path in root.glob("year=*/*.parquet")
        if lo <= int(path.parent.name.split("=")[1]) <= hi
    )


def read_bars(
    cfg: Config,
    symbol: str | None,
    tf: str,
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    warmup_days: int = 0,
) -> pd.DataFrame:
    symbol = symbol or cfg.symbol
    path = Path(cfg.data_root) / f"bars/symbol={symbol}/source=dukascopy/tf={tf}/anchor=0"
    if not path.exists():
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "n_ticks", "volume", "spread_med"])
    lo = pd.Timestamp(start or cfg.start) - pd.Timedelta(days=warmup_days)
    hi = pd.Timestamp(end or cfg.end) + pd.Timedelta(days=1)
    query = (
        f"SELECT * FROM read_parquet('{_glob(Path(cfg.data_root), symbol, tf)}') "
        "WHERE ts >= ? AND ts < ? ORDER BY ts"
    )
    with duckdb.connect() as con:
        out = con.execute(query, [lo.to_pydatetime(), hi.to_pydatetime()]).df()
    out["ts"] = pd.to_datetime(out["ts"]).dt.tz_localize(None)
    return out


def read_m1(
    cfg: Config, symbol: str | None = None, start=None, end=None, warmup_days: int = 0
) -> pd.DataFrame:
    return read_bars(cfg, symbol, "M1", start, end, warmup_days)


def tick_days(
    cfg: Config, symbol: str | None = None, start=None, end=None
) -> Iterator[tuple[date, pd.DataFrame]]:
    symbol = symbol or cfg.symbol
    root = Path(cfg.data_root) / f"ticks/symbol={symbol}/source=dukascopy"
    if not root.exists():
        return
    lo = pd.Timestamp(start or cfg.start).date()
    hi = pd.Timestamp(end or cfg.end).date()
    for day in pd.date_range(lo, hi, freq="D"):
        path = root / f"year={day.year}/{day:%Y-%m-%d}.parquet"
        if path.exists():
            ticks = pd.read_parquet(path)
            ticks["ts"] = pd.to_datetime(ticks["ts"]).dt.tz_localize(None)
            yield day.date(), ticks.sort_values("ts", ignore_index=True)


def tick_loader(cfg: Config, symbol: str | None = None, start=None, end=None):
    """Restituisce un loader lazy per i tick grezzi, un giorno alla volta."""
    lo = pd.Timestamp(start or cfg.start).date()
    hi = pd.Timestamp(end or cfg.end).date()
    root = Path(cfg.data_root) / f"ticks/symbol={symbol or cfg.symbol}/source=dukascopy"

    def load(day: date) -> pd.DataFrame:
        if day < lo or day > hi:
            return pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
        path = root / f"year={day.year}/{day:%Y-%m-%d}.parquet"
        if not path.exists():
            result = pd.DataFrame(columns=["ts", "bid", "ask", "bid_vol", "ask_vol"])
        else:
            result = pd.read_parquet(path)
            result["ts"] = pd.to_datetime(result["ts"]).dt.tz_localize(None)
            result = result.sort_values("ts", ignore_index=True)
        return result

    return load


def sha256_files(cfg: Config, symbols: list[str], tfs: list[str], start=None, end=None) -> dict[str, str]:
    out: dict[str, str] = {}
    for symbol in symbols:
        for tf in tfs:
            root = Path(cfg.data_root) / f"bars/symbol={symbol}/source=dukascopy/tf={tf}/anchor=0"
            if not root.exists():
                continue
            for path in sorted(root.glob("*/*.parquet")):
                h = hashlib.sha256(path.read_bytes()).hexdigest()
                out[str(path)] = h
        tick_root = Path(cfg.data_root) / f"ticks/symbol={symbol}/source=dukascopy"
        if tick_root.exists():
            lo = pd.Timestamp(start or cfg.start).date()
            hi = pd.Timestamp(end or cfg.end).date()
            for path in sorted(tick_root.glob("year=*/*.parquet")):
                day = pd.Timestamp(path.stem).date()
                if lo <= day <= hi:
                    out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out
