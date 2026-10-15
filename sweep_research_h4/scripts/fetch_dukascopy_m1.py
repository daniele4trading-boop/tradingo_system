"""Scarica candele M1 BID+ASK Dukascopy e le scrive nel layout warehouse.

Layout: <root>/bars/symbol=<S>/source=<SRC>/tf=M1/anchor=0/year=<Y>/<Y-MM>.parquet
Colonne: ts (UTC naive), open/high/low/close (BID), n_ticks (0, non disponibile
dalle candele), volume (BID), spread_med (mediana ASK-BID su O/H/L/C del minuto).
I minuti paddati da Dukascopy (volume 0, flat) restano: li scarta il loader S0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import lzma
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://datafeed.dukascopy.com/datafeed"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
DIVISOR = {"EURUSD": 1e5, "GBPUSD": 1e5}
RECORD = struct.Struct(">5if")
log = logging.getLogger("fetch")
_session = requests.Session()


def fetch(url: str) -> bytes | None:
    delay = 2.0
    for _ in range(12):
        try:
            r = _session.get(url, headers=HEADERS, timeout=60)
        except requests.RequestException:
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if r.status_code == 200:
            return r.content
        if r.status_code == 404:
            return None
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise RuntimeError(f"fetch fallito: {url}")


def decode(raw: bytes | None, day: dt.date, divisor: float) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["ts", "open", "close", "low", "high", "volume"])
    data = lzma.decompress(raw)
    arr = np.array(list(RECORD.iter_unpack(data)), dtype=float)
    frame = pd.DataFrame(arr, columns=["sec", "open", "close", "low", "high", "volume"])
    for col in ["open", "close", "low", "high"]:
        frame[col] = frame[col] / divisor
    frame["ts"] = pd.Timestamp(day) + pd.to_timedelta(frame.pop("sec"), unit="s")
    return frame


def fetch_day(code: str, day: dt.date, divisor: float) -> pd.DataFrame:
    path = f"{BASE}/{code}/{day.year}/{day.month - 1:02d}/{day.day:02d}"
    bid = decode(fetch(f"{path}/BID_candles_min_1.bi5"), day, divisor)
    ask = decode(fetch(f"{path}/ASK_candles_min_1.bi5"), day, divisor)
    if bid.empty:
        return bid
    m = bid.merge(ask, on="ts", how="left", suffixes=("", "_ask"))
    spreads = np.column_stack([m[f"{c}_ask"] - m[c] for c in ["open", "high", "low", "close"]])
    m["spread_med"] = np.nanmedian(spreads, axis=1) if len(m) else []
    m["n_ticks"] = 0
    return m[["ts", "open", "high", "low", "close", "n_ticks", "volume", "spread_med"]]


def month_days(year: int, month: int) -> list[dt.date]:
    start = dt.date(year, month, 1)
    end = (pd.Timestamp(start) + pd.offsets.MonthBegin(1)).date()
    return [start + dt.timedelta(days=i) for i in range((end - start).days)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--code", help="codice Dukascopy, default = symbol")
    ap.add_argument("--source", default="dukascopy")
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (incluso)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    code = args.code or args.symbol
    divisor = DIVISOR.get(code, 1e3)
    end = pd.Timestamp(args.end).date()
    root = Path(args.root) / f"bars/symbol={args.symbol}/source={args.source}/tf=M1/anchor=0"
    for period in pd.period_range(args.start, end, freq="M"):
        out = root / f"year={period.year}" / f"{period.year}-{period.month:02d}.parquet"
        if out.exists():
            continue
        days = [d for d in month_days(period.year, period.month) if d <= end]
        with ThreadPoolExecutor(args.workers) as pool:
            frames = list(pool.map(lambda d: fetch_day(code, d, divisor), days))
        frames = [f for f in frames if not f.empty]
        if not frames:
            log.info("%s %s vuoto", args.symbol, period)
            continue
        month = pd.concat(frames, ignore_index=True).sort_values("ts")
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        month.to_parquet(tmp, index=False, compression="zstd")
        tmp.rename(out)
        log.info("%s %s rows=%d", args.symbol, period, len(month))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
