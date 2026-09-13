"""Test della variante H1 false-breakout su M1 (barre sintetiche)."""
from __future__ import annotations

from datetime import time

import pandas as pd

from orb_vwap_module.h1_fbo import H1FboConfig, run, summarize
from orb_vwap_module.session import SessionSpec, build_sessions
from orb_vwap_module.setup_engine import SHORT
from orb_vwap_module.vwap import session_vwap

# 2026-01-12 (inverno): 09:00 NY = 14:00 UTC. ORB = candela H1 14:00-15:00 (range 100-110).
# Finestra trigger 15:00-15:35.


def _m1(path):
    """Barre M1 dalle 14:00 alle 16:00 UTC: prezzo piatto a 107.5 (dentro il range, corpo
    sopra il VWAP ~107.45; volume alto nella prima ora) tranne i punti forzati da `path`
    {minuto_da_1400: (open, high, low, close, volume)}."""
    idx = pd.date_range("2026-01-12 14:00", periods=120, freq="1min")
    rows = []
    for i in range(120):
        if i in path:
            rows.append(path[i])
        elif i == 0:
            rows.append((105, 110, 100, 105, 100))
        else:
            rows.append((107.5, 107.6, 107.2, 107.5, 1000 if i < 60 else 100))
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx).astype(float)
    df["spread"] = 0.1
    return df


def _prepared(m1: pd.DataFrame, cfg: H1FboConfig):
    spec = SessionSpec(tz=cfg.tz, open_time=cfg.session_open, cutoff_time=cfg.close_time)
    days = build_sessions(m1.index, spec)
    m15 = m1.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                    "volume": "sum", "spread": "mean"}).dropna()
    m15["vwap"] = session_vwap(m15, days, spec)
    m1 = m1.copy()
    m1["atr"] = float("nan")
    h1 = m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                "volume": "sum"}).dropna()
    return m1, m15, h1, days


CFG = H1FboConfig(cutoff=time(12, 0), volume_filter=True, vol_k=1.0, close_time=time(11, 0))


def test_false_breakout_up_opens_short_with_orb_levels():
    # 15:03 break-out sopra 110; 15:10 chiude col corpo sotto il VWAP (~105) con volume alto
    m1 = _m1({63: (110.5, 110.8, 110.3, 110.7, 1),
              70: (104.9, 105.0, 103.5, 103.8, 5000),
              80: (103, 103.2, 99.0, 99.5, 100)})
    res = run("", CFG, _prepared(m1, CFG))
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t["direction"] == SHORT
    assert t["breakout_ts"] == pd.Timestamp("2026-01-12 15:03")
    assert t["trigger_ts"] == pd.Timestamp("2026-01-12 15:10")
    assert t["sl"] == 110.0 and t["tp"] == 100.0
    assert t["entry"] == 103.8 - 0.1
    assert t["exit_reason"] == "TP1"
    assert summarize(res)["trades"] == 1


def test_no_trade_without_volume_or_without_breakout():
    m1 = _m1({63: (110.5, 110.8, 110.3, 110.7, 1), 70: (104.9, 105.0, 103.5, 103.8, 50)})
    res = run("", CFG, _prepared(m1, CFG))
    assert len(res.trades) == 0
    assert "TRIGGER_NO_VOLUME" in set(res.windows["state"])

    m1 = _m1({70: (104.9, 105.0, 103.5, 103.8, 5000)})  # cross del VWAP ma nessun break-out
    res = run("", CFG, _prepared(m1, CFG))
    assert len(res.trades) == 0
    assert set(res.windows["state"]) == {"NO_BREAKOUT"}


def test_trigger_after_window_is_ignored():
    m1 = _m1({63: (110.5, 110.8, 110.3, 110.7, 1), 96: (104.9, 105.0, 103.5, 103.8, 5000)})  # 15:36
    res = run("", CFG, _prepared(m1, CFG))
    assert len(res.trades) == 0
    assert list(res.windows["state"]) == ["BREAKOUT_NO_TRIGGER", "NO_BREAKOUT"]
