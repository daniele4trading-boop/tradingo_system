"""Test del modulo ORB+VWAP su barre sintetiche (nessun dato esterno)."""
from __future__ import annotations

from datetime import date, datetime, time

import pandas as pd
import pytest

from orb_vwap_module.backtester import BacktestConfig, config_dict
from orb_vwap_module.orb import compute_orb
from orb_vwap_module.position_manager import RiskParams, TrailParams, simulate, size_lots
from orb_vwap_module.session import SessionSpec, build_sessions, session_bars
from orb_vwap_module.setup_engine import LONG, SHORT, SetupParams, run_session_setup
from orb_vwap_module.vwap import atr, session_vwap, volume_ma

SPEC = SessionSpec()
DAY = date(2026, 1, 12)  # inverno: 09:30 NY = 14:30 UTC


def bars(rows, start="2026-01-12 14:45", freq="5min", spread=0.2, vwap=None, vol_ma=None):
    """rows: lista (open, high, low, close[, volume])."""
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    df = pd.DataFrame([r[:4] for r in rows], columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = [r[4] if len(r) > 4 else 100 for r in rows]
    df["spread"] = spread
    df["vwap"] = vwap if vwap is not None else 0.0
    df["vol_ma"] = vol_ma if vol_ma is not None else 100.0
    df["atr"] = 1.0
    return df


def session(day=DAY, spec=SPEC):
    idx = pd.DatetimeIndex([spec.open_utc(day)])
    return build_sessions(idx, spec)[0]


class FakeORB:
    def __init__(self, high, low):
        self.high, self.low = high, low


# ---------------------------------------------------------------- sessione / DST

def test_ny_open_dst():
    assert SPEC.open_utc(date(2026, 1, 12)) == datetime(2026, 1, 12, 14, 30)   # EST
    assert SPEC.open_utc(date(2026, 7, 15)) == datetime(2026, 7, 15, 13, 30)   # EDT
    assert SPEC.cutoff_utc(date(2026, 3, 9)) == datetime(2026, 3, 10, 2, 0)    # dopo il cambio (8/3/2026)
    assert SPEC.cutoff_utc(date(2026, 3, 6)) == datetime(2026, 3, 7, 3, 0)     # prima del cambio


def test_sessions_only_weekdays_and_trigger_window():
    idx = pd.date_range("2026-01-09 14:00", "2026-01-13 20:00", freq="5min")  # ven → mar
    ss = build_sessions(idx, SPEC)
    assert [s.day for s in ss] == [date(2026, 1, 9), date(2026, 1, 12), date(2026, 1, 13)]
    ss30 = build_sessions(idx, SessionSpec(trigger_window="30min"))
    assert ss30[1].trigger_deadline_utc == datetime(2026, 1, 12, 15, 15)


def test_session_bars_end_before_cutoff():
    df = bars([(1, 1, 1, 1)] * 200, start="2026-01-12 14:00")
    sb = session_bars(df, session(), SPEC)
    assert sb.index[0] == pd.Timestamp("2026-01-12 14:30")
    assert sb.index[-1] == pd.Timestamp("2026-01-13 02:55")   # chiude alle 03:00 UTC = 22:00 NY


# ---------------------------------------------------------------- ORB

def test_orb_m15_single_candle():
    m15 = pd.DataFrame({"open": [1, 2, 3], "high": [10, 20, 30], "low": [5, 15, 25], "close": [1, 2, 3]},
                       index=pd.date_range("2026-01-12 14:15", periods=3, freq="15min"))
    orb = compute_orb(m15, session(), SPEC)
    assert (orb.high, orb.low, orb.n_bars) == (20, 15, 1)
    assert compute_orb(m15.iloc[:1], session(), SPEC) is None


def test_orb_h1_param():
    spec = SessionSpec(orb_tf="1h")
    s = build_sessions(pd.DatetimeIndex([spec.open_utc(DAY)]), spec)[0]
    m15 = pd.DataFrame({"open": 1, "high": [10, 11, 12, 13, 99], "low": [5, 4, 3, 2, 0], "close": 1},
                       index=pd.date_range("2026-01-12 14:30", periods=5, freq="15min"))
    orb = compute_orb(m15, s, spec)
    assert (orb.high, orb.low, orb.n_bars) == (13, 2, 4)


# ---------------------------------------------------------------- VWAP / indicatori

def test_session_vwap_resets_and_ignores_csv_vwap():
    idx = pd.DatetimeIndex(["2026-01-12 14:30", "2026-01-12 14:35", "2026-01-13 14:30"])
    df = pd.DataFrame({"open": 0, "high": [12, 24, 60], "low": [9, 18, 57], "close": [9, 18, 63],
                       "volume": [1, 3, 5], "vwap": 999.0}, index=idx)
    ss = build_sessions(idx, SPEC)
    v = session_vwap(df, ss, SPEC)
    assert v.iloc[0] == pytest.approx(10.0)                   # tp=10
    assert v.iloc[1] == pytest.approx((10 * 1 + 20 * 3) / 4)  # cumulata
    assert v.iloc[2] == pytest.approx(60.0)                   # reset nuova sessione


def test_volume_ma_uses_previous_bars_only():
    df = pd.DataFrame({"volume": [10, 20, 30, 40]})
    m = volume_ma(df, 2)
    assert pd.isna(m.iloc[1]) and m.iloc[2] == 15 and m.iloc[3] == 25


def test_atr_no_lookahead():
    df = bars([(10, 12, 9, 11)] * 30)
    a = atr(df, 14)
    df2 = df.copy()
    df2.iloc[-1, df2.columns.get_loc("high")] = 100
    assert a.iloc[-2] == atr(df2, 14).iloc[-2]   # cambiare l'ultima barra non tocca le precedenti


# ---------------------------------------------------------------- setup engine

ORB = FakeORB(high=110.0, low=100.0)


def run(rows, p=SetupParams(), vwap=None):
    b = bars(rows, vwap=vwap)
    return run_session_setup(b, session(), ORB, p)


def test_long_breakin_reentry_trigger_separate_bars():
    # vwap 103: break-in sotto 100, rientro a 102 (corpo non sopra vwap), trigger 104-105
    r = run([(101, 102, 98, 99), (99, 103, 99, 102), (104, 106, 103.5, 105)], vwap=103.0)
    st = r.dirs[LONG]
    assert st.state == "TRIGGERED"
    assert (st.breakin_ts.minute, st.reentry_ts.minute, st.trigger_ts.minute) == (45, 50, 55)
    assert r.trigger.same_bar is False and r.trigger.entry_close == 105
    assert r.dirs[SHORT].state == "DISARMED"


def test_reentry_and_trigger_same_bar():
    r = run([(101, 102, 98, 99), (104, 106, 103.5, 105)], vwap=103.0)
    assert r.dirs[LONG].state == "TRIGGERED" and r.trigger.same_bar is True
    assert r.trigger.reentry_ts == r.trigger.ts


def test_body_must_be_entirely_beyond_vwap():
    # open 102 < vwap 103 anche se close 105 > vwap: nessun trigger
    r = run([(101, 102, 98, 99), (102, 106, 101, 105)], vwap=103.0)
    assert r.dirs[LONG].state == "EXPIRED" and r.trigger is None


def test_invalidation_after_reentry_blocks_direction_for_session():
    r = run([(101, 102, 98, 99), (99, 103, 99, 102), (102, 102, 97, 98),   # rientro poi richiude sotto → INVALID
             (98, 103, 98, 102), (104, 106, 103.5, 105)], vwap=103.0)     # nuovo rientro+trigger ignorati
    assert r.dirs[LONG].state == "INVALID" and r.trigger is None


def test_invalidation_prevails_on_same_bar():
    # terza barra: corpo 99-101 interamente sopra il vwap (90) ma close sotto ORB_Low → INVALID, non trigger
    r = run([(101, 102, 98, 99), (99, 103, 99, 102), (101, 101, 95, 99)], vwap=[103.0, 103.0, 90.0])
    assert r.dirs[LONG].state == "INVALID"


def test_armed_stays_armed_without_timeout():
    rows = [(101, 102, 98, 99)] + [(99, 99.5, 98, 99)] * 40 + [(104, 106, 103.5, 105)]
    r = run(rows, vwap=103.0)
    assert r.dirs[LONG].state == "TRIGGERED"


def test_short_symmetric():
    r = run([(109, 112, 108, 111), (111, 111, 106, 107), (106, 106.5, 104, 105)], vwap=107.0)
    st = r.dirs[SHORT]
    assert st.state == "TRIGGERED" and r.trigger.direction == SHORT
    assert r.dirs[LONG].state == "DISARMED"


def test_one_trade_per_session_disarms_armed_opposite():
    # short armato (close 111) poi long completo → short DISARMED con end_ts
    r = run([(109, 112, 108, 111), (110, 110, 98, 99), (104, 106, 103.5, 105)], vwap=103.0)
    assert r.trigger.direction == LONG
    assert r.dirs[SHORT].state == "DISARMED" and r.dirs[SHORT].end_ts is not None


def test_expired_at_cutoff():
    r = run([(101, 102, 98, 99), (99, 103, 99, 102)], vwap=103.0)
    assert r.dirs[LONG].state == "EXPIRED" and r.dirs[LONG].end_ts == session().cutoff_utc


def test_volume_filter_on_off():
    rows = [(101, 102, 98, 99), (104, 106, 103.5, 105, 110)]   # vol 110, ma=100, k=1.2 → 120 richiesto
    off = run(rows, SetupParams(volume_filter=False), vwap=103.0)
    on = run(rows, SetupParams(volume_filter=True), vwap=103.0)
    assert off.trigger is not None and off.trigger.vol_filter_on is False
    assert on.trigger is None and on.dirs[LONG].vol_checks_failed == 1
    on2 = run([(101, 102, 98, 99), (104, 106, 103.5, 105, 130)], SetupParams(volume_filter=True), vwap=103.0)
    assert on2.trigger is not None and on2.trigger.vol_filter_passed is True


def test_immediate_trigger_touch_vwap_intrabar():
    p = SetupParams(immediate_trigger=True)
    # break-in (close 99, vwap 103); barra dopo tocca 103 → ingresso a 103, non al close
    r = run([(101, 102, 98, 99), (99.5, 104, 99, 101)], p, vwap=103.0)
    t = r.trigger
    assert t is not None and t.same_bar and t.intrabar and t.entry_close == 103.0 and t.bar_close == 101
    # apertura già sopra il vwap → ingresso all'open (peggiore)
    r2 = run([(101, 102, 98, 99), (104, 106, 103.5, 105)], p, vwap=103.0)
    assert r2.trigger.entry_close == 104.0


def test_immediate_trigger_fails_then_rearms():
    p = SetupParams(immediate_trigger=True)
    # break-in, barra dopo non arriva al vwap → FAILED; nuovo break-in → tocco sulla successiva
    r = run([(101, 102, 98, 99), (99, 102.5, 99, 102), (102, 103, 98, 99), (99, 104, 99, 101)], p, vwap=103.0)
    assert r.trigger is not None and r.trigger.breakin_ts == bars([(0, 0, 0, 0)] * 3).index[2]
    assert r.dirs[LONG].failed_attempts == 1
    # vwap sotto ORB_Low → toccarlo non è un rientro: FAILED
    r2 = run([(101, 102, 97, 98), (98, 100, 96, 99)], p, vwap=97.0)
    assert r2.trigger is None and r2.dirs[LONG].failed_attempts == 1 and r2.dirs[LONG].state == "IDLE"


def test_min_range_bars_requires_lateral_phase():
    inside = (104, 106, 103, 105)
    p = SetupParams(min_range_bars=3)
    # solo 2 barre dentro prima del break-in → scartato; poi 3 dentro → armato e trigger
    r = run([inside, inside, (101, 102, 98, 99), inside, inside, inside, (101, 102, 98, 99),
             (104, 106, 103.5, 105)], p, vwap=103.0)
    assert r.dirs[LONG].range_rejects == 1 and r.trigger is not None
    assert r.trigger.breakin_ts == bars([(0, 0, 0, 0)] * 7).index[6]


def test_bars_before_orb_end_ignored():
    b = bars([(101, 102, 98, 99), (104, 106, 103.5, 105)], start="2026-01-12 14:30", vwap=103.0)
    r = run_session_setup(b, session(), ORB, SetupParams())
    assert r.dirs[LONG].state == "IDLE"   # 14:30 e 14:35 sono dentro l'ORB


# ---------------------------------------------------------------- sizing / posizione

RP = RiskParams()


def test_size_lots():
    assert size_lots(1000.0, 10.0, RP) == 1.0          # 1000 / (10 × 100)
    assert size_lots(500.0, 10.0, RP) == 0.5
    assert size_lots(1000.0, 3.0, RP) == 3.33          # floor allo step
    assert size_lots(1.0, 100.0, RP) == 0.01           # min lot


def test_variant_a_tp_and_spread_both_sides():
    after = bars([(105.2, 116, 105, 115)], start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "A", RP)
    assert tr.entry_price == pytest.approx(105.2)
    assert tr.risk_dist == pytest.approx(5.2) and tr.tp1 == pytest.approx(115.6)
    assert tr.legs[0].reason == "TP1" and tr.legs[0].exit_price == pytest.approx(115.4)
    assert tr.legs[0].lots == 1.92                       # 1000 / (5.2×100) = 1.923 → 1.92
    assert tr.pnl == pytest.approx((115.4 - 105.2) * 1.92 * 100)
    assert tr.outcome == "win" and tr.rr == pytest.approx(tr.pnl / 1000)


def test_stop_prevails_over_tp_same_bar():
    after = bars([(105, 120, 99, 118)], start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "A", RP)
    assert tr.legs[0].reason == "SL" and tr.legs[0].exit_price == pytest.approx(99.8)
    assert tr.outcome == "loss"
    assert tr.pnl == pytest.approx((99.8 - 105.2) * 1.92 * 100) and tr.rr < -1.0   # spread in uscita


def test_gap_through_stop_fills_at_open():
    after = bars([(97, 98, 95, 96)], start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "A", RP)
    assert tr.legs[0].exit_price == pytest.approx(96.8)


def test_cutoff_closes_at_last_close():
    after = bars([(105, 106, 104, 105.5), (105.5, 107, 105, 106)], start="2026-01-12 15:00")
    tr = simulate(SHORT, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "A", RP)
    assert tr.legs[0].reason == "CUTOFF" and tr.legs[0].exit_ts == pd.Timestamp("2026-01-12 15:05")
    assert tr.legs[0].exit_price == pytest.approx(106.2)   # short: close + spread
    assert tr.entry_price == pytest.approx(104.8)          # short: close − spread


def test_variant_b_be_then_trailing_never_backwards():
    # entry 105.2, SL 100, risk 5.2, TP1 115.6. Barra 1 prende TP1 → gamba 2 a BE + trailing 2×ATR(=1)
    rows = [(105.2, 116, 105, 115),    # TP1; extreme 116 → stop max(105.2, 114) = 114
            (115, 118, 114.5, 117),    # extreme 118 → stop 116
            (117, 117.5, 115, 115.5),  # nessun arretramento: stop resta 116, low 115 tocca → TRAIL a 116
            (115, 115, 110, 111)]
    after = bars(rows, start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "B",
                  RP, TrailParams(14, 2.0))
    l1, l2 = tr.legs
    assert l1.reason == "TP1" and l1.lots == 0.96 and l2.lots == 0.96     # 500 $ per gamba
    assert l2.reason == "TRAIL" and l2.exit_ts == pd.Timestamp("2026-01-12 15:10")
    assert l2.exit_price == pytest.approx(116 - 0.2)


def test_variant_b_be_hit():
    rows = [(105.2, 116, 105, 115), (115, 115.5, 104, 104.5)]   # dopo TP1 stop=max(BE, 116-2)=114 → TRAIL
    after = bars(rows, start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "B",
                  RP, TrailParams(14, 2.0))
    assert tr.legs[1].reason == "TRAIL" and tr.legs[1].exit_price == pytest.approx(113.8)
    # con ATR enorme il trailing non supera BE → esce a BE
    after2 = after.copy()
    after2["atr"] = 100.0
    tr2 = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after2, 100_000, "B",
                   RP, TrailParams(14, 2.0))
    assert tr2.legs[1].reason == "BE" and tr2.legs[1].exit_price == pytest.approx(105.0)
    assert tr2.outcome == "win"   # gamba 1 in profitto


def test_variant_b_both_legs_stopped_before_tp1():
    after = bars([(105, 106, 99, 100)], start="2026-01-12 15:00")
    tr = simulate(LONG, pd.Timestamp("2026-01-12 14:55"), 105.0, 0.2, 100.0, 110.0, after, 100_000, "B",
                  RP, TrailParams())
    assert [l.reason for l in tr.legs] == ["SL", "SL"]
    assert tr.outcome == "loss" and tr.pnl == pytest.approx((99.8 - 105.2) * 0.96 * 100 * 2)


def test_config_dict_serializable():
    d = config_dict(BacktestConfig())
    assert d["session"]["open_time"] == "09:30:00" and d["trail"]["atr_period"] == 14


# ---------------------------------------------------------------- ORB rolling / vwap_cross

def test_rolling_windows_h1_and_h4():
    from orb_vwap_module.session import build_windows
    spec = SessionSpec(cutoff_time=time(12, 0), orb_tf="30min", rolling_tf="1h")
    ws = build_windows([session(spec=spec)], spec)
    # la finestra 16:30 avrebbe l'ORB che termina al cutoff (17:00): esclusa
    assert [w.open_utc for w in ws] == [datetime(2026, 1, 12, 14, 30), datetime(2026, 1, 12, 15, 30)]
    assert ws[0].orb_end_utc == datetime(2026, 1, 12, 15, 0)
    assert ws[-1].cutoff_utc == datetime(2026, 1, 12, 16, 30)
    assert all(w.sim_end_utc == datetime(2026, 1, 12, 17, 0) for w in ws)
    spec4 = SessionSpec(cutoff_time=time(16, 0), orb_tf="2h", rolling_tf="4h")
    ws4 = build_windows([session(spec=spec4)], spec4)
    assert [(w.open_utc.hour, w.orb_end_utc.hour) for w in ws4] == [(14, 16), (18, 20)]


def test_rolling_setup_expires_at_window_end_but_bars_run_to_day_cutoff():
    spec = SessionSpec(cutoff_time=time(12, 0), orb_tf="30min", rolling_tf="1h")
    from orb_vwap_module.session import build_windows
    w = build_windows([session(spec=spec)], spec)[1]              # 15:30-16:30 UTC
    df = bars([(104, 106, 103, 105)] * 40, start="2026-01-12 15:30", vwap=103.0)
    b = session_bars(df, w, spec)
    assert b.index[-1] == pd.Timestamp("2026-01-12 16:55")          # fino al cutoff giornaliero
    rows = [(104, 106, 103, 105)] * 6 + [(101, 102, 98, 99)] + [(104, 106, 103, 105)] * 33
    df = bars(rows, start="2026-01-12 15:30", vwap=103.0)
    r = run_session_setup(df, w, FakeORB(110, 100), SetupParams())
    assert r.dirs[LONG].state == "TRIGGERED"
    rows = [(104, 106, 103, 105)] * 6 + [(101, 102, 98, 99)] + [(99, 99.5, 98, 99)] * 33
    df = bars(rows, start="2026-01-12 15:30", vwap=103.0)
    r = run_session_setup(df, w, FakeORB(110, 100), SetupParams())
    assert r.dirs[LONG].state == "EXPIRED" and r.dirs[LONG].end_ts == pd.Timestamp("2026-01-12 16:30")


def test_vwap_cross_mode_long_short_volume_and_sl():
    p = SetupParams(mode="vwap_cross", sl_lookback=3)
    # ORB (3 barre) → poi close sotto vwap → close sopra vwap = cross long
    rows = [(100, 101, 99, 100)] * 3 + [(100, 101, 99, 99.5), (99.5, 102, 97, 101.5)]
    df = bars(rows, vwap=[100.5] * 5)
    r = run_session_setup(df, session(), FakeORB(101, 99), p)
    t = r.trigger
    assert t is not None and t.direction == LONG and t.entry_close == 101.5 and t.sl_level == 97
    assert r.dirs[SHORT].state == "DISARMED"
    # short speculare
    rows = [(100, 102, 99, 101)] * 3 + [(101, 102, 100.6, 101), (101, 103, 98.5, 99.5)]
    r = run_session_setup(bars(rows, vwap=[100.5] * 5), session(), FakeORB(101, 99), p)
    assert r.trigger.direction == SHORT and r.trigger.sl_level == 103
    # nessun attraversamento (resta sopra) → niente trade
    rows = [(100, 102, 99, 101)] * 3 + [(101, 102, 100.6, 101), (101, 103, 100.7, 102)]
    r = run_session_setup(bars(rows, vwap=[100.5] * 5), session(), FakeORB(101, 99), p)
    assert r.trigger is None
    # filtro volume: cross con volume basso scartato, il successivo passa
    pv = SetupParams(mode="vwap_cross", volume_filter=True)
    rows = [(100, 101, 99, 100)] * 3 + [(100, 101, 99, 99.5), (99.5, 102, 99, 101.5, 50),
                                        (101.5, 102, 99, 99.5), (99.5, 102, 99, 101.5, 200)]
    r = run_session_setup(bars(rows, vwap=[100.5] * 7), session(), FakeORB(101, 99), pv)
    assert r.trigger.ts == pd.Timestamp("2026-01-12 15:15") and r.dirs[LONG].vol_checks_failed == 1
