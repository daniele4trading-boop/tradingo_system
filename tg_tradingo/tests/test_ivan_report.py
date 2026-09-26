"""Test tools/ivan_report.py su fixture sintetiche (eventi bridge + deal MT5)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

TG_ROOT = Path(__file__).resolve().parents[1]
if str(TG_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(TG_ROOT / "tools"))

import ivan_report as ir

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 9, 23, 14, 0, 0, tzinfo=UTC)
SERVER_OFF = 3  # ore server broker - UTC


def _ts(sec: int) -> str:
    return (T0 + dt.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ev(sec, sid, action, payload=None, text="", outcome="EMITTED", event_type="NEW", message_id=1):
    e = {"ts_utc": _ts(sec), "channel_id": "CH_IVAN", "chat_id": -1, "message_id": message_id,
         "event_type": event_type, "raw_text": text, "outcome": outcome}
    if sid:
        e["signal_id"] = sid
    if action:
        e["action"] = action
        p = {"action": action, "symbol": "XAUUSD", "magic_base": 17000, "timestamp": _ts(sec),
             "channel_id": "CH_IVAN", "signal_id": sid}
        p.update(payload or {})
        e["payload"] = p
    return e


def _open_payload(direction="BUY", entry=4300.0, tps=(4305.0, 4308.0, 4312.0, 4320.0), sl=4290.0, **kw):
    p = {"direction": direction, "entry": entry, "entry_range": None, "tp_levels": list(tps), "sl": sl,
         "trades": 4, "fixed_lot": 0.1, "splits": [0.25] * 4}
    p.update(kw)
    return p


def _deal(ticket, sec, typ, entry, magic, pos, price, profit=0.0, comment="", reason=3, volume=0.1):
    t = T0 + dt.timedelta(seconds=sec, hours=SERVER_OFF)
    return {"ticket": ticket, "order": ticket, "time": int(t.timestamp()), "type": typ, "entry": entry,
            "magic": magic, "position_id": pos, "reason": reason, "volume": volume, "price": price,
            "commission": 0.0, "swap": 0.0, "profit": profit, "symbol": "XAUUSD", "comment": comment}


EVENTS = [
    # setup A: BUY 4300, EDIT dopo 3s dello stesso messaggio (TP3 spostato), BE, TP1 HIT
    _ev(0, "aaaa000001", "OPEN", _open_payload(), "XAUUSD BUY 4300", message_id=10),
    _ev(0, "", "", None, "XAUUSD BUY 4300", outcome="DUPLICATE", event_type="EDIT", message_id=10),
    _ev(3, "aaaa000002", "UPDATE_OPEN", _open_payload(tps=(4305.0, 4308.0, 4314.0, 4320.0)),
        "XAUUSD BUY 4300", event_type="EDIT", message_id=10),
    _ev(600, "aaaa000003", "CHECK_AND_BE", {"tp_index": 1, "be_price": 4300.0}, "Spostiamo SL a BE", message_id=11),
    _ev(700, "", "", None, "TP 1 HIT SQUAD", outcome="IGNORED_PATTERN", message_id=12),
    # setup B: SELL range, chiuso da Ivan
    _ev(3600, "bbbb000001", "OPEN", _open_payload("SELL", None, (4270.0, 4265.0, 4260.0, 4250.0), 4290.0,
                                                  entry_range=[4278.0, 4280.0]), "XAUUSD SELL 4278-4280", message_id=20),
    _ev(4200, "bbbb000002", "CLOSE_ALL_SYMBOL", {}, "CHIUDIAMO ORA", message_id=21),
    # EDIT tardivo di un vecchio messaggio (>10 min, message_id diverso) -> setup autonomo flaggato
    _ev(7200, "cccc000001", "UPDATE_OPEN", _open_payload("SELL", 4316.0, (4310.0, 4308.0, 4305.0, 4280.0), 4327.0),
        "XAUUSD SELL 4316", event_type="EDIT", message_id=5),
    # rumore di altro canale
    {"ts_utc": _ts(10), "channel_id": "CH_IVANBTC", "event_type": "NEW", "raw_text": "BTC", "outcome": "UNPARSED"},
]

# broker "alpha": esegue tutto; broker "beta": setup A solo T1/T2 (not enough money), setup B cancellato
DEALS_ALPHA = [
    _deal(1, 1, 0, 0, 17001, 101, 4300.2, comment="IT-T1-aaaa000001"),
    _deal(2, 1, 0, 0, 17002, 102, 4300.2, comment="IT-T2-aaaa000001"),
    _deal(3, 2, 0, 0, 17003, 103, 4300.3, comment="IT-T3-aaaa000001"),
    _deal(4, 2, 0, 0, 17004, 104, 4300.3, comment="IT-T4-aaaa000001"),
    _deal(5, 500, 1, 1, 17001, 101, 4305.0, 48.0, "[tp 4305.00]", reason=5),
    _deal(6, 900, 1, 1, 17002, 102, 4308.0, 78.0, "[tp 4308.00]", reason=5),
    _deal(7, 1500, 1, 1, 17003, 103, 4312.0, 117.0, "[tp 4312.00]", reason=5),     # TP originale, non aggiornato
    _deal(8, 2000, 1, 1, 17004, 104, 4300.3, 0.0, "[sl 4300.30]", reason=4),      # BE
    _deal(9, 3601, 1, 0, 17001, 201, 4279.0, comment="IT-T1-bbbb000001"),
    _deal(10, 3601, 1, 0, 17002, 202, 4279.0, comment="IT-T2-bbbb000001"),
    _deal(11, 3601, 1, 0, 17003, 203, 4279.1, comment="IT-T3-bbbb000001"),
    _deal(12, 3601, 1, 0, 17004, 204, 4279.1, comment="IT-T4-bbbb000001"),
    _deal(13, 4201, 0, 1, 17001, 201, 4276.0, 30.0, "", reason=3),
    _deal(14, 4202, 0, 1, 17002, 202, 4276.0, 30.0, "", reason=3),
    _deal(15, 4203, 0, 1, 0, 203, 4276.0, 31.0, "", reason=1),                    # chiusura manuale da mobile
    _deal(16, 4300, 0, 1, 17004, 204, 4290.0, -109.0, "[sl 4290.00]", reason=4),  # SL pieno
    _deal(17, 7201, 1, 0, 17001, 301, 4306.0, comment="IT-T1-cccc000001"),
    _deal(18, 7202, 0, 1, 17001, 301, 4306.0, 0.0, "[tp 4310.00]", reason=5),
    # BTC / altro magic: ignorato
    _deal(90, 5, 0, 0, 18001, 901, 110000.0, comment="IB-T1-zzzz") | {"symbol": "BTCUSD"},
]
DEALS_BETA = [
    _deal(1, 2, 0, 0, 17001, 101, 4300.5, comment="IT-T1-aaaa000001", volume=0.05),
    _deal(2, 2, 0, 0, 17002, 102, 4300.5, comment="IT-T2-aaaa000001", volume=0.05),
    _deal(3, 500, 1, 1, 17001, 101, 4305.0, 22.0, "[tp 4305.00]", reason=5, volume=0.05),
    _deal(4, 2000, 1, 1, 17002, 102, 4300.5, 0.0, "[sl 4300.50]", reason=4, volume=0.05),
]
STATS_BETA = (
    "2026.09.23 17:00:01,CH_IVAN,signal_ch_ivan.json,XAUUSD,BUY,EXECUTED_DIRECT,4300.00000,0.00000,0.00000,"
    f"4300.50000,50.0,0.0,1,17001,{_ts(1)}\n"
    "2026.09.23 18:00:01,CH_IVAN,signal_ch_ivan.json,XAUUSD,SELL,CANCELLED_RANGE,0.00000,4278.00000,4280.00000,"
    f"4283.10000,0.0,310.0,0,0,{_ts(3601)}\n"
)
EA_LOG_BETA = (
    f"AB\t0\t16:00:00.100\tTG_TradinGoEA (XAUUSD,M15)\t[TradinGo] signal_ch_ivan.json action=OPEN sym=XAUUSD ts={_ts(0)}\n"
    "AB\t0\t16:00:00.500\tTG_TradinGoEA (XAUUSD,M15)\tCTrade::OrderSend: market buy 0.05 XAUUSD [not enough money]\n"
    "AB\t0\t16:00:00.501\tTG_TradinGoEA (XAUUSD,M15)\t[TradinGo] Open failed XAUUSD BUY err=10019\n"
    "AB\t0\t16:00:00.502\tTG_TradinGoEA (XAUUSD,M15)\t[TradinGo] HEARTBEAT ok\n"
    f"AB\t0\t17:00:00.100\tTG_TradinGoEA (XAUUSD,M15)\t[TradinGo] signal_ch_ivan.json action=OPEN sym=XAUUSD ts={_ts(3600)}\n"
    "AB\t0\t17:00:00.200\tTG_TradinGoEA (XAUUSD,M15)\t[TradinGo] SIGNAL_CANCELLED CH_IVAN XAUUSD SELL range=[4278.00,4280.00] price=4283.10\n"
)


@pytest.fixture
def workspace(tmp_path: Path) -> dict:
    ev_dir = tmp_path / "events"
    ev_dir.mkdir()
    with (ev_dir / "events_20260923.jsonl").open("w", encoding="utf-8") as fh:
        for e in EVENTS:
            fh.write(json.dumps(e) + "\n")
    alpha = tmp_path / "alpha"
    alpha.mkdir()
    (alpha / "deals.json").write_text(json.dumps(DEALS_ALPHA), encoding="utf-8")
    (alpha / "account.json").write_text(json.dumps({"account": {"login": 1, "currency": "USD"}}), encoding="utf-8")
    beta = tmp_path / "beta"
    (beta / "ea_logs").mkdir(parents=True)
    with (beta / "deals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(DEALS_BETA[0].keys()))
        w.writeheader()
        w.writerows(DEALS_BETA)
    (beta / "tradingo_signal_stats.csv").write_text(STATS_BETA, encoding="utf-8")
    (beta / "ea_logs" / "20260923.log").write_text(EA_LOG_BETA, encoding="utf-8")
    (beta / "account.json").write_text(json.dumps({"account": {"login": 2, "currency": "EUR"}}), encoding="utf-8")
    return {"events": ev_dir, "alpha": alpha, "beta": beta, "out": tmp_path / "out"}


def test_build_setups_groups_followups_and_late_edit(workspace):
    events = ir.load_events_jsonl([workspace["events"] / "events_20260923.jsonl"])
    assert all(e.text != "BTC" for e in events)
    setups = ir.build_setups(events)
    assert [s.direction for s in setups] == ["BUY", "SELL", "SELL"]
    a, b, c = setups
    assert a.tps == [4305.0, 4308.0, 4312.0, 4320.0]
    assert a.final_tps == [4305.0, 4308.0, 4314.0, 4320.0]   # EDIT dello stesso messaggio = update
    assert a.n_be == 1 and a.claims == ["TP1"]
    assert {"aaaa000001", "aaaa000002", "aaaa000003"} <= a.sids
    assert b.entry_ref == 4279.0 and b.n_close == 1
    assert c.from_edit and c.entry == 4316.0


def test_legs_outcomes_and_server_offset(workspace):
    events = ir.load_events_jsonl([workspace["events"] / "events_20260923.jsonl"])
    setups = ir.build_setups(events)
    broker = ir.load_broker("alpha", workspace["alpha"], vps_utc_offset=2)
    off = ir.infer_server_offset(broker, {e.sid: e for e in events if e.sid})
    assert off == SERVER_OFF
    legs = ir.build_legs(broker, setups, off)
    by = {(lg.setup_sid, lg.tp_index): lg for lg in legs}
    assert len(legs) == 9
    assert by[("aaaa000001", 1)].outcome == "TP"
    assert by[("aaaa000001", 4)].outcome == "BE_SL"
    assert by[("bbbb000001", 1)].outcome == "EA_CLOSE"
    assert by[("bbbb000001", 3)].outcome == "MANUAL" and "mobile" in by[("bbbb000001", 3)].close_comment
    assert by[("bbbb000001", 4)].outcome == "SL"
    assert by[("aaaa000001", 1)].fill_ts == T0 + dt.timedelta(seconds=1)
    assert by[("bbbb000001", 4)].pnl_oz == pytest.approx(-10.9, abs=1e-6)
    assert by[("aaaa000001", 2)].pnl_ccy == 78.0


def test_missing_reasons_from_stats_and_ea_log(workspace):
    events = ir.load_events_jsonl([workspace["events"] / "events_20260923.jsonl"])
    setups = ir.build_setups(events)
    beta = ir.load_broker("beta", workspace["beta"], vps_utc_offset=2)
    assert beta.currency == "EUR"
    assert not any("HEARTBEAT" in n.text for n in beta.ea_notes)
    a, b, _ = setups
    assert ir.missing_reason(a, 3, beta, None).startswith("not enough money")
    assert ir.missing_reason(b, 1, beta, None).startswith("CANCELLED_RANGE")


def test_run_end_to_end_writes_reports_and_issues(workspace):
    events = ir.load_events_jsonl([workspace["events"] / "events_20260923.jsonl"])
    brokers = [ir.load_broker("alpha", workspace["alpha"], 2), ir.load_broker("beta", workspace["beta"], 2)]
    res = ir.run(events, brokers, dt.date(2026, 9, 23), dt.date(2026, 9, 23), workspace["out"], ["test"],
                 sim={"broker": "alpha", "balance": 10000.0, "from": dt.date(2026, 9, 1), "dd_pct": 7.0, "payout": 0.7})
    out = workspace["out"]
    for name in ("ivan_report.md", "ivan_setups.csv", "ivan_legs.csv", "ivan_stats.csv",
                 "ivan_sim_alpha_daily.csv", "ivan_sim_alpha_equity.csv"):
        assert (out / name).exists(), name
    kinds = {i.kind for i in res["issues"]}
    assert "esecuzione disomogenea" in kinds
    assert "apertura da EDIT di vecchio messaggio" in kinds
    assert "chiusura manuale (magic 0)" in kinds
    assert "TP spostato non recepito" in kinds   # T3 chiuso a 4312 dopo l'update a 4314
    assert "BE non applicato" not in kinds       # lo SL pieno di B e' arrivato senza messaggio BE
    md = (out / "ivan_report.md").read_text(encoding="utf-8")
    assert "not enough money" in md and "CANCELLED_RANGE" in md
    assert "Simulazione conto 10,000 USD" in md
    rows = list(csv.DictReader((out / "ivan_setups.csv").open(encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["alpha_legs"] == "4" and rows[0]["beta_legs"] == "2"
    assert "T3:not enough money" in rows[0]["beta_missing"]
    stats = {s["scope"]: s for s in res["stats"]}
    assert stats["alpha"]["setups_closed"] == 3
    assert stats["alpha"]["net_pnl"] == pytest.approx(48 + 78 + 117 + 0 + 30 + 30 + 31 - 109 + 0)
    assert stats["alpha"]["tp_hit"][1] == (2, 3)
    assert stats["teorico"]["unit"] == "$/oz"
    # teorico: setup A tutti TP tranne T4 BE -> (5+8+14+0.3)... T4 chiuso a BE con prezzo mediano reale 4300.3
    theo_a = {lg.tp_index: lg for lg in res["theoretical"] if lg.setup_sid == "aaaa000001"}
    assert theo_a[1].pnl_oz == pytest.approx(5.0)
    assert theo_a[3].pnl_oz == pytest.approx(14.0)   # TP aggiornato, fill all'entry


def test_simulation_drawdown_and_sizing(workspace):
    events = ir.load_events_jsonl([workspace["events"] / "events_20260923.jsonl"])
    setups = ir.build_setups(events)
    broker = ir.load_broker("alpha", workspace["alpha"], 2)
    legs = ir.build_legs(broker, setups, SERVER_OFF)
    sim = ir.simulate_account(legs, setups, 10000.0, 7.0, 0.7)
    # P&L USD da prezzo: 0.1 lot x 100 oz = 10 USD per $/oz
    expected = 10 * (4.8 + 7.8 + 11.7 + 0.0 + 3.0 + 3.0 + 3.1 - 10.9 + 0.0)
    assert sim["net_pnl"] == pytest.approx(expected)
    assert sim["max_dd"] == pytest.approx(109.0)
    assert sim["worst_daily_dd"] == pytest.approx(109.0)
    assert sim["size_multiplier"] == pytest.approx(700 / 109.0)
    assert sim["suggested_lot"] == pytest.approx(0.1 * 700 / 109.0)
    assert sim["scaled_payout"] == pytest.approx(expected * 700 / 109.0 * 0.7)
    assert list(sim["monthly"]) == ["2026-09"] and sim["monthly"]["2026-09"]["setups"] == 3


def test_drawdown_helper():
    ts = [T0 + dt.timedelta(hours=i) for i in range(5)]
    series = list(zip(ts, [100.0, -50.0, -80.0, 200.0, -30.0]))
    max_dd, worst_daily, day = ir.drawdown(series)
    assert max_dd == pytest.approx(130.0)
    assert worst_daily == pytest.approx(130.0)
    assert day == "2026-09-23"


def test_bridge_log_fallback(tmp_path: Path):
    log = tmp_path / "tradingo_20260922.log"
    log.write_text(
        "2026-09-22 14:14:59,100 [INFO] [CH_IVAN] MSG: XAUUSD BUY 4323 | TP1: 4329 | SL: 4310\n"
        "2026-09-22 14:15:01,695 [INFO] [IVAN] OPEN BUY XAUUSD @ 4323.0 TP=[4329.0, 4332.0, 4335.0, 4340.0] SL=4310.0 lot_factor=1.0\n"
        "2026-09-22 14:15:01,759 [INFO] [CH_IVAN] -> signal_ch_ivan.json | action=OPEN symbol=XAUUSD dir=BUY sid=ddf6d44416\n"
        "2026-09-22 14:15:01,789 [INFO] [CH_IVAN] -> signal_ch_ivan.json | action=OPEN symbol=XAUUSD dir=BUY sid=ddf6d44416\n"
        "2026-09-22 14:24:03,000 [INFO] [CH_IVAN] MSG: Spostiamo SL a BE\n"
        "2026-09-22 14:24:03,100 [INFO] [IVAN] CHECK_AND_BE be_price=4323.0\n"
        "2026-09-22 14:24:03,200 [INFO] [CH_IVAN] -> signal_ch_ivan.json | action=CHECK_AND_BE symbol=XAUUSD dir= sid=5234e7e166\n",
        encoding="utf-8")
    events = ir.load_events_bridge_log([log], log_utc_offset=2)
    emitted = [e for e in events if e.emitted]
    assert [e.action for e in emitted] == ["OPEN", "CHECK_AND_BE"]   # scritture ripetute dedotte per sid
    assert emitted[0].ts == dt.datetime(2026, 9, 22, 12, 15, 1, tzinfo=UTC)
    assert emitted[0].payload["tp_levels"] == [4329.0, 4332.0, 4335.0, 4340.0]
    assert emitted[0].payload["entry"] == 4323.0
    setups = ir.build_setups(events)
    assert len(setups) == 1 and setups[0].n_be == 1


def test_cli_smoke(workspace):
    rc = ir.main(["--events", str(workspace["events"]), "--broker", f"alpha={workspace['alpha']}",
                  "--from", "2026-09-23", "--to", "2026-09-23", "--out", str(workspace["out"])])
    assert rc == 0
    assert (workspace["out"] / "ivan_report.md").exists()
