"""Test dei filtri di conferma opzionali (RSI divergenza / test 50 %) e della
regressione "flag OFF = comportamento identico" su entrambi i motori."""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from orb_vwap_module.h1_fbo import H1FboConfig
from orb_vwap_module.h1_fbo import run as run_h1
from orb_vwap_module.orb import ORB
from orb_vwap_module.rsi_liquidity_filter import (ConfirmParams, HtfHolder, align_closed, depth_ok, divergence_ok,
                                                  level_rsi, rsi)
from orb_vwap_module.setup_engine import LONG, SHORT, SetupParams, run_session_setup
from orb_vwap_module.tests.test_h1_fbo import _m1, _prepared
from orb_vwap_module.tests.test_orb_vwap import bars, session

# ---------------------------------------------------------------- indicatori


def test_rsi_known_values_and_bounds():
    up = pd.Series(np.arange(1.0, 40.0))
    assert rsi(up, 14).iloc[-1] == 100.0            # solo rialzi
    down = pd.Series(np.arange(40.0, 1.0, -1))
    assert rsi(down, 14).iloc[-1] == 0.0
    alt = pd.Series([10.0, 11.0] * 30)              # +1/-1 alternati -> RSI ~50
    v = rsi(alt, 14).iloc[-1]
    assert 45 < v < 55
    assert rsi(up, 14).iloc[:14].isna().all()       # warm-up


def test_align_closed_uses_only_closed_htf_bar():
    htf = pd.Series([10.0, 20.0, 30.0], index=pd.date_range("2026-01-12 14:00", periods=3, freq="15min"))
    idx = pd.date_range("2026-01-12 14:00", periods=46, freq="1min")
    a = align_closed(htf, "M15", idx)
    assert pd.isna(a.loc["2026-01-12 14:14"])       # la barra 14:00 chiude alle 14:15
    assert a.loc["2026-01-12 14:15"] == 10.0
    assert a.loc["2026-01-12 14:29"] == 10.0
    assert a.loc["2026-01-12 14:30"] == 20.0
    assert a.loc["2026-01-12 14:45"] == 30.0


def test_level_rsi_picks_extreme_bar():
    idx = pd.date_range("2026-01-12 14:00", periods=4, freq="15min")
    htf = pd.DataFrame({"high": [101, 105, 103, 120], "low": [99, 95, 98, 50], "rsi": [50, 75, 60, 90]}, index=idx)
    start, end = idx[0], idx[3]                     # esclude l'ultima barra
    assert level_rsi(htf, start, end, "UP") == 75
    assert level_rsi(htf, start, end, "DOWN") == 75
    assert pd.isna(level_rsi(htf, idx[3] + pd.Timedelta(hours=5), idx[3] + pd.Timedelta(hours=6), "UP"))


# ---------------------------------------------------------------- divergenza / profondita'


def test_divergence_strict_and_loose():
    strict, loose = ConfirmParams(rsi_strict=True), ConfirmParams(rsi_strict=False)
    # sweep sopra il massimo: livello RSI 75, sweep RSI 68 -> divergenza (LH)
    assert divergence_ok("UP", 75, 68, strict)
    assert not divergence_ok("UP", 75, 78, strict)  # RSI conferma il nuovo massimo
    assert not divergence_ok("UP", 65, 60, strict)  # livello non estremo
    assert divergence_ok("UP", 65, 60, loose)
    # sweep sotto il minimo: livello RSI 25, sweep RSI 33 -> divergenza (HL)
    assert divergence_ok("DOWN", 25, 33, strict)
    assert not divergence_ok("DOWN", 25, 20, strict)
    assert not divergence_ok("DOWN", 40, 45, strict)
    assert divergence_ok("DOWN", 40, 45, loose)
    assert not divergence_ok("UP", float("nan"), 60, loose)


def test_depth_30_50_70_pct():
    p = ConfirmParams()
    width = 10.0
    assert not depth_ok("UP", 110, 113, width, p)   # 30 %
    assert depth_ok("UP", 110, 115, width, p)       # 50 %
    assert depth_ok("UP", 110, 117, width, p)       # 70 %
    assert not depth_ok("DOWN", 100, 97, width, p)
    assert depth_ok("DOWN", 100, 95, width, p)
    assert depth_ok("DOWN", 100, 96, width, ConfirmParams(min_pct=0.4))
    assert not depth_ok("UP", 110, 115, 0.0, p)


# ---------------------------------------------------------------- motore (a) H1 false-breakout

CFG = H1FboConfig(cutoff=time(12, 0), volume_filter=True, vol_k=1.0, close_time=time(11, 0))
# range H1 100-110; break-out a 110.7 (profondita' 0.8/10 = 8 %) e trigger short
PATH_SHALLOW = {63: (110.5, 110.8, 110.3, 110.7, 1), 70: (104.9, 105.0, 103.5, 103.8, 5000),
                80: (103, 103.2, 99.0, 99.5, 100)}
PATH_DEEP = {63: (110.5, 116.0, 110.3, 110.7, 1), 70: (104.9, 105.0, 103.5, 103.8, 5000),
             80: (103, 103.2, 99.0, 99.5, 100)}


def _prep_with_rsi(m1, cfg, rsi_values):
    """Come `_prepared` piu' colonna `rsi` M1 forzata (ultima M15 chiusa) e htf con RSI."""
    m1p, m15, h1, days = _prepared(m1, cfg)
    htf = m15.copy()
    htf["rsi"] = rsi_values(htf.index)
    m1p["rsi"] = align_closed(htf["rsi"], "M15", m1p.index)
    m1p.attrs["rsi_htf"] = HtfHolder(htf)
    return m1p, m15, h1, days


def test_h1_flags_off_identical_to_baseline():
    m1 = _m1(PATH_DEEP)
    base = run_h1("", CFG, _prepared(m1, CFG))
    off = H1FboConfig(**{**CFG.__dict__, "confirm": ConfirmParams()})
    res = run_h1("", off, _prepared(m1, off))
    pd.testing.assert_frame_equal(base.trades, res.trades)
    pd.testing.assert_frame_equal(base.windows, res.windows)


def test_h1_50pct_test_rejects_shallow_sweep_and_keeps_entry():
    cfg = H1FboConfig(**{**CFG.__dict__, "confirm": ConfirmParams(require_50pct_test=True)})
    shallow = run_h1("", cfg, _prep_with_rsi(_m1(PATH_SHALLOW), cfg, lambda i: pd.Series(50.0, index=i)))
    assert len(shallow.trades) == 0
    assert (shallow.windows["state"] == "REJECT_DEPTH").sum() == 1
    deep = run_h1("", cfg, _prep_with_rsi(_m1(PATH_DEEP), cfg, lambda i: pd.Series(50.0, index=i)))
    base = run_h1("", CFG, _prepared(_m1(PATH_DEEP), CFG))
    assert len(deep.trades) == 1
    t = deep.trades.iloc[0]
    assert t["trigger_ts"] == base.trades.iloc[0]["trigger_ts"]     # entry invariato
    assert t["entry"] == base.trades.iloc[0]["entry"]
    assert t["sweep_depth_pct"] == 0.6


def test_h1_rsi_divergence_required():
    cfg = H1FboConfig(**{**CFG.__dict__, "confirm": ConfirmParams(require_rsi_divergence=True)})

    # Il massimo H1 (110) e' nella M15 14:00 (RSI 80). Allo sweep (15:03) l'ultima M15 chiusa
    # e' la 14:45: e' il suo RSI che deve essere piu' basso (LH) per la divergenza.
    def diverging(i):
        return pd.Series([80.0 if ts < pd.Timestamp("2026-01-12 14:45") else 65.0 for ts in i], index=i)

    def confirming(i):         # RSI sale allo sweep: nessuna divergenza
        return pd.Series([80.0 if ts < pd.Timestamp("2026-01-12 14:45") else 85.0 for ts in i], index=i)

    def not_extreme(i):        # livello con RSI 60: strict scarta, loose accetta
        return pd.Series([60.0 if ts < pd.Timestamp("2026-01-12 14:45") else 50.0 for ts in i], index=i)

    m1 = _m1(PATH_DEEP)
    assert len(run_h1("", cfg, _prep_with_rsi(m1, cfg, diverging)).trades) == 1
    r = run_h1("", cfg, _prep_with_rsi(m1, cfg, confirming))
    assert len(r.trades) == 0 and (r.windows["state"] == "REJECT_RSI").sum() == 1
    assert len(run_h1("", cfg, _prep_with_rsi(m1, cfg, not_extreme)).trades) == 0
    loose = H1FboConfig(**{**CFG.__dict__, "confirm": ConfirmParams(require_rsi_divergence=True, rsi_strict=False)})
    assert len(run_h1("", loose, _prep_with_rsi(m1, loose, not_extreme)).trades) == 1


# ---------------------------------------------------------------- motore (b) ORB 9:30

ORB_ = ORB(102.0, 100.0, pd.Timestamp("2026-01-12 14:30"), pd.Timestamp("2026-01-12 14:45"), 1,
           rsi_high=75.0, rsi_low=25.0)
# long: break-in sotto 100, rientro, trigger sopra vwap 103
ROWS_SHALLOW = [(101, 102, 99.5, 99.6), (99.6, 103, 99.5, 102), (104, 106, 103.5, 105)]   # sweep 0.5/2 = 25 %
ROWS_DEEP = [(101, 102, 98.5, 99.6), (99.6, 103, 99.5, 102), (104, 106, 103.5, 105)]      # 1.5/2 = 75 %


def _run(rows, p, rsi_col=None):
    b = bars(rows, vwap=103.0)
    if rsi_col is not None:
        b["rsi"] = rsi_col
    return run_session_setup(b, session(), ORB_, p)


def test_orb_flags_off_identical_to_baseline():
    for rows in (ROWS_SHALLOW, ROWS_DEEP):
        base = _run(rows, SetupParams())
        off = _run(rows, SetupParams(confirm=ConfirmParams()), rsi_col=[40.0, 35.0, 45.0])
        pd.testing.assert_series_equal(pd.Series(vars(base.trigger)), pd.Series(vars(off.trigger)))
        assert base.dirs[LONG].state == off.dirs[LONG].state == "TRIGGERED"


def test_orb_50pct_test():
    p = SetupParams(confirm=ConfirmParams(require_50pct_test=True))
    r = _run(ROWS_SHALLOW, p)
    assert r.trigger is None and r.dirs[LONG].confirm_rejects == 1 and r.dirs[LONG].state == "IDLE"
    r = _run(ROWS_DEEP, p)
    assert r.trigger is not None and r.trigger.entry_close == 105 and r.trigger.sweep_extreme == 98.5


def test_orb_rsi_divergence():
    p = SetupParams(confirm=ConfirmParams(require_rsi_divergence=True))
    # livello RSI 25 (<30); sweep RSI minimo 32 > 25 -> divergenza (HL)
    r = _run(ROWS_DEEP, p, rsi_col=[32.0, 36.0, 45.0])
    assert r.trigger is not None and r.trigger.level_rsi == 25.0 and r.trigger.sweep_rsi == 32.0
    # RSI conferma il nuovo minimo (22 < 25): scartato
    r = _run(ROWS_DEEP, p, rsi_col=[22.0, 30.0, 45.0])
    assert r.trigger is None and r.dirs[LONG].confirm_rejects == 1
    # livello non estremo: strict scarta, loose accetta
    orb_mid = ORB(102.0, 100.0, ORB_.start_utc, ORB_.end_utc, 1, rsi_high=60.0, rsi_low=40.0)
    b = bars(ROWS_DEEP, vwap=103.0)
    b["rsi"] = [45.0, 48.0, 50.0]
    assert run_session_setup(b, session(), orb_mid, p).trigger is None
    loose = SetupParams(confirm=ConfirmParams(require_rsi_divergence=True, rsi_strict=False))
    assert run_session_setup(b, session(), orb_mid, loose).trigger is not None


def test_orb_short_symmetric_both_filters():
    orb = ORB(102.0, 100.0, ORB_.start_utc, ORB_.end_utc, 1, rsi_high=78.0, rsi_low=25.0)
    rows = [(101, 103.6, 100.5, 102.5), (102.5, 102.6, 99, 100.5), (98.5, 98.6, 96, 97)]   # sweep 1.6/2 = 80 %
    p = SetupParams(confirm=ConfirmParams(require_rsi_divergence=True, require_50pct_test=True))
    b = bars(rows, vwap=99.0)
    b["rsi"] = [70.0, 65.0, 50.0]
    r = run_session_setup(b, session(), orb, p)
    assert r.trigger is not None and r.trigger.direction == SHORT and r.trigger.sweep_rsi == 70.0
