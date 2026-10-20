"""orb_touch: break M5 oltre l'ORB, trigger al tocco del VWAP, SL sull'estremo della candela trigger ± buffer."""
from datetime import time

import pandas as pd

from orb_vwap_module import orb_touch
from orb_vwap_module.orb_touch import OrbTouchConfig, run


def _m5(rows, start="2026-03-02 14:30"):  # 09:30 NY = 14:30 UTC (EST)
    idx = pd.date_range(start, periods=len(rows), freq="5min")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df.index.name = "ts"
    df["volume"] = 100.0
    df["spread"] = 0.0
    return df


def test_break_up_touch_vwap_long_sl_on_trigger_low(monkeypatch):
    # ORB 09:30-09:45 = 100..110. Poi break sopra (close 112), ritraccio che tocca il VWAP, TP RR1.
    rows = [(105, 110, 100, 105), (105, 106, 104, 105), (105, 106, 104, 105),
            (105, 113, 105, 112),             # break: close > 110
            (112, 112, 104, 108),             # tocca il VWAP (~105.x): trigger, entry 108
            (108, 116, 108, 115)]             # TP 1:1 raggiunto
    df = _m5(rows)
    monkeypatch.setattr(orb_touch, "load_symbol", lambda *a, **k: df)
    t, d, e = run(".", OrbTouchConfig(start="2026-01-01", close_time=time(17, 0), sl_buffer=2.0))
    assert len(t) == 1
    tr = t.iloc[0]
    assert tr.direction == "long" and tr.entry_ts == df.index[4]
    assert tr.entry == 108 and tr.sl == 104 - 2.0 and tr.tp == 108 + 6.0
    assert tr.reason == "TP1" and d.iloc[0].state == "TRIGGERED"


def test_no_trigger_without_touch_and_one_trade_per_day(monkeypatch):
    rows = [(105, 110, 100, 105), (105, 106, 104, 105), (105, 106, 104, 105),
            (105, 113, 105, 112), (112, 115, 111, 114), (114, 116, 113, 115)]  # mai giu' al VWAP
    df = _m5(rows)
    monkeypatch.setattr(orb_touch, "load_symbol", lambda *a, **k: df)
    t, d, _ = run(".", OrbTouchConfig(start="2026-01-01"))
    assert t.empty and d.iloc[0].state == "BREAK" and d.iloc[0].break_side == "long"


def test_break_after_window_ignored(monkeypatch):
    rows = [(105, 110, 100, 105)] + [(105, 106, 104, 105)] * 48 + [(105, 113, 105, 112), (112, 112, 104, 108)]
    df = _m5(rows)  # break a 09:30 + 49*5 min = 13:35 NY > 4h
    monkeypatch.setattr(orb_touch, "load_symbol", lambda *a, **k: df)
    t, d, _ = run(".", OrbTouchConfig(start="2026-01-01", window_hours=4.0))
    assert t.empty and d.iloc[0].state == "NO_BREAK"
