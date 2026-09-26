"""dx_executor: contratto JSON del bridge → operazioni DXtrade (paper e live mock)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dx_executor.config import config_from_dict
from dx_executor.dxclient import AccountMetrics, DXTradeError, Instrument, Quote
from dx_executor.executor import Executor
from dx_executor.journal import Journal


class MockClient:
    def __init__(self, bid: float = 4300.0, ask: float = 4300.3, reject_protection: bool = False):
        self.bid, self.ask = bid, ask
        self.reject_protection = reject_protection
        self._positions: list[dict] = []
        self._orders: list[dict] = []
        self.calls: list[tuple] = []
        self.equity = 10000.0
        self._n = 0

    def set_price(self, bid: float, ask: float | None = None) -> None:
        self.bid, self.ask = bid, ask if ask is not None else bid + 0.3

    def quote(self, symbol: str) -> Quote:
        return Quote(symbol, self.bid, self.ask, datetime.now(timezone.utc))

    def instrument(self, symbol: str) -> Instrument:
        return Instrument(symbol, 0.01, 0.01, 1.0)

    def metrics(self) -> AccountMetrics:
        return AccountMetrics(self.equity, self.equity, 0.0, len(self._positions), len(self._orders))

    def positions(self) -> list[dict]:
        return list(self._positions)

    def orders(self) -> list[dict]:
        return list(self._orders)

    def place_market_order(self, symbol, side, quantity, client_order_id):
        self.calls.append(("MARKET", side, quantity, client_order_id))
        self._n += 1
        self._positions.append({
            "positionCode": f"P{self._n}", "symbol": symbol, "side": side,
            "openPrice": self.ask if side == "BUY" else self.bid, "quantity": quantity,
        })
        return {"orderCode": client_order_id}

    def place_protective_order(self, symbol, side, quantity, price, client_order_id, position_code, kind):
        self.calls.append((kind, side, quantity, price, position_code))
        if self.reject_protection:
            raise DXTradeError("errorCode 33: legs not allowed")
        self._orders.append({"orderCode": client_order_id, "positionCode": position_code, "kind": kind})
        return {"orderCode": client_order_id}

    def cancel_order(self, order_code):
        self.calls.append(("CANCEL", order_code))
        self._orders = [o for o in self._orders if o["orderCode"] != order_code]

    def close_position(self, symbol, side, quantity, client_order_id, position_code):
        self.calls.append(("CLOSE", side, quantity, position_code))
        self._positions = [p for p in self._positions if p["positionCode"] != position_code]
        return {"orderCode": client_order_id}


def make_cfg(tmp_path: Path, **execution) -> dict:
    return {
        "broker": {"account": "default:1"},
        "execution": {"mode": "paper", **execution},
        "risk": {"initial_balance": 10000.0, "heartbeat_max_age_sec": 0},
        "paths": {"signals_dir": str(tmp_path)},
    }


@pytest.fixture
def paper(tmp_path):
    cfg = config_from_dict(make_cfg(tmp_path))
    client = MockClient()
    ex = Executor(cfg, client, Journal(cfg.state_dir))
    return ex, client


@pytest.fixture
def live(tmp_path):
    raw = make_cfg(tmp_path, mode="live", live_orders_ack="I_AUTHORIZE_LIVE_ORDERS")
    cfg = config_from_dict(raw)
    client = MockClient()
    ex = Executor(cfg, client, Journal(cfg.state_dir))
    return ex, client


def open_payload(**over) -> dict:
    base = {
        "action": "OPEN", "direction": "BUY", "symbol": "XAUUSD", "entry": 4300.0,
        "entry_range": [4297.0, 4300.0], "sl": 4290.0, "tp_levels": [4303.0, 4306.0, 4310.0, 4320.0],
        "trades": 4, "fixed_lot": 0.02, "magic_base": 17000, "channel_id": "CH_IVAN",
        "timestamp": "2026-09-22T10:00:00Z", "message_id": 1001, "event_type": "new", "signal_id": "sig-1",
    }
    base.update(over)
    return base


def events(ex: Executor) -> list[dict]:
    return [json.loads(line) for line in ex.journal.events_path.read_text().splitlines()]


# ---------------------------------------------------------------- apertura

def test_open_paper_creates_four_legs_min_quantity(paper):
    ex, client = paper
    ex.process_signal(open_payload())
    legs = ex.state.open_legs("XAU")
    assert len(legs) == 4
    assert [leg.tp for _, leg in legs] == [4303.0, 4306.0, 4310.0, 4320.0]
    assert all(leg.quantity == 0.01 and leg.sl == 4290.0 and leg.fill == 4300.3 for _, leg in legs)
    assert client.calls == []  # paper: nessun ordine al broker


def test_open_cancelled_when_price_beyond_tolerance(paper):
    ex, client = paper
    client.set_price(4302.5)  # ask 4302.8 → 2.8 $ oltre la zona [4297, 4300]
    ex.process_signal(open_payload())
    assert ex.state.open_legs() == []
    assert any(e["event"] == "SIGNAL_CANCELLED" and e["reason"] == "off_market" for e in events(ex))


def test_open_within_tolerance_executes(paper):
    ex, client = paper
    client.set_price(4301.5)  # ask 4301.8 → 1.8 $ oltre, entro 2.0
    ex.process_signal(open_payload())
    assert len(ex.state.open_legs()) == 4


def test_duplicate_payload_and_same_signal_ignored(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    ex.process_signal(open_payload())  # stesso timestamp/message_id
    ex.process_signal(open_payload(timestamp="2026-09-22T10:00:05Z", message_id=1002))  # stesso signal_id
    assert len(ex.state.open_legs()) == 4


def test_btc_symbol_ignored(paper):
    ex, _ = paper
    ex.process_signal(open_payload(symbol="BTCUSD"))
    assert ex.state.open_legs() == []
    assert events(ex)[-1]["event"] == "SYMBOL_IGNORED"


def test_level_out_of_scale_dropped(paper):
    ex, _ = paper
    ex.process_signal(open_payload(tp_levels=[4303.0, 43060.0], sl=4290.0))
    legs = ex.state.open_legs()
    assert len(legs) == 1 and legs[0][1].tp == 4303.0


def test_killswitch_blocks_open(paper, tmp_path):
    ex, _ = paper
    (tmp_path / "dx_executor.killswitch").write_text("")
    ex.process_signal(open_payload())
    assert ex.state.open_legs() == []
    assert any(e["event"] == "OPEN_BLOCKED" and e["reason"] == "killswitch" for e in events(ex))


def test_stale_heartbeat_blocks_open(tmp_path):
    raw = make_cfg(tmp_path)
    raw["risk"]["heartbeat_max_age_sec"] = 180
    cfg = config_from_dict(raw)
    (tmp_path / "tradingo_heartbeat.json").write_text(json.dumps({"ts_utc": "2020-01-01T00:00:00Z"}))
    ex = Executor(cfg, MockClient(), Journal(cfg.state_dir))
    ex.process_signal(open_payload())
    assert ex.state.open_legs() == []


# ------------------------------------------------------------ gestione soft

def test_paper_tp_and_sl_hits(paper):
    ex, client = paper
    ex.process_signal(open_payload())
    client.set_price(4303.2)  # bid ≥ TP1
    ex.manage()
    assert len(ex.state.open_legs()) == 3
    client.set_price(4289.5)
    ex.manage()
    assert ex.state.open_legs() == []
    closed = [leg for s in ex.state.setups for leg in s.legs]
    assert closed[0].close_reason == "TP" and closed[0].pnl == pytest.approx((4303.0 - 4300.3) * 0.01)
    assert all(leg.close_reason == "SL" for leg in closed[1:])
    assert ex.state.paper_balance == pytest.approx(10000 + 0.027 - 3 * 0.103, abs=1e-6)


# --------------------------------------------------------------- modifiche

def test_update_sl_never_widens_beyond_factor_and_follows_tighter(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    ex.process_signal({"action": "UPDATE_SL", "symbol": "XAUUSD", "new_sl": 4250.0,
                       "timestamp": "t2", "message_id": 2, "signal_id": "sig-1"})
    # rischio 10.3 $ × 2 = 20.6 → cap a 4279.7
    assert all(leg.sl == pytest.approx(4279.7) for _, leg in ex.state.open_legs())
    ex.process_signal({"action": "UPDATE_SL", "symbol": "XAUUSD", "new_sl": 4295.0,
                       "timestamp": "t3", "message_id": 3})
    assert all(leg.sl == 4295.0 for _, leg in ex.state.open_legs())


def test_update_tp_with_index_moves_only_that_leg(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    ex.process_signal({"action": "UPDATE_TP", "symbol": "XAUUSD", "new_tp": 4315.0, "tp_index": 4,
                       "timestamp": "t2", "message_id": 2})
    tps = {leg.tp_index: leg.tp for _, leg in ex.state.open_legs()}
    assert tps == {1: 4303.0, 2: 4306.0, 3: 4310.0, 4: 4315.0}


def test_update_tp_adverse_skipped(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    ex.process_signal({"action": "UPDATE_TP", "symbol": "XAUUSD", "new_tp": 4299.0,
                       "timestamp": "t2", "message_id": 2})
    assert all(leg.tp > 4300 for _, leg in ex.state.open_legs())


def test_be_uses_most_protective_between_fill_and_signal_entry(paper):
    ex, client = paper
    ex.process_signal(open_payload())  # BUY fill 4300.3, entry segnale 4300
    client.set_price(4304.0)
    ex.process_signal({"action": "CHECK_AND_BE", "symbol": "XAUUSD", "be_price": 4300.0,
                       "timestamp": "t2", "message_id": 2})
    assert all(leg.sl == 4300.3 for _, leg in ex.state.open_legs())


def test_be_sell_with_better_fill_goes_to_fill(paper):
    ex, client = paper
    client.set_price(4367.0, 4367.3)
    ex.process_signal(open_payload(direction="SELL", entry=4370.0, entry_range=[4370.0, 4372.0],
                                   sl=4383.0, tp_levels=[4360.0, 4355.0, 4350.0, 4340.0]))
    client.set_price(4360.5, 4360.8)
    ex.process_signal({"action": "CHECK_AND_BE", "symbol": "XAUUSD", "be_price": 4370.0,
                       "timestamp": "t2", "message_id": 2})
    assert all(leg.sl == 4367.0 for _, leg in ex.state.open_legs())


def test_be_pending_until_legal_then_applied(paper):
    ex, client = paper
    ex.process_signal(open_payload())
    client.set_price(4299.0)  # in perdita: BE non legale
    ex.process_signal({"action": "CHECK_AND_BE", "symbol": "XAUUSD", "be_price": 4300.0,
                       "timestamp": "t2", "message_id": 2})
    assert all(leg.sl == 4290.0 and leg.pending_be == 4300.3 for _, leg in ex.state.open_legs())
    client.set_price(4302.0)
    ex.manage()
    assert all(leg.sl == 4300.3 and leg.pending_be == 0 for _, leg in ex.state.open_legs())


def test_update_open_applies_levels_without_new_legs(paper):
    ex, _ = paper
    ex.process_signal(open_payload(tp_levels=[4303.0, 4306.0]))
    ex.process_signal(open_payload(action="UPDATE_OPEN", sl=4292.0, tp_levels=[4304.0, 4307.0, 4310.0],
                                   timestamp="t2", message_id=2))
    legs = ex.state.open_legs()
    assert len(legs) == 2
    assert [(leg.sl, leg.tp) for _, leg in legs] == [(4292.0, 4304.0), (4292.0, 4307.0)]


# ---------------------------------------------------------------- chiusure

def test_close_selective_all_but_newest_closes_only_reentry(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    first = ex.state.setups[0]
    first.opened_at = "2026-09-22T09:00:00Z"
    ex.process_signal(open_payload(signal_id="sig-2", timestamp="t2", message_id=2, tp_levels=[4305.0]))
    ex.process_signal({"action": "CLOSE_SELECTIVE", "keep": "ALL_BUT_NEWEST", "symbol": "XAUUSD",
                       "timestamp": "t3", "message_id": 3})
    assert len(first.open_legs) == 4
    assert ex.state.setups[1].open_legs == []


def test_check_and_close_tp_and_close_all(paper):
    ex, _ = paper
    ex.process_signal(open_payload())
    ex.process_signal({"action": "CHECK_AND_CLOSE_TP", "symbol": "XAUUSD", "tp_index": 2,
                       "timestamp": "t2", "message_id": 2})
    assert [leg.tp_index for _, leg in ex.state.open_legs()] == [1, 3, 4]
    ex.process_signal({"action": "CLOSE_ALL_SYMBOL", "symbol": "XAUUSD", "timestamp": "t3", "message_id": 3})
    assert ex.state.open_legs() == []


# ------------------------------------------------------------ live (mock)

def test_live_requires_ack(tmp_path):
    cfg = config_from_dict(make_cfg(tmp_path, mode="live"))
    with pytest.raises(RuntimeError):
        Executor(cfg, MockClient(), Journal(cfg.state_dir))


def test_live_open_places_market_and_protective_orders(live):
    ex, client = live
    ex.process_signal(open_payload())
    markets = [c for c in client.calls if c[0] == "MARKET"]
    assert len(markets) == 4 and all(c[1] == "BUY" and c[2] == 0.01 for c in markets)
    assert len([c for c in client.calls if c[0] == "SL"]) == 4
    assert len([c for c in client.calls if c[0] == "TP"]) == 4
    legs = ex.state.open_legs()
    assert all(not leg.soft and leg.position_code.startswith("P") for _, leg in legs)


def test_live_falls_back_to_soft_stop_when_protection_rejected(tmp_path):
    raw = make_cfg(tmp_path, mode="live", live_orders_ack="I_AUTHORIZE_LIVE_ORDERS")
    cfg = config_from_dict(raw)
    client = MockClient(reject_protection=True)
    ex = Executor(cfg, client, Journal(cfg.state_dir))
    ex.process_signal(open_payload())
    assert all(leg.soft for _, leg in ex.state.open_legs())
    client.set_price(4289.0)
    ex.manage()
    assert ex.state.open_legs() == []
    assert len([c for c in client.calls if c[0] == "CLOSE"]) == 4


def test_live_reconciles_broker_side_close(live):
    ex, client = live
    ex.process_signal(open_payload())
    leg = ex.state.open_legs()[0][1]
    # il broker esegue il TP: posizione e ordine TP spariscono
    client._positions = [p for p in client._positions if p["positionCode"] != leg.position_code]
    client._orders = [o for o in client._orders if o["orderCode"] != leg.tp_order]
    ex.manage()
    assert leg.status == "CLOSED" and leg.close_reason == "TP"
    assert ("CANCEL", leg.sl_order) in client.calls


# --------------------------------------------------------- file / persistenza

def test_tick_consumes_signal_file_and_persists_state(paper, tmp_path):
    ex, _ = paper
    (tmp_path / "signal_ch_ivan.json").write_text(json.dumps(open_payload()))
    ex.tick()
    assert json.loads((tmp_path / "signal_ch_ivan.json").read_text()) == {"action": "NONE"}
    reloaded = Journal(ex.cfg.state_dir)
    assert len(reloaded.state.open_legs()) == 4
    status = json.loads((ex.cfg.state_dir / "dx_executor_status.json").read_text())
    assert status["mode"] == "paper" and status["open_legs"] == 4
