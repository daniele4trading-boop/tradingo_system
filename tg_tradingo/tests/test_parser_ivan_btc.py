"""Parser ``ivan_btc`` (IvanTrades - BTC): solo BTCUSD/XAGUSD, stato separato dal VIP oro.

Messaggi reali in ``docs/fixtures/ivan_btc_samples.txt``.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest import mock

from bridge_core import BridgeState, apply_lot_rules, payload_for_ea, validate_signal
from tradingo_bridge import (
    IVAN_REENTRY_DEDUP_TTL_SEC,
    _ivan_btc_price,
    parser_ivan_btc,
    parser_ivan_vip,
)

CH_BTC = {
    "id": "CH_IVANBTC",
    "magic_base": 18000,
    "execution": {
        "fixed_lot_single": 0.01,
        "fixed_lot_per_tp": 0.01,
        "tp_levels_expected": 1,
        "max_level_deviation_pct": 60,
    },
}
CH_IVAN = {
    "id": "CH_IVAN",
    "magic_base": 17000,
    "execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10},
}

BTC_SETUP = (
    "BTCUSD SELL 78700\n\n"
    "TP1: 74000\nTP2: 70000\nTP3: 65000\nTP4: 55000\n\n"
    "🛑 SL: 81500\n\nSize medio piccole"
)
BTC_SETUP_OLD = (
    "BTCUSD BUY 66900\n\n"
    "TP 1 68000\nTP 2 69000\nTP 3 70000\nTP 4 74000\n\n"
    "SL @ 64500"
)
XAG_SETUP = "Proviamo Long su XAGUSD 64.365\n\n TP: OPEN \n\nSL 63.000"
XAG_REENTRY = "XAGUSD rientriamo BUY 64.500\nSize medio basse\n\nSL: 62.700"
GOLD_SETUP = (
    "XAUUSD SELL 4060\n\nTP1: 4055\nTP2: 4052\nTP3: 4048\nTP4: 4043\n\nSL: 4065"
)


def _ok(sig: dict) -> dict:
    sig = dict(sig)
    apply_lot_rules(sig, CH_BTC)
    ok, why = validate_signal(sig)
    assert ok, why
    return sig


class TestPrices:
    def test_btc_thousands_separator(self):
        assert _ivan_btc_price("78.800", "BTCUSD") == 78800.0
        assert _ivan_btc_price("78,800", "BTCUSD") == 78800.0
        assert _ivan_btc_price("78700", "BTCUSD") == 78700.0

    def test_xag_decimals(self):
        assert _ivan_btc_price("64.365", "XAGUSD") == 64.365
        assert _ivan_btc_price("63.000", "XAGUSD") == 63.0
        assert _ivan_btc_price("64,5", "XAGUSD") == 64.5

    def test_out_of_range_rejected(self):
        assert _ivan_btc_price("600", "XAGUSD") is None
        assert _ivan_btc_price("4.060", "BTCUSD") is None
        assert _ivan_btc_price("4060", "BTCUSD") is None


class TestSetups:
    def test_btc_four_tp_setup(self, bridge_state: BridgeState):
        sig = _ok(parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state))
        assert sig["action"] == "OPEN"
        assert sig["symbol"] == "BTCUSD"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 78700.0
        assert sig["tp_levels"] == [74000.0, 70000.0, 65000.0, 55000.0]
        assert sig["sl"] == 81500.0
        assert sig["trades"] == 4
        assert sig["fixed_lot"] == 0.01
        assert sig["max_level_deviation_pct"] == 60.0
        assert "lot_factor" not in sig
        assert bridge_state.ivan_btc_trades["BTCUSD"]["entry"] == 78700.0
        assert "XAGUSD" not in bridge_state.ivan_btc_trades

    def test_btc_old_tp_form(self, bridge_state: BridgeState):
        sig = _ok(parser_ivan_btc(BTC_SETUP_OLD, CH_BTC, bridge_state))
        assert sig["direction"] == "BUY"
        assert sig["tp_levels"] == [68000.0, 69000.0, 70000.0, 74000.0]
        assert sig["sl"] == 64500.0

    def test_xag_tp_open_single_position(self, bridge_state: BridgeState):
        sig = _ok(parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state))
        assert sig["symbol"] == "XAGUSD"
        assert sig["direction"] == "BUY"
        assert sig["entry"] == 64.365
        assert sig["sl"] == 63.0
        assert sig["tp_levels"] == []
        assert sig["trades"] == 1
        assert sig["fixed_lot"] == 0.01
        assert "XAGUSD" in payload_for_ea(sig)["symbol"]

    def test_xag_reentry_with_sl_only(self, bridge_state: BridgeState):
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        sig = _ok(parser_ivan_btc(XAG_REENTRY, CH_BTC, bridge_state))
        assert sig["symbol"] == "XAGUSD"
        assert sig["entry"] == 64.5
        assert sig["sl"] == 62.7
        assert sig["allow_stack"] is True

    def test_incoherent_levels_rejected(self, bridge_state: BridgeState):
        bad = "BTCUSD SELL 78700\nTP1: 80000\nSL: 75000"
        assert parser_ivan_btc(bad, CH_BTC, bridge_state) is None

    def test_edit_same_setup_not_reemitted(self, bridge_state: BridgeState):
        assert parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state) is not None
        assert parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state) is None

    def test_reduced_size_never_below_min_lot(self, bridge_state: BridgeState):
        parser_ivan_btc(
            "BTCUSD SELL 64000\nTP1: 60000\nTP2: 57000\nTP3: 53000\nTP4: 42000\n🛑 SL: 67000",
            CH_BTC, bridge_state,
        )
        sig = _ok(parser_ivan_btc(
            "Aggiungiamo una piccola entry qui a 65250", CH_BTC, bridge_state,
        ))
        assert sig["symbol"] == "BTCUSD"
        assert sig["entry"] == 65250.0
        assert sig["fixed_lot"] == 0.01
        assert sig["allow_stack"] is True


class TestSymbolIsolation:
    def test_gold_ignored_in_btc_channel(self, bridge_state: BridgeState):
        assert parser_ivan_btc(GOLD_SETUP, CH_BTC, bridge_state) is None
        assert bridge_state.ivan_btc_trades == {}
        assert bridge_state.ivan_last_trade is None

    def test_sol_and_narrative_ignored(self, bridge_state: BridgeState):
        for txt in (
            "SOL SELL 180\nTP1: 170\nSL: 190",
            "Sia xau che BTC scopo porca puttanaaaaa",
            "Menomale che c'è XAG",
            "Per i nuovi entrati nella sala BTC",
            "Solo per chi è entrato da poco e non è ancora dentro il sell",
            "BTC SELL NOW",
            "A breve potremmo rientrare",
            "E poi rientriamo cattivi non appena ci dà conferme",
            "Su btc abbiamo preso ieri sera una super Reentry",
        ):
            assert parser_ivan_btc(txt, CH_BTC, bridge_state) is None, txt

    def test_btc_setup_in_vip_channel_is_advertising(self, bridge_state: BridgeState):
        parser_ivan_vip(GOLD_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(BTC_SETUP, CH_IVAN, bridge_state) is None
        assert parser_ivan_vip(XAG_SETUP, CH_IVAN, bridge_state) is None
        assert parser_ivan_vip(
            "Su btc abbiamo preso ieri sera una super Reentry", CH_IVAN, bridge_state,
        ) is None
        assert bridge_state.ivan_last_trade["symbol"] == "XAUUSD"
        assert bridge_state.ivan_btc_trades == {}

    def test_states_never_cross(self, bridge_state: BridgeState):
        parser_ivan_vip(GOLD_SETUP, CH_IVAN, bridge_state)
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        assert bridge_state.ivan_last_trade["symbol"] == "XAUUSD"
        assert set(bridge_state.ivan_btc_trades) == {"BTCUSD", "XAGUSD"}
        be = parser_ivan_vip("Spostiamo SL a BE", CH_IVAN, bridge_state)
        assert be["symbol"] == "XAUUSD"
        be_btc = parser_ivan_btc("Spostiamo SL a BE", CH_BTC, bridge_state)
        assert be_btc["symbol"] == "XAGUSD"
        assert be_btc["be_price"] == 64.365


class TestManagement:
    def test_be_free_risk_phrase(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        sig = parser_ivan_btc("Mettiamo BE x free risk", CH_BTC, bridge_state)
        assert sig["action"] == "CHECK_AND_BE"
        assert sig["symbol"] == "BTCUSD"

    def test_be_named_symbol(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        sig = parser_ivan_btc("Mettiamo SL a BE su BTC", CH_BTC, bridge_state)
        assert sig["action"] == "CHECK_AND_BE"
        assert sig["symbol"] == "BTCUSD"
        assert sig["be_price"] == 78700.0

    def test_selective_close_btc(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        sig = parser_ivan_btc(
            "Chiduamo quasi tutto su BTC\nLasciamo solo qualcosina open",
            CH_BTC, bridge_state,
        )
        assert sig["action"] == "CLOSE_SELECTIVE"
        assert sig["symbol"] == "BTCUSD"
        assert "XAGUSD" in bridge_state.ivan_btc_trades

    def test_close_all_named_symbol(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        sig = parser_ivan_btc("Chiudiamo tutto su XAG", CH_BTC, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAGUSD"
        assert "BTCUSD" in bridge_state.ivan_btc_trades

    def test_update_sl_xag_decimals(self, bridge_state: BridgeState):
        parser_ivan_btc(XAG_SETUP, CH_BTC, bridge_state)
        sig = parser_ivan_btc("Spostiamo SL a 63.800", CH_BTC, bridge_state)
        assert sig["action"] == "UPDATE_SL"
        assert sig["symbol"] == "XAGUSD"
        assert sig["new_sl"] == 63.8
        assert bridge_state.ivan_btc_trades["XAGUSD"]["sl"] == 63.8

    def test_price_only_reentry_goes_to_matching_symbol(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        parser_ivan_btc(XAG_REENTRY, CH_BTC, bridge_state)
        assert bridge_state.ivan_btc_last_symbol == "XAGUSD"
        sig = _ok(parser_ivan_btc("potete rientrare ora da 78.800", CH_BTC, bridge_state))
        assert sig["action"] == "OPEN"
        assert sig["symbol"] == "BTCUSD"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 78800.0
        assert sig["tp_levels"] == [74000.0, 70000.0, 65000.0, 55000.0]
        assert sig["fixed_lot"] == 0.01

    def test_reentry_without_state_ignored(self, bridge_state: BridgeState):
        assert parser_ivan_btc("potete rientrare ora da 78.800", CH_BTC, bridge_state) is None
        assert parser_ivan_btc("Spostiamo SL a BE", CH_BTC, bridge_state) is None

    def test_state_persists_across_restart(self, tmp_path: Path):
        f = tmp_path / "bridge_state.json"
        st = BridgeState(f)
        parser_ivan_btc(BTC_SETUP, CH_BTC, st)
        parser_ivan_btc(XAG_SETUP, CH_BTC, st)
        st2 = BridgeState(f)
        assert set(st2.ivan_btc_trades) == {"BTCUSD", "XAGUSD"}
        assert st2.ivan_btc_last_symbol == "XAGUSD"
        assert st2.ivan_last_trade is None
        later = time.time() + IVAN_REENTRY_DEDUP_TTL_SEC + 1
        with mock.patch.object(time, "time", lambda: later):
            sig = parser_ivan_btc("Rientriamo ora su BTC", CH_BTC, st2)
        assert sig["symbol"] == "BTCUSD"
        assert sig["entry"] == 78700.0
        assert sig["allow_stack"] is True

    def test_reentry_right_after_setup_is_deduplicated(self, bridge_state: BridgeState):
        parser_ivan_btc(BTC_SETUP, CH_BTC, bridge_state)
        assert parser_ivan_btc("Rientriamo da qui", CH_BTC, bridge_state) is None
