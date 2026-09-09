"""Parser regression tests for TG TradinGo bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TG_ROOT = Path(__file__).resolve().parents[1]
if str(TG_ROOT) not in sys.path:
    sys.path.insert(0, str(TG_ROOT))

# Import parsers without Telethon client startup side effects
from bridge_core import (
    BridgeState,
    validate_signal,
    apply_lot_rules,
    match_close_all_intent,
    salvage_incoherent_entry_range,
    payload_for_ea,
    make_signal_id,
)
from tradingo_bridge import (
    GOLD_CMD_DEDUP_TTL_SEC,
    parser_sala_gold,
    parser_sala_oro,
    parser_sala_stark,
    parser_sala_vip,
    parser_ivan_vip,
    parser_zanni_vip,
    coerce_edit_open_to_update,
    pf,
)


CH1 = {
    "id": "CH1",
    "magic_base": 11000,
    "risk_percent": 0.5,
    "splits": [0.4, 0.4, 0.2],
}

CH2 = {
    "id": "CH2",
    "magic_base": 12000,
    "risk_percent": 0.5,
    "splits": [0.6, 0.4],
}

CH3 = {
    "id": "CH3",
    "magic_base": 13000,
    "fixed_lot_xauusd": 0.05,
    "fixed_lot_forex": 0.20,
}

CH4 = {
    "id": "CH4",
    "magic_base": 14000,
    "risk_percent": 0.5,
    "splits": [0.5, 0.4, 0.1],
}


CH_ORO = {
    "id": "CH_ORO",
    "magic_base": 14100,
    "execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10},
}

CH_IVAN = {
    "id": "CH_IVAN",
    "magic_base": 17000,
    "execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10},
}


class TestCH1ZanniVip:
    def test_open_buy_xauusd(self):
        text = (
            "🟢 BUY XAUUSD 4726\n\n"
            "TP1: 4729\nTP2: 4731\nTP3: 4735\n\nSL: 4717"
        )
        sig = parser_zanni_vip(text, CH1)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["symbol"] == "XAUUSD"
        assert sig["entry"] == 4726.0
        assert sig["tp_levels"] == [4729.0, 4731.0, 4735.0]
        assert sig["sl"] == 4717.0
        ok, _ = validate_signal(sig)
        assert ok

    def test_check_and_be_tp1(self):
        text = "TP1 ✅\nSpostiamo SL a BE"
        sig = parser_zanni_vip(text, CH1)
        assert sig["action"] == "CHECK_AND_BE"
        assert sig["tp_index"] == 1

    def test_check_and_close_tp2(self):
        text = "TP2 preso✅\n\nChiudete manualmente e stiamo dentro per il tp3"
        sig = parser_zanni_vip(text, CH1)
        assert sig["action"] == "CHECK_AND_CLOSE_TP"
        assert sig["tp_index"] == 2

    def test_close_tp3_explicit(self):
        text = "Noi chiudiamo ora il TP3"
        sig = parser_zanni_vip(text, CH1)
        assert sig["action"] == "CHECK_AND_CLOSE_TP"
        assert sig["tp_index"] == 3

    def test_close_all_generic(self):
        text = "cambio di trend, chiudiamo ora"
        sig = parser_zanni_vip(text, CH1)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"


class TestCH2SalaGold:
    def test_open_now_naked(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Gold sell now", CH2, bridge_state)
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"
        assert bridge_state.ch2_pending_open is True
        assert bridge_state.ch2_pending_dir == "SELL"

    def test_update_open_after_naked(self, bridge_state: BridgeState):
        parser_sala_gold("Gold buy now", CH2, bridge_state)
        text = (
            "Buy gold now 4802 - 4795\n"
            "SL: 4790\n"
            "Tp: 4812\n"
            "Tp: 4835"
        )
        sig = parser_sala_gold(text, CH2, bridge_state)
        assert sig["action"] == "UPDATE_OPEN"
        assert sig["direction"] == "BUY"
        assert sig["sl"] == 4790.0
        assert sig["tp_levels"] == [4812.0, 4835.0]
        assert bridge_state.ch2_pending_open is False

    def test_standalone_open_with_range(self, bridge_state: BridgeState):
        text = (
            "Sell gold now 4796 - 4800\n"
            "SL: 4810\n"
            "Tp: 4785\n"
            "Tp: 4765"
        )
        sig = parser_sala_gold(text, CH2, bridge_state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4796.0, 4800.0]
        assert sig["entry"] is None

    def test_break_even_price(self, bridge_state: BridgeState):
        sig = parser_sala_gold("4789 gold break even", CH2, bridge_state)
        assert sig["action"] == "BREAK_EVEN_PRICE"
        assert sig["be_price"] == 4789.0

    def test_close_half_be(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Close half break even close +100 pips", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_partial_closure_break_even(self, bridge_state: BridgeState):
        # Bug 20-22 Jul: "PARTIAL CLOSURE" did not match "PARTIAL CLOSE"
        sig = parser_sala_gold("Partial closure break Even", CH2, bridge_state)
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_partial_closure_break_even_exclaim(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Partial closure break Even!!!", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_standalone_break_even(self, bridge_state: BridgeState):
        sig = parser_sala_gold("break Even", CH2, bridge_state)
        assert sig["action"] == "CHECK_AND_BE"
        assert sig["symbol"] == "XAUUSD"

    def test_tp1_hit_break_even(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "TP1 hit gold +90 pips break even", CH2, bridge_state
        )
        assert sig["action"] == "CHECK_AND_BE"

    def test_partial_close_break_even_plus_pips(self, bridge_state: BridgeState):
        # Bug 23 Jul: comma before break even was parsed as BE price → crash
        sig = parser_sala_gold(
            "Partial close, break even +100 pips", CH2, bridge_state
        )
        assert sig is not None
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_this_other_partial_break_even(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "This other partial break even", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_break_even_plus_pips_not_price(self, bridge_state: BridgeState):
        # Must not treat "+100" / lone punctuation as BREAK_EVEN_PRICE
        sig = parser_sala_gold("break even +100 pips", CH2, bridge_state)
        assert sig["action"] == "CHECK_AND_BE"

    def test_state_persists_across_restart(self, tmp_path: Path):
        state_file = tmp_path / "bridge_state.json"
        s1 = BridgeState(state_file)
        parser_sala_gold("Gold buy now", CH2, s1)
        s2 = BridgeState(state_file)
        assert s2.ch2_pending_open is True
        assert s2.ch2_pending_dir == "BUY"


class TestCH3SalaVip:
    def test_open_xauusd(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        text = (
            "NUOVO ORDINE - XAUUSDpm Buy\n"
            "Entrata: 4736.64 [Lotti: 0.01]\n"
            "Nessuno SL\nNessuno TP"
        )
        sig = parser_sala_vip(text, CH3, state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["symbol"] == "XAUUSD"
        assert sig["entry"] == 4736.64
        assert sig["tp_levels"] == []
        assert sig["sl"] is None
        sized = apply_lot_rules(sig, {"execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10}})
        assert sized["fixed_lot"] == 0.20
        assert sized["trades"] == 1

        mod = (
            "XAUUSDpm Buy - Modificato\n"
            "Nuovo TP: 4747.00 [103.6 Pips]"
        )
        upd = parser_sala_vip(mod, CH3, state)
        assert upd["action"] == "UPDATE_TP"
        assert upd["new_tp"] == 4747.0

    def test_new_order_then_modified_open(self, tmp_path: Path):
        """Real flow — NEW ORDER senza TP apre, il Modified sposta solo il TP."""
        state = BridgeState(tmp_path / "bridge_state.json")
        new_order = (
            "NUOVO ORDINE - NZDJPYpm Sell\n"
            "Entrata: 95.102 [Lotti: 0.02]\n"
            "Nessuno SL\nNessuno TP"
        )
        sig = parser_sala_vip(new_order, CH3, state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["symbol"] == "NZDJPY"
        assert sig["entry"] == 95.102
        assert sig["tp_levels"] == []
        modified = (
            "NZDJPYpm Sell - Modificato\n"
            "Nuovo TP: 94.801 [30.1 Pips]"
        )
        upd = parser_sala_vip(modified, CH3, state)
        assert upd["action"] == "UPDATE_TP"
        assert upd["new_tp"] == 94.801
        assert upd["symbol"] == "NZDJPY"

    def test_update_tp(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        parser_sala_vip(
            "NUOVO ORDINE - XAUUSDpm Buy\nEntrata: 4736.64\nNessuno TP",
            CH3,
            state,
        )
        parser_sala_vip(
            "XAUUSDpm Buy - Modificato\nNuovo TP: 4747.00 [103.6 Pips]",
            CH3,
            state,
        )
        text = (
            "XAUUSDpm Buy - Modificato\n"
            "Nuovo TP: 4750.00 [103.6 Pips]"
        )
        sig = parser_sala_vip(text, CH3, state)
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == 4750.0

    def test_update_sl_be(self, tmp_path: Path):
        text = (
            "XAUUSDpm Buy - Modificato\n"
            "Nuovo SL: 4730.00 [15.9 Pips]\n"
            "Stop spostato a pareggio"
        )
        sig = parser_sala_vip(text, CH3)
        assert sig["action"] == "UPDATE_SL"
        assert sig["new_sl"] == 4730.0
        assert sig["is_be"] is True

    def test_check_and_close(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        parser_sala_vip(
            "NUOVO ORDINE - XAUUSDpm Buy\nEntrata: 4736.64\nNessuno TP",
            CH3,
            state,
        )
        parser_sala_vip(
            "XAUUSDpm Buy - Modificato\nNuovo TP: 4747.00",
            CH3,
            state,
        )
        text = "CHIUSO - XAUUSDpm Buy"
        sig = parser_sala_vip(text, CH3, state)
        assert sig["action"] == "CHECK_AND_CLOSE"
        assert sig["direction"] == "BUY"
        assert state.forex_last_trade is None

    def test_open_forex_english(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        text = (
            "NEW ORDER - GBPCADpm Sell\n"
            "Entry: 1.89235 [Lots: 0.02]\n"
            "No SL\nNo TP"
        )
        sig = parser_sala_vip(text, CH3, state)
        assert sig["action"] == "OPEN"
        assert sig["symbol"] == "GBPCAD"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 1.89235
        assert sig["tp_levels"] == []
        upd = parser_sala_vip(
            "GBPCADpm Sell - Modified\nNew TP: 1.88924 [31.1 Pips]",
            CH3,
            state,
        )
        assert upd["action"] == "UPDATE_TP"
        assert upd["new_tp"] == 1.88924

    def test_update_tp_english(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        parser_sala_vip(
            "NEW ORDER - GBPCADpm Sell\nEntry: 1.89235\nNo TP",
            CH3,
            state,
        )
        parser_sala_vip(
            "GBPCADpm Sell - Modified\nNew TP: 1.88924 [31.1 Pips]",
            CH3,
            state,
        )
        text = (
            "GBPCADpm Sell - Modified\n"
            "New TP: 1.88500 [31.1 Pips]"
        )
        sig = parser_sala_vip(text, CH3, state)
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == 1.885

    def test_close_english(self):
        text = "CLOSED - GBPCADpm Sell"
        sig = parser_sala_vip(text, CH3)
        assert sig["action"] == "CHECK_AND_CLOSE"


class TestCHORO:
    def test_open_with_two_tp(self):
        text = (
            "XAUUSD BUY 4073-4071\n"
            "TP 4094\n"
            "TP 4120\n"
            "SL 4068"
        )
        sig = parser_sala_oro(text, CH_ORO)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert len(sig["tp_levels"]) == 2
        assert sig["sl"] == 4068.0
        assert sig["entry_range"] == [4071.0, 4073.0]
        assert sig["entry"] is None

    def test_open_single_tp(self):
        text = "XAUUSD BUY 4088\nTP 4098\nSL 4083"
        sig = parser_sala_oro(text, CH_ORO)
        assert len(sig["tp_levels"]) == 1

    def test_close_half_be_brekiven(self):
        sig = parser_sala_oro("60 PIPS CLOSE OR BREKIVEN ✅", CH_ORO)
        assert sig["action"] == "CLOSE_HALF_BE"
        assert sig["symbol"] == "XAUUSD"

    def test_compact_sell_without_symbol(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        assert parser_sala_oro("4020 sell", CH_ORO, state) is None
        assert state.oro_pending_dir == "SELL"
        assert state.oro_pending_entry == 4020.0

    def test_tp_sl_completes_pending(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        parser_sala_oro("4020 sell", CH_ORO, state)
        sig = parser_sala_oro("Tp 4010 | Sl 4024", CH_ORO, state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["symbol"] == "XAUUSD"
        assert sig["entry"] == 4020.0
        assert sig["tp_levels"] == [4010.0]
        assert sig["sl"] == 4024.0

    def test_update_tp_on_edit(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        sig1 = parser_sala_oro(
            "XAUUSD SELL 4020-4022\nTP 4010\nSL 4024", CH_ORO, state
        )
        assert sig1["action"] == "OPEN"
        sig2 = parser_sala_oro(
            "XAUUSD SELL 4020-4022\nTP 4012\nSL 4024", CH_ORO, state
        )
        assert sig2["action"] == "UPDATE_TP"
        assert sig2["new_tp"] == 4012.0

    def test_fragmented_sell_sl_tp_sequence(self, tmp_path: Path):
        """Real flow 13:32 — Sell 4034, Sl 4039, Tp 4027 as separate messages."""
        state = BridgeState(tmp_path / "bridge_state.json")
        assert parser_sala_oro("Sell 4034", CH_ORO, state) is None
        assert state.oro_pending_dir == "SELL"
        assert parser_sala_oro("Sl 4039", CH_ORO, state) is None
        assert state.oro_pending_sl == 4039.0
        sig = parser_sala_oro("Tp 4027", CH_ORO, state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 4034.0
        assert sig["sl"] == 4039.0
        assert sig["tp_levels"] == [4027.0]

    def test_range_only_updates_pending(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        parser_sala_oro("Sell 4029", CH_ORO, state)
        assert parser_sala_oro("4027-4029", CH_ORO, state) is None
        assert state.oro_pending_range == [4027.0, 4029.0]

    def test_zona_buy_range(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        assert parser_sala_oro("Zona buy 4026-4024", CH_ORO, state) is None
        assert state.oro_pending_dir == "BUY"
        assert state.oro_pending_entry is None
        assert state.oro_pending_range == [4024.0, 4026.0]

    def test_zona_buy_range_with_levels(self, tmp_path: Path):
        text = "Zona buy 4016-4010\nTP 4025\nSL 4008"
        sig = parser_sala_oro(text, CH_ORO)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["entry"] is None
        assert sig["entry_range"] == [4010.0, 4016.0]
        assert sig["tp_levels"] == [4025.0]
        assert sig["sl"] == 4008.0

    def test_jul24_edit_range_not_second_open(self, tmp_path: Path):
        """Fragment OPEN then complete-form / range EDITs must not stack OPENs."""
        state = BridgeState(tmp_path / "bridge_state.json")
        assert parser_sala_oro("Sell 4060", CH_ORO, state) is None
        assert parser_sala_oro("Sl 4065", CH_ORO, state) is None
        sig1 = parser_sala_oro("Tp 4053", CH_ORO, state)
        assert sig1["action"] == "OPEN"
        assert sig1["entry"] == 4060.0

        # Same levels as complete message → ignore
        assert (
            parser_sala_oro(
                "XAUUSD SELL 4060\nTP 4053\nSL 4065", CH_ORO, state
            )
            is None
        )

        # Range refinement → UPDATE_OPEN (not a second OPEN)
        sig2 = parser_sala_oro(
            "XAUUSD SELL 4060-4062\nTP 4053\nSL 4065", CH_ORO, state
        )
        assert sig2["action"] == "UPDATE_OPEN"
        assert sig2["entry_range"] == [4060.0, 4062.0]

        sig3 = parser_sala_oro(
            "XAUUSD SELL 4060-4063\nTP 4055\nSL 4065", CH_ORO, state
        )
        assert sig3["action"] == "UPDATE_OPEN"
        assert sig3["entry_range"] == [4060.0, 4063.0]
        assert sig3["tp_levels"] == [4055.0]

    def test_reentry_sets_allow_stack(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        sig1 = parser_sala_oro(
            "XAUUSD SELL 4060\nTP 4053\nSL 4065", CH_ORO, state
        )
        assert sig1["action"] == "OPEN"
        assert not sig1.get("allow_stack")
        sig2 = parser_sala_oro(
            "Rientriamo\nXAUUSD SELL 4055\nTP 4048\nSL 4060", CH_ORO, state
        )
        assert sig2["action"] == "OPEN"
        assert sig2["allow_stack"] is True

    def test_buy_now_price_sets_pending(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        assert parser_sala_oro("Buy now 4042", CH_ORO, state) is None
        assert state.oro_pending_dir == "BUY"
        assert state.oro_pending_entry == 4042.0

    def test_buy_now_after_stale_range_opens_clean(self, tmp_path: Path):
        """07:51 regression: Buy now 4042 + Sl/Tp must not reuse overnight range."""
        state = BridgeState(tmp_path / "bridge_state.json")
        state.set_oro_last_trade({
            "direction": "BUY",
            "entry": None,
            "entry_range": [4061.0, 4063.0],
            "sl": 4058.0,
            "tp_levels": [4070.0],
        })
        state.save()

        assert parser_sala_oro("Buy now 4042", CH_ORO, state) is None
        assert state.oro_pending_entry == 4042.0
        assert state.oro_last_trade is None

        sig = parser_sala_oro("Sl 4038 | Tp 4048", CH_ORO, state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["entry"] == 4042.0
        assert sig["entry_range"] is None
        assert sig["sl"] == 4038.0
        assert sig["tp_levels"] == [4048.0]
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_combined_sl_tp_without_pending_does_not_reuse_last(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        state.set_oro_last_trade({
            "direction": "BUY",
            "entry": None,
            "entry_range": [4061.0, 4063.0],
            "sl": 4058.0,
            "tp_levels": [4070.0],
        })
        state.save()
        assert parser_sala_oro("Sl 4038 | Tp 4048", CH_ORO, state) is None
        assert state.oro_last_trade["entry_range"] == [4061.0, 4063.0]
        assert state.oro_last_trade["sl"] == 4058.0


class TestCHIvan:
    def test_open_sell_four_tp(self):
        text = (
            "XAUUSD SELL 4011\n\n"
            "TP 1 4006\n"
            "TP 2 4004\n"
            "TP 3 4002\n"
            "TP 4 3990\n\n"
            "SL @ 4022"
        )
        sig = parser_ivan_vip(text, CH_IVAN)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 4011.0
        assert sig["tp_levels"] == [4006.0, 4004.0, 4002.0, 3990.0]
        assert sig["sl"] == 4022.0
        sized = apply_lot_rules(sig, CH_IVAN)
        assert sized["trades"] == 4
        assert sized["fixed_lot"] == 0.10

    def test_open_one_line_with_colons(self, tmp_path: Path):
        """Formato del canale dal 26/08: due punti e separatori '|' su una riga."""
        state = BridgeState(tmp_path / "st.json")
        text = ("XAUUSD SELL: 4596 |  | TP1: 4590 | TP2: 4587 | TP3: 4582 "
                "| TP4: 4510 |  | SL: 4608")
        sig = parser_ivan_vip(text, CH_IVAN, state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 4596.0
        assert sig["tp_levels"] == [4590.0, 4587.0, 4582.0, 4510.0]
        assert sig["sl"] == 4608.0
        assert validate_signal(sig)[0]
        assert apply_lot_rules(sig, CH_IVAN)["trades"] == 4

    def test_open_entry_zone(self, tmp_path: Path):
        """"XAUUSD BUY: 4591-4587": zona d'ingresso, non prezzo singolo."""
        state = BridgeState(tmp_path / "st.json")
        text = ("XAUUSD BUY: 4591-4587 |  | TP1: 4596 | TP2: 4600 | TP3: 4607 "
                "| TP4: 4630 |  | SL: 4570")
        sig = parser_ivan_vip(text, CH_IVAN, state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["entry"] is None
        assert sig["entry_range"] == [4587.0, 4591.0]
        assert sig["tp_levels"] == [4596.0, 4600.0, 4607.0, 4630.0]
        assert sig["sl"] == 4570.0
        assert validate_signal(sig)[0]

    def test_zone_setup_keeps_reference_for_next_commands(self, tmp_path: Path):
        """Su un setup a zona il TP dal lato sbagliato resta rifiutato."""
        state = BridgeState(tmp_path / "st.json")
        parser_ivan_vip(
            "XAUUSD BUY: 4591-4587 | TP1: 4596 | TP2: 4600 | TP3: 4607 | SL: 4570",
            CH_IVAN, state,
        )
        assert state.ivan_last_trade["entry_range"] == [4587.0, 4591.0]
        # 4580 è sotto la zona: per un BUY chiuderebbe a mercato.
        assert parser_ivan_vip("TP 3 spostiamo a 4580", CH_IVAN, state) is None
        sig = parser_ivan_vip("TP 3 spostiamo a 4620", CH_IVAN, state)
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == 4620.0

    def test_move_tp_noun_before_verb(self, tmp_path: Path):
        """"TP 3 spostiamo a 4580": ordine invertito, il 27/08 era UNPARSED."""
        state = BridgeState(tmp_path / "st.json")
        parser_ivan_vip(
            "XAUUSD SELL: 4596 | TP1: 4590 | TP2: 4587 | TP3: 4582 | SL: 4608",
            CH_IVAN, state,
        )
        sig = parser_ivan_vip("TP 3 spostiamo a 4580", CH_IVAN, state)
        assert sig is not None
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == 4580.0
        assert sig["tp_index"] == 3

    def test_tp_hit_comment_does_not_move_tp(self, tmp_path: Path):
        state = BridgeState(tmp_path / "st.json")
        parser_ivan_vip(
            "XAUUSD SELL: 4596 | TP1: 4590 | TP2: 4587 | SL: 4608",
            CH_IVAN, state,
        )
        assert parser_ivan_vip("TP 1 HIT SQUAD ✔️ | +100 PIPS", CH_IVAN, state) is None

    def test_check_and_be(self):
        sig = parser_ivan_vip("Spostiamo SL a BE", CH_IVAN)
        assert sig["action"] == "CHECK_AND_BE"
        assert sig["symbol"] == "XAUUSD"

    def test_close_now(self):
        sig = parser_ivan_vip("CHIUDERE ORA", CH_IVAN)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAUUSD"

    def test_usciamo_ora(self):
        sig = parser_ivan_vip("USCIAMO ORA", CH_IVAN)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAUUSD"

    def test_close_coverage_table(self, tmp_path: Path):
        state = BridgeState(tmp_path / "bridge_state.json")
        cases = [
            ("USCIAMO ORA", None),
            ("Usciamo qui a 5054 -40 PIPS❌", 5054.0),
            ("CHIUDERE ORA", None),
            ("Chiudiamo tutto", None),
            ("USCITE ORA", None),
            ("Usciamo a mercato", None),
            ("chiudete tutto adesso", None),
            ("Usciamo qui", None),
        ]
        for msg, ref in cases:
            sig = parser_ivan_vip(msg, CH_IVAN, BridgeState(tmp_path / f"st_{hash(msg)}.json"))
            assert sig is not None, msg
            assert sig["action"] == "CLOSE_ALL_SYMBOL", msg
            if ref is None:
                assert "reference_price" not in sig, msg
            else:
                assert sig.get("reference_price") == ref, msg

        # Price follow-up after USCIAMO ORA
        assert parser_ivan_vip("USCIAMO ORA", CH_IVAN, state)["action"] == "CLOSE_ALL_SYMBOL"
        follow = parser_ivan_vip("A 4060.5", CH_IVAN, state)
        assert follow is not None
        assert follow["action"] == "CLOSE_ALL_SYMBOL"
        assert follow["reference_price"] == 4060.5

    def test_close_ignore_preparatory(self):
        assert parser_ivan_vip("Pronti a chiudere se ve lo dico", CH_IVAN) is None
        assert parser_ivan_vip("Gestiamo a mercato", CH_IVAN) is None
        assert parser_ivan_vip("Se non li piace chiudete pure", CH_IVAN) is None
        assert parser_ivan_vip("TP 1 HIT SQUAD✔️", CH_IVAN) is None
        be = parser_ivan_vip("Spostiamo SL a BE", CH_IVAN)
        assert be["action"] == "CHECK_AND_BE"

    def test_meta_size_half_lot(self):
        text = (
            "XAUUSD BUY 4012\n\n"
            "TP 1 4018\n"
            "TP 2 4020\n"
            "TP 3 4023\n"
            "TP 4 4030\n\n"
            "SL @ 4000\n\n"
            "Meta size"
        )
        sig = parser_ivan_vip(text, CH_IVAN)
        assert sig["lot_factor"] == 0.5
        sized = apply_lot_rules(sig, CH_IVAN)
        assert sized["fixed_lot"] == 0.05

    def test_meta_size_accent_and_typo(self):
        base = (
            "XAUUSD SELL 4122\n\n"
            "TP 1 4117\n"
            "TP 2 4115\n"
            "TP 3 4112\n"
            "TP 4 4100\n\n"
            "SL @ 4135\n\n"
        )
        for suffix in ("METÀ SIZE", "MEZZA SIZE", "META SAZIE", "Half size"):
            sig = parser_ivan_vip(base + suffix, CH_IVAN)
            assert sig is not None, suffix
            assert sig["lot_factor"] == 0.5, suffix
            sized = apply_lot_rules(sig, CH_IVAN)
            assert sized["fixed_lot"] == 0.05, suffix

    def test_chat_ignored(self):
        assert parser_ivan_vip("BOOOOOOMM", CH_IVAN) is None
        assert parser_ivan_vip("SL", CH_IVAN) is None
        assert parser_ivan_vip("**TP 1 HIT SQUAD**", CH_IVAN) is None


class TestLotRules:
    def test_single_tp_020(self):
        ch = {"execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10}}
        sig = apply_lot_rules({"action": "OPEN", "tp_levels": [4100.0]}, ch)
        assert sig["trades"] == 1
        assert sig["fixed_lot"] == 0.20

    def test_two_tp_010_each(self):
        ch = {"execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10}}
        sig = apply_lot_rules({"action": "OPEN", "tp_levels": [4100.0, 4110.0]}, ch)
        assert sig["trades"] == 2
        assert sig["fixed_lot"] == 0.10

    def test_lot_factor(self):
        ch = {"execution": {"fixed_lot_single": 0.20, "fixed_lot_per_tp": 0.10}}
        sig = apply_lot_rules(
            {"action": "OPEN", "tp_levels": [4100.0, 4110.0], "lot_factor": 0.5},
            ch,
        )
        assert sig["fixed_lot"] == 0.05

    def test_open_now_gold_expected_two_splits(self):
        """GOLD naked OPEN_NOW: 2 tickets x fixed_lot_per_tp (not 1x single)."""
        ch = {
            "execution": {
                "fixed_lot_single": 0.20,
                "fixed_lot_per_tp": 0.10,
                "tp_levels_expected": 2,
            }
        }
        sig = apply_lot_rules({"action": "OPEN_NOW", "tp_levels": []}, ch)
        assert sig["trades"] == 2
        assert sig["fixed_lot"] == 0.10
        assert sig["splits"] == [0.5, 0.5]

    def test_open_now_single_expected_keeps_single(self):
        ch = {
            "execution": {
                "fixed_lot_single": 0.20,
                "fixed_lot_per_tp": 0.10,
                "tp_levels_expected": 1,
            }
        }
        sig = apply_lot_rules({"action": "OPEN_NOW"}, ch)
        assert sig["trades"] == 1
        assert sig["fixed_lot"] == 0.20


class TestCH4SalaStark:
    def test_open_markdown(self):
        text = (
            "Apro una nuova operazione\n"
            "XAUUSD BUY\n"
            "Entry: 4725\n"
            "SL: 4712.37\n"
            "TP1: 4726.87 / TP2: 4730.77 / TP3: 4798.71"
        )
        sig = parser_sala_stark(text, CH4)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["symbol"] == "XAUUSD"
        assert len(sig["tp_levels"]) == 3
        assert sig["is_add_signal"] is False

    def test_add_with_inherit(self):
        text = (
            "Aggiungo un'altra operazione\n"
            "XAUUSD BUY\n"
            "Entry: 4730"
        )
        sig = parser_sala_stark(text, CH4)
        assert sig["is_add_signal"] is True
        assert sig["inherit_from_first"] is True

    def test_add_with_own_sl_tp(self):
        text = (
            "Aggiungo un'altra operazione\n"
            "GBPUSD SELL\n"
            "Entry: 1.35200\n"
            "SL: 1.35800\n"
            "TP1: 1.35100"
        )
        sig = parser_sala_stark(text, CH4)
        assert sig["is_add_signal"] is True
        assert sig["inherit_from_first"] is False
        assert sig["sl"] == 1.358

    def test_flat_forex_open(self):
        text = "BUY   GBPUSD 1.35864\nSL    1.31230\nTP    1.36050"
        sig = parser_sala_stark(text, CH4)
        assert sig["action"] == "OPEN"
        assert sig["symbol"] == "GBPUSD"


class TestValidation:
    def test_rejects_incoherent_sl_for_buy(self):
        sig = {
            "action": "OPEN",
            "direction": "BUY",
            "symbol": "XAUUSD",
            "entry": 4800.0,
            "sl": 4810.0,
            "tp_levels": [4815.0],
            "magic_base": 11000,
            "splits": [0.5, 0.5],
        }
        ok, reason = validate_signal(sig)
        assert not ok
        assert "SL" in reason

    def test_rejects_bad_splits(self):
        sig = {
            "action": "OPEN",
            "direction": "BUY",
            "symbol": "XAUUSD",
            "entry": 4800.0,
            "sl": 4790.0,
            "tp_levels": [4810.0],
            "magic_base": 11000,
            "splits": [0.3, 0.3],
        }
        ok, reason = validate_signal(sig)
        assert not ok
        assert "splits" in reason


class TestEditOpenCoerce:
    def test_edit_open_becomes_update(self):
        sig = {"action": "OPEN", "direction": "SELL", "symbol": "XAUUSD"}
        out = coerce_edit_open_to_update(sig, is_edit=True)
        assert out["action"] == "UPDATE_OPEN"

    def test_new_open_unchanged(self):
        sig = {"action": "OPEN", "direction": "SELL", "symbol": "XAUUSD"}
        out = coerce_edit_open_to_update(sig, is_edit=False)
        assert out["action"] == "OPEN"

    def test_allow_stack_edit_keeps_open(self):
        sig = {
            "action": "OPEN",
            "direction": "SELL",
            "symbol": "XAUUSD",
            "allow_stack": True,
        }
        out = coerce_edit_open_to_update(sig, is_edit=True)
        assert out["action"] == "OPEN"


class TestGoldBreakEvenHardening:
    def test_partial_close_plus_pips_no_crash(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Partial close, break even +100 pips", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_partial_closure_be(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Partial closure break Even!!!", CH2, bridge_state)
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_bare_break_even(self, bridge_state: BridgeState):
        sig = parser_sala_gold("break Even", CH2, bridge_state)
        assert sig["action"] == "CHECK_AND_BE"

    def test_manual_break_even_instruction(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "MANUALLY SET A BREAK EVEN ON ALL YOUR POSITIONS!", CH2, bridge_state
        )
        assert sig["action"] == "CHECK_AND_BE"

    def test_pf_defensive(self):
        assert pf(".") is None
        assert pf("") is None
        assert pf("+100") == 100.0
        assert pf("5054") == 5054.0


class TestCloseIntentHelper:
    def test_match_close_with_price(self):
        ok, px = match_close_all_intent("USCIAMO QUI A 5054 -40 PIPS")
        assert ok is True
        assert px == 5054.0

    def test_match_chiudiamo_typos(self):
        for text in ("CHIDUAMO ORA", "Chiduamo ora !", "CHIUDAMO ORA"):
            ok, _ = match_close_all_intent(text.upper())
            assert ok is True, text

    def test_signal_id_stable(self):
        a = make_signal_id(1, 2, "NEW")
        b = make_signal_id(1, 2, "NEW")
        assert a == b
        assert len(a) == 10


# ─────────────────────────────────────────────────────────────────────────────
# Regressioni audit parser (luglio 2026)
# ─────────────────────────────────────────────────────────────────────────────


class TestZanniBeAndTpClose:
    def test_sposto_stop_a_be(self):
        sig = parser_zanni_vip("E sposto stop a BE", CH1)
        assert sig is not None
        assert sig["action"] == "CHECK_AND_BE"

    def test_portiamo_stop_loss_in_pareggio(self):
        sig = parser_zanni_vip("Portiamo lo stop loss in pareggio", CH1)
        assert sig["action"] == "CHECK_AND_BE"

    def test_chiudete_il_tp1(self):
        sig = parser_zanni_vip(
            "Se siete entrati sui 4820 chiudete il tp1 anche a 4817!", CH1
        )
        assert sig is not None
        assert sig["action"] == "CHECK_AND_CLOSE_TP"
        assert sig["tp_index"] == 1


class TestGoldPartialBeAndSl:
    def test_close_half_be_with_trailing_close(self, bridge_state):
        """'…close half break even close' non deve diventare CLOSE_ALL_SYMBOL."""
        sig = parser_sala_gold(
            "+70 pips close half break even close", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_standalone_sl_updates_known_trade(self, bridge_state):
        parser_sala_gold(
            "Sell gold now 4791 - 4800\nSL: 4805\nTp: 4782\nTp: 4765",
            CH2,
            bridge_state,
        )
        # SL più vicino all'entry: riduce il rischio, si applica.
        sig = parser_sala_gold("SL 4803", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "UPDATE_SL"
        assert sig["direction"] == "SELL"
        assert sig["new_sl"] == 4803.0

    def test_standalone_sl_widening_within_cap_applied(self, bridge_state):
        """"SL 4660" su un SELL con SL 4656 allarga di poco: si esegue."""
        parser_sala_gold(
            "Sell gold now 4639 - 4645\nSL: 4656\nTp: 4630", CH2, bridge_state
        )
        sig = parser_sala_gold("SL 4660", CH2, bridge_state)
        assert sig is not None
        assert sig["new_sl"] == 4660.0

    def test_standalone_sl_widening_beyond_cap_is_capped(self, bridge_state):
        """Oltre il doppio del rischio lo SL viene fissato al tetto, non scartato."""
        # SELL, riferimento 4642 (zona 4639-4645), SL 4656 → rischio 14, tetto 28.
        parser_sala_gold(
            "Sell gold now 4639 - 4645\nSL: 4656\nTp: 4630", CH2, bridge_state
        )
        sig = parser_sala_gold("SL 4700", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "UPDATE_SL"
        assert sig["new_sl"] == 4670.0
        # BUY speculare: riferimento 4703, SL 4690 → rischio 13, tetto 26.
        parser_sala_gold(
            "Buy gold now 4700 - 4706\nSL: 4690\nTp: 4720", CH2, bridge_state
        )
        assert parser_sala_gold("SL 4680", CH2, bridge_state)["new_sl"] == 4680.0
        parser_sala_gold(
            "Buy gold now 4700 - 4706\nSL: 4690\nTp: 4720", CH2, bridge_state
        )
        assert parser_sala_gold("SL 4600", CH2, bridge_state)["new_sl"] == 4677.0

    def test_standalone_sl_without_trade_ignored(self, bridge_state):
        assert parser_sala_gold("SL 4807", CH2, bridge_state) is None

    def test_canceled_closes_the_signal_just_sent(self, bridge_state):
        """Il 24/08 "Canceled" 21s dopo l'apertura restò UNPARSED: -350 EUR."""
        opened = parser_sala_gold("Gold sell now", CH2, bridge_state)
        assert opened["action"] == "OPEN_NOW"
        sig = parser_sala_gold("Canceled", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAUUSD"

    def test_cancel_forms_and_translations(self, bridge_state):
        for text in ("Annullato", "Annulliamo", "Cancelled", "Anulado", "Annule"):
            parser_sala_gold(
                "Sell gold now 4639 - 4645\nSL: 4656\nTp: 4630", CH2, bridge_state
            )
            sig = parser_sala_gold(text, CH2, bridge_state)
            assert sig is not None, text
            assert sig["action"] == "CLOSE_ALL_SYMBOL", text

    def test_cancel_without_recent_signal_does_nothing(self, bridge_state):
        assert parser_sala_gold("Canceled", CH2, bridge_state) is None

    def test_cancel_of_a_level_is_not_a_close(self, bridge_state):
        parser_sala_gold(
            "Sell gold now 4639 - 4645\nSL: 4656\nTp: 4630", CH2, bridge_state
        )
        sig = parser_sala_gold("Annulliamo il TP3", CH2, bridge_state)
        assert sig is None or sig["action"] != "CLOSE_ALL_SYMBOL"

    def test_cancel_of_a_lesson_is_not_a_close(self, bridge_state):
        parser_sala_gold(
            "Sell gold now 4639 - 4645\nSL: 4656\nTp: 4630", CH2, bridge_state
        )
        assert parser_sala_gold("Formazione annullata", CH2, bridge_state) is None

    def test_composite_sl_hit_plus_new_order(self, bridge_state):
        """"SL hit | | Sell gold now": l'ordine dentro il messaggio va eseguito."""
        sig = parser_sala_gold("SL hit \n\nSell gold now", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"

    def test_sl_hit_alone_still_ignored(self, bridge_state):
        assert parser_sala_gold("SL hit", CH2, bridge_state) is None

    def test_close_half_be_emitted_once_per_signal(self, bridge_state):
        """L'edit o la traduzione dello stesso comando non chiude due volte."""
        first = parser_sala_gold(
            "+70 pips close half break even close", CH2, bridge_state
        )
        assert first["action"] == "CLOSE_HALF_BE"
        assert (
            parser_sala_gold("+70 pips chiudi meta e break even", CH2, bridge_state)
            is None
        )

    def test_standalone_sl_same_level_ignored(self, bridge_state):
        parser_sala_gold(
            "Sell gold now 4791 - 4800\nSL: 4805\nTp: 4782", CH2, bridge_state
        )
        assert parser_sala_gold("SL 4805", CH2, bridge_state) is None

    def test_identical_setup_repost_ignored(self, bridge_state):
        text = "Buy gold now 4807 - 4800\nSL: 4795\nTp: 4814\nTp: 4835"
        first = parser_sala_gold(text, CH2, bridge_state)
        assert first["action"] == "OPEN"
        assert parser_sala_gold(text, CH2, bridge_state) is None

    def test_different_setup_still_opens(self, bridge_state):
        parser_sala_gold(
            "Buy gold now 4807 - 4800\nSL: 4795\nTp: 4814", CH2, bridge_state
        )
        sig = parser_sala_gold(
            "Buy gold now 4795 - 4802\nSL: 4790\nTp: 4812", CH2, bridge_state
        )
        assert sig["action"] == "OPEN"

    def test_naked_then_setup_still_update_open(self, bridge_state):
        assert parser_sala_gold("Gold sell now", CH2, bridge_state)["action"] == "OPEN_NOW"
        sig = parser_sala_gold(
            "Sell gold now 4790 - 4800\nSL: 4805\nTp: 4782", CH2, bridge_state
        )
        assert sig["action"] == "UPDATE_OPEN"


FOREX_NEW_ORDER = """NUOVO ORDINE - GBPCADpm Buy 📈
Entrata: 1.84642 [Lotti: 0.02]
Nessuno SL
Nessuno TP
Questo messaggio non incita a investire, riporta i nostri trade"""

FOREX_MODIFIED = """GBPCADpm Buy - Modificato
Nuovo TP: 1.85000
Questo messaggio non incita a investire, riporta i nostri trade"""

FOREX_CLOSED = """🟠 CHIUSO - GBPUSDpm Sell 🟠
Entrata:    1.35166
Uscita:     1.34954
Questo messaggio non incita a investire, riporta i nostri trade"""


class TestForexDisclaimerNotIgnored:
    def test_new_order_with_disclaimer_opens(self, bridge_state):
        sig = parser_sala_vip(FOREX_NEW_ORDER, CH3, bridge_state)
        assert sig["action"] == "OPEN"
        assert sig["symbol"] == "GBPCAD"
        assert sig["entry"] == 1.84642

    def test_modified_after_open_updates_levels(self, bridge_state):
        parser_sala_vip(FOREX_NEW_ORDER, CH3, bridge_state)
        sig = parser_sala_vip(FOREX_MODIFIED, CH3, bridge_state)
        assert sig["action"] == "UPDATE_TP"
        assert sig["symbol"] == "GBPCAD"
        assert sig["new_tp"] == 1.85

    def test_closed_with_disclaimer_emits_close(self, bridge_state):
        sig = parser_sala_vip(FOREX_CLOSED, CH3, bridge_state)
        assert sig["action"] == "CHECK_AND_CLOSE"
        assert sig["symbol"] == "GBPUSD"
        assert sig["direction"] == "SELL"

    def test_report_still_ignored(self, bridge_state):
        assert parser_sala_vip(
            "GIORNALIERO RAPPORTO\nProfitto: 120€", CH3, bridge_state
        ) is None


class TestStarkManualClose:
    def test_close_uses_symbol_in_text(self, bridge_state):
        sig = parser_sala_stark("⚖️ Chiusa a Break Even\n**XAUUSD**", CH4, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAUUSD"

    def test_close_falls_back_to_last_trade_symbol(self, bridge_state):
        parser_sala_stark(
            "BUY   GBPUSD 1.35864\n\nSL    1.31230\n\nTP    1.36050",
            CH4,
            bridge_state,
        )
        sig = parser_sala_stark("Chiusa in take profit ✅", CH4, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "GBPUSD"

    def test_close_without_context_ignored(self, bridge_state):
        assert parser_sala_stark("Chiusa in take profit ✅", CH4, bridge_state) is None

    def test_open_still_parsed(self, bridge_state):
        sig = parser_sala_stark(
            "Apro una nuova operazione 🚀\n**XAUUSD** **SELL**\n"
            "Entry: **4809**\nSL: `4820.61`\nTP1: `4804.41`",
            CH4,
            bridge_state,
        )
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"


class TestOroCloseAndIgnore:
    def test_close_all_with_noise_word(self, bridge_state):
        sig = parser_sala_oro("Chiudiamo tutto ragazzi", CH_ORO, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["symbol"] == "XAUUSD"

    def test_exit_with_reference_price(self, bridge_state):
        sig = parser_sala_oro("usciamo qui a 4015", CH_ORO, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"
        assert sig["reference_price"] == 4015.0

    def test_noise_still_ignored(self, bridge_state):
        assert parser_sala_oro(
            "Ragazzi stasera live di formazione", CH_ORO, bridge_state
        ) is None
        assert parser_sala_oro("Report: +300 pips ✅", CH_ORO, bridge_state) is None

    def test_setup_with_noise_word_parsed(self, bridge_state):
        sig = parser_sala_oro(
            "Attendiamo XAUUSD BUY 4050 | TP 4060 | SL 4040", CH_ORO, bridge_state
        )
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["sl"] == 4040.0

    def test_close_clears_state(self, bridge_state):
        parser_sala_oro(
            "XAUUSD SELL 4020-4022 | TP 4010 | SL 4024", CH_ORO, bridge_state
        )
        parser_sala_oro("Chiudiamo tutto", CH_ORO, bridge_state)
        assert bridge_state.oro_last_trade is None
        assert bridge_state.oro_pending_dir is None


class TestIvanIgnoreGuard:
    def test_setup_with_take_profit_wording_not_ignored(self, bridge_state):
        sig = parser_ivan_vip(
            "XAUUSD SELL 4059\nTP 1 4055\nTP 2 4052\nSL @ 4065\n"
            "Take profit ravvicinati",
            CH_IVAN,
            bridge_state,
        )
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["tp_levels"] == [4055.0, 4052.0]

    def test_preparatory_phrase_still_ignored(self, bridge_state):
        assert parser_ivan_vip(
            "Pronti a chiudere se ve lo dico", CH_IVAN, bridge_state
        ) is None

    def test_ignore_registry_reports_pattern(self):
        from tradingo_bridge import matched_ignore_pattern

        assert matched_ignore_pattern("ivan_vip", "PRONTI A CHIUDERE SE VE LO DICO")
        assert matched_ignore_pattern("ivan_vip", "XAUUSD SELL 4059") is None


IVAN_SETUP = """XAUUSD SELL 4059
TP 1 4055
TP 2 4052
TP 3 4048
TP 4 4043
SL @ 4065"""


class TestSelectiveClose:
    """Casi reali CH_IVAN 31/07: chiusure parziali eseguite come chiusura totale."""

    @pytest.mark.parametrize(
        "text,keep",
        [
            ("Chiudiamo le entry meno premium", "BEST"),
            ("Chiduamo a be l'entrata meno premium a Be", "BEST"),
            ("Chiduamo orario entry più in basso", "HIGHEST"),
            ("Chiudiamo le posizioni più in alto", "LOWEST"),
            ("E lasciamo solo quelle da sopra", "HIGHEST"),
            ("E lasciamo solo quelle più in alto", "HIGHEST"),
            ("Teniamo solo le entrate più in basso", "LOWEST"),
        ],
    )
    def test_selective_close_variants(self, bridge_state, text, keep):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip(text, CH_IVAN, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "CLOSE_SELECTIVE", text
        assert sig["keep"] == keep, text
        assert sig["symbol"] == "XAUUSD"
        ok, reason = validate_signal(sig)
        assert ok, reason

    @pytest.mark.parametrize(
        "text",
        [
            "Chiduamo tutto",
            "CHIUDIAMO ORA ‼️",
            "Usciamo ora",
        ],
    )
    def test_total_close_still_total(self, bridge_state, text):
        sig = parser_ivan_vip(text, CH_IVAN, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "CLOSE_ALL_SYMBOL", text

    @pytest.mark.parametrize(
        "text",
        [
            "Stiamo pronti a chiudere se torna su",
            "Gestiamo a mercato",
            (
                "E lasciamo solo quelle da sopra a respirare un po'"
                " prima di decidere cosa fare domani mattina presto"
            ),
        ],
    )
    def test_narrative_not_selective(self, bridge_state, text):
        sig = parser_ivan_vip(text, CH_IVAN, bridge_state)
        assert sig is None or sig["action"] != "CLOSE_SELECTIVE", text

    def test_invalid_keep_rejected_by_validation(self):
        ok, reason = validate_signal(
            {
                "action": "CLOSE_SELECTIVE",
                "symbol": "XAUUSD",
                "keep": "MIDDLE",
                "magic_base": 17000,
            }
        )
        assert not ok
        assert "keep" in reason


class TestIvanReentry:
    def test_rientrate_ora_reopens_last_setup(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Rientrate ora", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["symbol"] == "XAUUSD"
        assert sig["tp_levels"] == [4055.0, 4052.0, 4048.0, 4043.0]
        assert sig["sl"] == 4065.0
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_reentry_after_close_keeps_direction(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip("Chiudere ora", CH_IVAN, bridge_state)[
            "action"
        ] == "CLOSE_ALL_SYMBOL"
        sig = parser_ivan_vip("rientriamo", CH_IVAN, bridge_state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"

    def test_reentry_keeps_lot_factor(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP + "\nMetà size", CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Riapriamo ora", CH_IVAN, bridge_state)
        assert sig["lot_factor"] == 0.5

    def test_reentry_without_previous_setup_ignored(self, bridge_state):
        assert parser_ivan_vip("Rientrate ora", CH_IVAN, bridge_state) is None

    def test_new_setup_wins_over_reentry_wording(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip(
            "Rientriamo: XAUUSD BUY 4070\nTP 1 4075\nSL @ 4062",
            CH_IVAN,
            bridge_state,
        )
        assert sig["direction"] == "BUY"
        assert sig["entry"] == 4070.0

    def test_rientrare_ora_a_prezzo_usa_il_nuovo_entry(self, bridge_state):
        """Caso reale CH_IVAN 28/07 12:11Z: 'Rientrare ora a 4023' era UNPARSED."""
        parser_ivan_vip(
            "XAUUSD BUY 4028\nTP 1 4033\nTP 2 4037\nTP 3 4040\nTP 4 4050\nSL @ 4015",
            CH_IVAN,
            bridge_state,
        )
        sig = parser_ivan_vip("Rientrare ora a 4023", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["entry"] == 4023.0
        assert sig["tp_levels"] == [4033.0, 4037.0, 4040.0, 4050.0]
        assert sig["sl"] == 4015.0
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_rientro_abbreviato_con_size_ridotta(self, bridge_state):
        """Caso reale CH_IVAN 31/07 11:12Z: 'Rientri piccola da qui a 58'."""
        parser_ivan_vip(
            "XAUUSD SELL 4054\nTP 1 4050\nTP 2 4047\nTP 3 4042\nTP 4 4020\nSL @ 4066",
            CH_IVAN,
            bridge_state,
        )
        sig = parser_ivan_vip("Rientri piccola da qui a 58", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry"] == 4058.0
        assert sig["lot_factor"] == 0.5
        assert sig["allow_stack"] is True
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_rientro_ripetuto_in_edit_non_riapre(self, bridge_state):
        """MSG 'a 58' + EDIT 'a 58.5' devono aprire una sola posizione."""
        parser_ivan_vip(
            "XAUUSD SELL 4054\nTP 1 4050\nSL @ 4066", CH_IVAN, bridge_state
        )
        assert parser_ivan_vip("Rientri piccola da qui a 58", CH_IVAN, bridge_state)
        assert parser_ivan_vip(
            "Rientri piccola da qui a 58.5", CH_IVAN, bridge_state
        ) is None

    def test_frase_di_attesa_non_apre(self, bridge_state):
        """Caso reale CH_IVAN 31/07 13:51Z: aveva aperto 4 posizioni a mercato."""
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(
            "Aspettiamo migliori conferme e poi rientriamo con calma🤝",
            CH_IVAN,
            bridge_state,
        ) is None

    @pytest.mark.parametrize(
        "msg",
        [
            "Se torna sui massimi rientriamo",
            "Quando rompe la resistenza rientriamo",
            "Magari più tardi rientriamo",
            "Rientreremo appena si calma",
            "Vediamo se rientriamo",
            "Pronti a rientrare",
            "Aspettiamo, poi entriamo anche noi",
        ],
    )
    def test_rientri_annunciati_non_aprono(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(msg, CH_IVAN, bridge_state) is None

    @pytest.mark.parametrize(
        "msg",
        [
            "Rientrate ora",
            "Rientriamo subito",
            "Rientri piccola da qui a 58",
            "Okay dentro anche da qui",
            "Rientriamo ora anche se il volume è basso",
        ],
    )
    def test_rientri_operativi_restano_validi(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip(msg, CH_IVAN, bridge_state)
        assert sig is not None, msg
        assert sig["action"] == "OPEN"
        assert sig["allow_stack"] is True

    @pytest.mark.parametrize(
        "msg",
        [
            # Casi reali CH_IVAN 06/08: avevano aperto 4+1 posizioni a mercato
            # riusando SL/TP del setup vecchio, richiuse subito dall'EA.
            "Se ritraxcia un minimo rientriamo anche qui in sala",
            "Se ritraccia rientriamo da qui",
            "Zona reentry 56-53",
            "Zona rientro 4256-4253",
            "Valuto un rientro da qui",
        ],
    )
    def test_annunci_condizionali_non_aprono(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(msg, CH_IVAN, bridge_state) is None, msg

    @pytest.mark.parametrize(
        "msg",
        [
            # Caso reale CH_IVAN 11/08 13:20Z: aveva aperto 4 posizioni a
            # mercato con lo SL ereditato a 4.75 di distanza, SL preso in 2'.
            "Vorrei rientrare sell ehhh",
            "Vorrei rientrare ora",
            "Volevo rientrare da qui",
            "Mi piacerebbe rientrare adesso",
            "Pensavo di rientrare qui",
            "Sarebbe bello rientrare da questi livelli",
        ],
    )
    def test_desiderativi_non_aprono(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(msg, CH_IVAN, bridge_state) is None, msg

    @pytest.mark.parametrize(
        "msg",
        [
            # Caso reale CH_IVAN 11/08 15:51Z: aveva chiuso il TP4 running.
            "E anche oggi chiudiamo in Profitto 🏌🏼‍♂️🏌🏼‍♂️",
            "Chiudiamo in profitto",
            "Chiudiamo la settimana in bellezza",
            "Anche oggi chiudo in verde",
        ],
    )
    def test_consuntivi_non_chiudono(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        assert parser_ivan_vip(msg, CH_IVAN, bridge_state) is None, msg

    @pytest.mark.parametrize(
        "msg",
        ["CHIUDIAMO ORA!", "USCIAMO ORA", "Chiudiamo tutto", "Chiudiamo ora in profitto"],
    )
    def test_comandi_di_chiusura_restano_validi(self, bridge_state, msg):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip(msg, CH_IVAN, bridge_state)
        assert sig is not None, msg
        assert sig["action"] == "CLOSE_ALL_SYMBOL", msg

    def test_chiudo_la_rientry_non_chiude_il_setup_base(self, bridge_state):
        """Caso reale CH_IVAN 06/08 15:50Z: aveva chiuso anche il segnale principale."""
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Chiudo la rientry", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "CLOSE_SELECTIVE"
        assert sig["keep"] == "ALL_BUT_NEWEST"
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_chiusura_totale_resta_totale(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Chiudiamo tutto, anche i rientri", CH_IVAN, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"

    def test_chiudiamo_questa_dopo_rientro_chiude_solo_il_rientro(self, bridge_state):
        """Caso reale CH_IVAN 09/09 12:52Z: chiuse anche il setup, che poi fece TP3."""
        parser_ivan_vip(
            "XAUUSD BUY 4398 |  | TP1: 4404 | TP2: 4407 | TP3: 4412 | TP4: 4450 |  | SL: 4386",
            CH_IVAN, bridge_state,
        )
        re_sig = parser_ivan_vip(
            "Rientriamo con piccola size qui da 4396", CH_IVAN, bridge_state
        )
        assert re_sig["action"] == "OPEN" and re_sig["allow_stack"]
        sig = parser_ivan_vip("Chiduamo questa", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "CLOSE_SELECTIVE"
        assert sig["keep"] == "ALL_BUT_NEWEST"
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_chiudiamo_questa_senza_rientro_resta_totale(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Chiudiamo questa", CH_IVAN, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"

    def test_chiudiamo_tutto_dopo_rientro_resta_totale(self, bridge_state):
        parser_ivan_vip(IVAN_SETUP, CH_IVAN, bridge_state)
        parser_ivan_vip("Rientriamo qui da 4060", CH_IVAN, bridge_state)
        sig = parser_ivan_vip("Chiudiamo tutto", CH_IVAN, bridge_state)
        assert sig["action"] == "CLOSE_ALL_SYMBOL"

    def test_zona_con_typo_viene_corretta(self, bridge_state):
        """Caso reale CH_IVAN 09/09 10:57Z: "4401-3399" scartata, setup mai aperto."""
        sig = parser_ivan_vip(
            "XAUUSD BUY 4401-3399 |  | TP1: 4404 | TP2: 4407 | TP3: 4412 | TP4: 4450 |  | SL: 4386",
            CH_IVAN, bridge_state,
        )
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["entry_range"] == [4399.0, 4401.0]
        assert sig["entry"] is None
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_zona_incoerente_non_viene_inventata(self, bridge_state):
        sig = parser_ivan_vip(
            "XAUUSD BUY 4401-3399 |  | TP1: 4404 |  | SL: 4386",
            CH_IVAN, bridge_state,
        )
        # SL/TP coerenti -> stessa correzione; con TP fuori scala non si tocca.
        assert sig["entry_range"] == [4399.0, 4401.0]
        sig2 = parser_ivan_vip(
            "XAUUSD BUY 4401-3399 |  | TP1: 5404 |  | SL: 4386",
            CH_IVAN, bridge_state,
        )
        assert sig2["entry_range"] == [3399.0, 4401.0]

    def test_spostiamo_lo_stop_a_prezzo(self, bridge_state):
        """Caso reale CH_IVAN 06/08 12:43Z: era UNPARSED, lo SL restava a 4250."""
        parser_ivan_vip(
            "XAUUSD BUY 4265\nTP 1 4270\nTP 2 4273\nSL @ 4250", CH_IVAN, bridge_state
        )
        sig = parser_ivan_vip(
            "Per chi non è rientrato da sotto. Spostiamo lo stop a 4255",
            CH_IVAN,
            bridge_state,
        )
        assert sig is not None
        assert sig["action"] == "UPDATE_SL"
        assert sig["new_sl"] == 4255.0
        assert sig["direction"] == "BUY"
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_entrata_aggiuntiva_a_mercato(self, bridge_state):
        """Caso reale CH_IVAN 31/07 12:10Z: 'Okay dentro anche da qui'."""
        parser_ivan_vip(
            "XAUUSD SELL 4054\nTP 1 4050\nSL @ 4065", CH_IVAN, bridge_state
        )
        sig = parser_ivan_vip("Okay dentro anche da qui", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["allow_stack"] is True

    def test_reentry_drops_levels_already_passed(self, bridge_state):
        parser_ivan_vip(
            "XAUUSD BUY 4028\nTP 1 4033\nTP 2 4037\nSL @ 4015", CH_IVAN, bridge_state
        )
        sig = parser_ivan_vip("Rientrare ora a 4035", CH_IVAN, bridge_state)
        assert sig["tp_levels"] == [4037.0]
        assert sig["sl"] == 4015.0
        ok, reason = validate_signal(sig)
        assert ok, reason

    def test_reentry_drops_sl_on_wrong_side(self, bridge_state):
        parser_ivan_vip(
            "XAUUSD BUY 4028\nTP 1 4033\nSL @ 4015", CH_IVAN, bridge_state
        )
        sig = parser_ivan_vip("Rientriamo a 4010", CH_IVAN, bridge_state)
        assert sig["sl"] is None
        ok, reason = validate_signal(sig)
        assert ok, reason


class TestStateWriteFailureDoesNotLoseSignal:
    """CH_ORO 29/07: PermissionError su bridge_state.json abortiva il segnale."""

    def test_oro_open_emitted_when_state_write_fails(self, bridge_state, monkeypatch):
        import bridge_core

        def boom(*args, **kwargs):
            raise PermissionError(5, "Access is denied")

        monkeypatch.setattr(bridge_core, "atomic_write_json", boom)
        sig = parser_sala_oro(
            "XAUUSD SELL 4047-4049\nTP 4040\nSL 4051", CH_ORO, bridge_state
        )
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4047.0, 4049.0]
        ok, reason = validate_signal(sig)
        assert ok, reason
        # lo stato resta comunque coerente in memoria
        assert bridge_state.oro_last_trade is not None

    def test_save_state_json_returns_false_instead_of_raising(self, tmp_path, monkeypatch):
        import bridge_core

        def boom(*args, **kwargs):
            raise PermissionError(5, "Access is denied")

        monkeypatch.setattr(bridge_core, "atomic_write_json", boom)
        assert bridge_core.save_state_json(tmp_path / "s.json", {"a": 1}, "test") is False

    def test_save_state_json_writes_file(self, tmp_path):
        import bridge_core

        path = tmp_path / "s.json"
        assert bridge_core.save_state_json(path, {"a": 1}, "test") is True
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


class TestIdenticalEditIsDeduped:
    """STARK 29/07: EDIT identico dopo SL → l'EA riapriva a 539 pts dal segnale."""

    def test_new_marks_identical_edit_as_duplicate(self, tmp_path):
        from bridge_core import ProcessedMessageStore

        store = ProcessedMessageStore(tmp_path / "processed.json")
        text = "Apro una nuova operazione\nXAUUSD SELL\nEntry: 4010\nSL: 4014.66\nTP1: 4007.96"
        key_new = ProcessedMessageStore.make_key(-100, 55, "NEW", text)
        key_edit = ProcessedMessageStore.make_key(-100, 55, "EDIT", text)

        store.mark_processed(key_new, key_edit)

        assert store.is_duplicate(key_new)
        assert store.is_duplicate(key_edit)
        # un EDIT che cambia i livelli resta processabile
        changed = text.replace("4010", "4020")
        assert not store.is_duplicate(
            ProcessedMessageStore.make_key(-100, 55, "EDIT", changed)
        )

    def test_alias_survives_reload(self, tmp_path):
        from bridge_core import ProcessedMessageStore

        path = tmp_path / "processed.json"
        store = ProcessedMessageStore(path)
        store.mark_processed("k:NEW", "k:EDIT:abc")
        assert ProcessedMessageStore(path).is_duplicate("k:EDIT:abc")


class TestOroFragmentsFromProduction:
    """Sequenze ORO reali del 29/07: pending → setup completo."""

    def test_entriamo_ora_sets_pending_then_full_setup_opens(self, bridge_state):
        assert parser_sala_oro("Entriamo ora buy 3996", CH_ORO, bridge_state) is None
        assert bridge_state.oro_pending_dir == "BUY"
        assert bridge_state.oro_pending_entry == 3996.0

        sig = parser_sala_oro(
            "XAUUSD BUY 3996-3995\nTP 4002\nSL 3991", CH_ORO, bridge_state
        )
        assert sig["action"] == "OPEN"
        assert sig["entry_range"] == [3995.0, 3996.0]
        assert sig["sl"] == 3991.0

    def test_standalone_sl_accumulates_on_pending(self, bridge_state):
        assert parser_sala_oro("4016 buy", CH_ORO, bridge_state) is None
        assert bridge_state.oro_pending_dir == "BUY"
        assert parser_sala_oro("Sl 4011", CH_ORO, bridge_state) is None
        assert bridge_state.oro_pending_sl == 4011.0

        sig = parser_sala_oro("TP 4024", CH_ORO, bridge_state)
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["sl"] == 4011.0
        assert sig["tp_levels"] == [4024.0]

    def test_zona_buy_after_range(self, bridge_state):
        assert parser_sala_oro("3998-3995 zona buy", CH_ORO, bridge_state) is None
        assert bridge_state.oro_pending_dir == "BUY"
        assert bridge_state.oro_pending_range == [3995.0, 3998.0]


# ─────────────────────────────────────────────────────────────────────────────
# GOLD: forme italiane e sinonimi (audit produzione 29-30/07/2026)
# ─────────────────────────────────────────────────────────────────────────────
class TestGoldItalianForms:
    def test_vendi_oro_adesso_naked(self, bridge_state: BridgeState):
        sig = parser_sala_gold("VENDI oro adesso", CH2, bridge_state)
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"
        assert bridge_state.ch2_pending_open is True

    @pytest.mark.parametrize(
        "text,direction",
        [
            ("Vendi oro ora", "SELL"),
            ("Vendo oro subito", "SELL"),
            ("Vendiamo oro adesso", "SELL"),
            ("Vendete oro a mercato", "SELL"),
            ("Short gold now", "SELL"),
            ("Compra oro adesso", "BUY"),
            ("Compro oro ora", "BUY"),
            ("Compriamo oro subito", "BUY"),
            ("Comprate oro a mercato", "BUY"),
            ("Acquistiamo oro adesso", "BUY"),
            ("Long gold now", "BUY"),
            ("Vendi XAUUSD adesso", "SELL"),
            ("Buy xau/usd now", "BUY"),
        ],
    )
    def test_naked_open_synonyms(self, bridge_state: BridgeState, text, direction):
        sig = parser_sala_gold(text, CH2, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == direction
        ok, err = validate_signal(sig)
        assert ok, err

    def test_english_naked_still_works(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Gold sell now", CH2, bridge_state)
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"

    def test_italian_setup_completes_naked(self, bridge_state: BridgeState):
        parser_sala_gold("VENDI oro adesso", CH2, bridge_state)
        sig = parser_sala_gold(
            "VENDI oro ora 4063 - 4070\nSL:4078\nTP: 4055\nTP: 4040",
            CH2,
            bridge_state,
        )
        assert sig["action"] == "UPDATE_OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4063.0, 4070.0]
        assert sig["sl"] == 4078.0
        assert sig["tp_levels"] == [4055.0, 4040.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_italian_setup_standalone(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "VENDI oro ora 4067 - 4077\nSL:4082\nTP: 4060\nTP: 4040",
            CH2,
            bridge_state,
        )
        assert sig["action"] == "OPEN"
        assert sig["entry_range"] == [4067.0, 4077.0]
        assert sig["sl"] == 4082.0
        assert sig["tp_levels"] == [4060.0, 4040.0]

    def test_buy_setup_italian(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "COMPRA oro ora 4063 - 4070\nSL: 4055\nTP: 4080", CH2, bridge_state
        )
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["sl"] == 4055.0
        assert sig["tp_levels"] == [4080.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_setup_without_asset_word(self, bridge_state: BridgeState):
        """Canale mono-asset: direzione + prezzo + SL + TP bastano."""
        sig = parser_sala_gold(
            "Compriamo ora 4063 - 4070 SL 4055 TP 4080", CH2, bridge_state
        )
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "BUY"
        assert sig["entry_range"] == [4063.0, 4070.0]
        assert sig["sl"] == 4055.0
        assert sig["tp_levels"] == [4080.0]

    @pytest.mark.parametrize(
        "text",
        [
            "Vendi stop oro 4065",
            "SELL stop oro 4065",
            "Buy limit oro 4050",
            "Sell limit gold 4090",
            "Stop sell oro 4065",
        ],
    )
    def test_pending_orders_never_open_at_market(self, bridge_state: BridgeState, text):
        assert parser_sala_gold(text, CH2, bridge_state) is None, text

    @pytest.mark.parametrize(
        "text",
        [
            "BE HIT",
            "TP1 HIT + 70 PIPS 🔥",
            "SL HIT",
            "📢 Comunicazione importante per tutti i possessori di un Conto Finanziato",
            "Tramite questa guida sara’ possibile aprire la dashboard",
        ],
    )
    def test_informational_messages_ignored(self, bridge_state: BridgeState, text):
        assert parser_sala_gold(text, CH2, bridge_state) is None, text


class TestGoldManagementSynonyms:
    def test_partial_be_with_pips_prefix(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "+120 pip chiusura parziale in pareggio", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_partial_be_italian_plain(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Chiusura parziale in pareggio", CH2, bridge_state)
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_partial_be_break_wording(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Rompere anche la chiusura parziale", CH2, bridge_state)
        assert sig["action"] == "CLOSE_HALF_BE"

    def test_be_and_partial_italian(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Mettere a break even  e chiusura parziale", CH2, bridge_state
        )
        assert sig["action"] == "CLOSE_HALF_BE"

    @pytest.mark.parametrize(
        "text", ["Pareggiare", "Mettiamo in pareggio", "Portiamo lo SL a pareggio"]
    )
    def test_break_even_italian(self, bridge_state: BridgeState, text):
        sig = parser_sala_gold(text, CH2, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "CHECK_AND_BE"

    def test_break_even_with_price_italian(self, bridge_state: BridgeState):
        sig = parser_sala_gold("4074 pareggio in oro", CH2, bridge_state)
        assert sig["action"] == "BREAK_EVEN_PRICE"
        assert sig["be_price"] == 4074.0

    def test_identical_command_not_repeated_within_ttl(self, bridge_state: BridgeState):
        first = parser_sala_gold("Chiusura parziale in pareggio", CH2, bridge_state)
        assert first["action"] == "CLOSE_HALF_BE"
        # stesso comando tradotto in inglese subito dopo (EDIT del canale)
        assert (
            parser_sala_gold("Partial close break Even +100 pips", CH2, bridge_state)
            is None
        )

    def test_same_command_emitted_again_after_ttl(self, bridge_state: BridgeState):
        first = parser_sala_gold("Chiusura parziale in pareggio", CH2, bridge_state)
        assert first["action"] == "CLOSE_HALF_BE"
        last = dict(bridge_state.gold_last_cmd or {})
        last["ts"] = last["ts"] - (GOLD_CMD_DEDUP_TTL_SEC + 1)
        bridge_state.gold_last_cmd = last

        again = parser_sala_gold("Partial close break even +60 pips", CH2, bridge_state)
        assert again is not None
        assert again["action"] == "CLOSE_HALF_BE"

    def test_same_setup_reposted_in_english_is_not_a_second_trade(
        self, bridge_state: BridgeState
    ):
        first = parser_sala_gold(
            "VENDI oro ora 4076 - 4086\nSL:4091\nTP: 4069\nTP: 4040",
            CH2,
            bridge_state,
        )
        assert first["action"] == "OPEN"
        assert (
            parser_sala_gold(
                "SELL gold now 4076.8 - 4086\nSL: 4091\nTP: 4069\nTP: 4040",
                CH2,
                bridge_state,
            )
            is None
        )


class TestCloseSynonymsWidening:
    @pytest.mark.parametrize(
        "text",
        [
            "USCIAMO",
            "Usciamo ora",
            "USCITE",
            "Uscite subito",
            "Usciamo qui a 5054 -40 PIPS",
            "ESCO",
            "Esco ora",
            "Esciamo",
            "Esciamo tutti",
            "Esci adesso",
            "CHIUDIAMO",
            "Chiudiamo ora",
            "CHIUDO",
            "Chiudete ora",
            "Chiudi tutto",
            "Chiudiamo tutte le posizioni",
            "Liquidiamo tutto",
            "Chiudiamo a mercato",
            "CLOSE ALL",
            "Close everything now",
            "EXIT ALL",
            "Closing all positions",
            "CHIDUAMO ORA",
        ],
    )
    def test_close_variants_detected(self, text):
        ok, _ = match_close_all_intent(text.upper())
        assert ok is True, text

    @pytest.mark.parametrize(
        "text",
        [
            "pronti a chiudere ragazzi",
            "potremmo chiudere a breve",
            "gestiamo a mercato",
            "Se rompe chiudiamo il trade a mercato senza pensarci",
            "chiudiamo la settimana con un ottimo risultato in profitto",
            "domani chiuderemo la sala prima",
        ],
    )
    def test_narrative_close_not_operational(self, text):
        ok, _ = match_close_all_intent(text.upper())
        assert ok is False, text

    def test_close_price_still_extracted(self):
        ok, px = match_close_all_intent("CHIUDIAMO ORA 4073")
        assert ok is True
        assert px == 4073.0


# ─────────────────────────────────────────────────────────────────────────────
# FOREX: varianti IT/EN aggiunte (canale bot Sala VIP)
# ─────────────────────────────────────────────────────────────────────────────
class TestForexWidening:
    def test_english_flow_open_then_update(self, bridge_state: BridgeState):
        opened = parser_sala_vip(
            "NEW ORDER - GBPUSDpm Sell 📉\n\nEntry: 1.33852 [Lots: 0.02]\nNo SL\nNo TP",
            CH3,
            bridge_state,
        )
        assert opened["action"] == "OPEN"
        assert opened["symbol"] == "GBPUSD"
        assert opened["direction"] == "SELL"
        assert opened["entry"] == 1.33852
        assert opened["tp_levels"] == []
        assert opened["sl"] is None
        ok, err = validate_signal(opened)
        assert ok, err
        sig = parser_sala_vip(
            "🛠️ GBPUSDpm Sell - Modified\n\n--------{ Set TP }---------\n"
            "🗑️ Old TP: 0.00000\n👉 New TP: 1.33700",
            CH3,
            bridge_state,
        )
        assert sig["action"] == "UPDATE_TP"
        assert sig["symbol"] == "GBPUSD"
        assert sig["new_tp"] == 1.337

    def test_italian_flow(self, bridge_state: BridgeState):
        opened = parser_sala_vip(
            "NUOVO ORDINE - EURUSDpm Compra\n\nEntrata: 1.10000", CH3, bridge_state
        )
        assert opened["action"] == "OPEN"
        assert opened["direction"] == "BUY"
        assert opened["symbol"] == "EURUSD"
        ok, err = validate_signal(opened)
        assert ok, err
        sig = parser_sala_vip(
            "EURUSDpm Compra - Modificato\n\nNuovo TP: 1.11000", CH3, bridge_state
        )
        assert sig["action"] == "UPDATE_TP"
        assert sig["tp_levels"] == [1.11]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_new_order_with_levels_opens_complete(self, bridge_state: BridgeState):
        sig = parser_sala_vip(
            "NEW ORDER - EURUSDpm Sell\nEntry: 1.15000\nSL: 1.15400\nTP: 1.14000",
            CH3,
            bridge_state,
        )
        assert sig["action"] == "OPEN"
        assert sig["sl"] == 1.154
        assert sig["tp_levels"] == [1.14]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_new_order_zero_levels_are_naked(self, bridge_state: BridgeState):
        sig = parser_sala_vip(
            "NEW ORDER - EURUSDpm Sell\nEntry: 1.15000\nSL: 0.00000\nTP: 0.00000",
            CH3,
            bridge_state,
        )
        assert sig["action"] == "OPEN"
        assert sig["sl"] is None
        assert sig["tp_levels"] == []

    def test_take_profit_label(self, bridge_state: BridgeState):
        parser_sala_vip("NEW ORDER - EURUSDpm Sell\nEntry: 1.15000", CH3, bridge_state)
        sig = parser_sala_vip(
            "EURUSDpm Sell - Updated\nNew Take Profit: 1.14000", CH3, bridge_state
        )
        assert sig["action"] == "UPDATE_TP"
        assert sig["tp_levels"] == [1.14]

    def test_sl_only_modified_updates_open_trade(self, bridge_state: BridgeState):
        parser_sala_vip("NEW ORDER - EURUSDpm Sell\nEntry: 1.15000", CH3, bridge_state)
        sig = parser_sala_vip(
            "EURUSDpm Sell - Modified\nNew SL: 1.15400", CH3, bridge_state
        )
        assert sig["action"] == "UPDATE_SL"
        assert sig["new_sl"] == 1.154
        ok, err = validate_signal(sig)
        assert ok, err

    def test_tp_and_sl_together_on_open_trade(self, bridge_state: BridgeState):
        parser_sala_vip("NEW ORDER - XAUUSDpm Sell\nEntry: 4076.31", CH3, bridge_state)
        moved = parser_sala_vip(
            "XAUUSDpm Sell - Modified\n-----{ Moved SL & TP }-----\n"
            "👉 New SL: 4091.00\n👉 New TP: 4050.00",
            CH3,
            bridge_state,
        )
        assert moved["action"] == "UPDATE_OPEN"

        sig = parser_sala_vip(
            "XAUUSDpm Sell - Modified\n👉 New SL: 4085.00\n👉 New TP: 4045.00",
            CH3,
            bridge_state,
        )
        assert sig["action"] == "UPDATE_OPEN"
        assert sig["sl"] == 4085.0
        assert sig["tp_levels"] == [4045.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_update_tp_without_known_trade(self, bridge_state: BridgeState):
        sig = parser_sala_vip(
            "AUDJPYpm Sell - Modified\nNew TP: 113.370", CH3, bridge_state
        )
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == 113.37

    def test_update_sl_without_known_trade(self, bridge_state: BridgeState):
        sig = parser_sala_vip(
            "AUDJPYpm Sell - Modificato\nNuovo SL: 113.900", CH3, bridge_state
        )
        assert sig["action"] == "UPDATE_SL"
        assert sig["new_sl"] == 113.9

    def test_close_english(self, bridge_state: BridgeState):
        sig = parser_sala_vip(
            "🟠 CLOSED - XAUUSDpm Sell 🟠\n\nEntry:  4076.31", CH3, bridge_state
        )
        assert sig["action"] == "CHECK_AND_CLOSE"
        assert sig["symbol"] == "XAUUSD"
        assert sig["direction"] == "SELL"

    @pytest.mark.parametrize(
        "text",
        [
            "CHIUSO - EURUSDpm Sell",
            "CHIUSA - EURUSDpm Sell",
            "EURUSDpm Sell - CHIUSO",
            "EURUSDpm Vendi - Chiuso",
            "CLOSE - EURUSDpm Sell",
            "EXIT - EURUSDpm Sell",
        ],
    )
    def test_close_variants(self, bridge_state: BridgeState, text):
        sig = parser_sala_vip(text, CH3, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "CHECK_AND_CLOSE"
        assert sig["symbol"] == "EURUSD"
        assert sig["direction"] == "SELL"

    def test_close_clears_pending(self, bridge_state: BridgeState):
        parser_sala_vip("NEW ORDER - EURUSDpm Sell\nEntry: 1.15000", CH3, bridge_state)
        parser_sala_vip("CLOSED - EURUSDpm Sell", CH3, bridge_state)
        assert bridge_state.forex_pending_symbol is None

    def test_promo_message_ignored(self, bridge_state: BridgeState):
        assert parser_sala_vip(
            "📢 Comunicazione importante per tutti i possessori di un Conto Finanziato",
            CH3,
            bridge_state,
        ) is None


class TestGoldTranslatedFormsFrom0812:
    """Setup GOLD del 12/08 finiti UNPARSED: traduzioni EN/ES, "Typ", zone."""

    def test_gold_on_sale_now_is_a_sell_setup(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Gold on sale now: 4407 - 4417 SL: 4422 Typ: 4399 Typ: 4370",
            CH2,
            bridge_state,
        )
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4407.0, 4417.0]
        assert sig["tp_levels"] == [4399.0, 4370.0]
        assert sig["sl"] == 4422.0
        ok, err = validate_signal(sig)
        assert ok, err

    def test_spanish_a_la_venta_ahora(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Oro a la venta ahora: 4408 - 4417 SL: 4422 Tp: 4399.8 Tp: 4370",
            CH2,
            bridge_state,
        )
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4408.0, 4417.0]
        assert sig["tp_levels"] == [4399.8, 4370.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_direction_inferred_when_translation_drops_it(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Precio actual del oro: 4414,8 - 4422 SL: 4429 Tp: 4405 Tp: 4380",
            CH2,
            bridge_state,
        )
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4414.8, 4422.0]
        assert sig["sl"] == 4429.0
        ok, err = validate_signal(sig)
        assert ok, err

    def test_zone_label_does_not_hide_the_entry_range(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "BUY XAUUSD ZONE 4425 - 4422 SL: 4418 TP1 4429 TP2 4439",
            CH2,
            bridge_state,
        )
        assert sig["direction"] == "BUY"
        assert sig["entry_range"] == [4422.0, 4425.0]
        assert sig["tp_levels"] == [4429.0, 4439.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_colon_after_now_does_not_hide_the_entry_range(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Buy gold now: 4419 - 4410 SL: 4401 Typ: 4428 Tp: 4450",
            CH2,
            bridge_state,
        )
        assert sig["direction"] == "BUY"
        assert sig["entry_range"] == [4410.0, 4419.0]
        assert sig["tp_levels"] == [4428.0, 4450.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_typ_does_not_break_existing_english_form(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Gold sell now: 4407 - 4417 SL: 4422 Typ: 4399 Tp: 4370",
            CH2,
            bridge_state,
        )
        assert sig["tp_levels"] == [4399.0, 4370.0]
        assert sig["entry_range"] == [4407.0, 4417.0]

    def test_comment_without_levels_still_ignored(self, bridge_state: BridgeState):
        assert parser_sala_gold(
            "Il prezzo attuale dell'oro è interessante, restiamo a guardare",
            CH2,
            bridge_state,
        ) is None

    def test_naked_open_still_works(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Gold sell now", CH2, bridge_state)
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"

    def test_decorative_arrows_do_not_hide_levels(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "\U0001f48e COMPRAR XAUUSD \U0001f947\n\nZONA \u27a1\ufe0f 4425 - 4422\n\n"
            "SL: 4418\nTP1 \u27a1\ufe0f 4429\nTP2 \u27a1\ufe0f 4439",
            CH2,
            bridge_state,
        )
        assert sig["direction"] == "BUY"
        assert sig["entry_range"] == [4422.0, 4425.0]
        assert sig["tp_levels"] == [4429.0, 4439.0]
        assert sig["sl"] == 4418.0

    def test_translated_edit_of_an_emitted_setup_is_not_reemitted(self, bridge_state: BridgeState):
        first = parser_sala_gold(
            "BUY XAUUSD \U0001f947\n\nZONE 4425 - 4422\n\nSL: 4418\nTP1 4429\nTP2 4439",
            CH2,
            bridge_state,
        )
        assert first["action"] == "OPEN"
        # Stesso setup ripubblicato tradotto e con le frecce: nessun secondo OPEN.
        assert parser_sala_gold(
            "\U0001f48e COMPRAR XAUUSD \U0001f947\n\nZONA \u27a1\ufe0f 4425 - 4422\n\n"
            "SL: 4418\nTP1 \u27a1\ufe0f 4429\nTP2 \u27a1\ufe0f 4439",
            CH2,
            bridge_state,
        ) is None


class TestIvanEntryTypoFrom0812:
    """L'entry sbagliata di battitura non deve costare il segnale."""

    TEXT_TYPO = "XAUUSD SELL 4326\n\nTP 1 4420\nTP 2 4417\nTP 3 4412\nTP 4 4408\n\nSL @ 4436"
    TEXT_OK = "XAUUSD SELL 4427\n\nTP 1 4420\nTP 2 4417\nTP 3 4412\nTP 4 4408\n\nSL @ 4436"

    def test_typo_entry_becomes_market_open(self, bridge_state: BridgeState):
        sig = parser_ivan_vip(self.TEXT_TYPO, CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry"] is None
        assert sig["tp_levels"] == [4420.0, 4417.0, 4412.0, 4408.0]
        assert sig["sl"] == 4436.0
        ok, err = validate_signal(sig)
        assert ok, err

    def test_coherent_entry_is_preserved(self, bridge_state: BridgeState):
        sig = parser_ivan_vip(self.TEXT_OK, CH_IVAN, bridge_state)
        assert sig["entry"] == 4427.0

    def test_out_of_scale_setup_is_not_rescued(self, bridge_state: BridgeState):
        # SL e TP su lati opposti ma forchetta assurda: resta scartato dal
        # validatore, non si apre a mercato su livelli inservibili.
        sig = parser_ivan_vip(
            "XAUUSD BUY 4400\n\nTP 1 5000\n\nSL @ 4000",
            CH_IVAN,
            bridge_state,
        )
        assert sig is not None
        assert sig["entry"] == 4400.0


class TestIvanUpdateTpFrom0817:
    """17/08: "Spostiamo TP 4 a 4376" era UNPARSED e il TP4 restava a 4375."""

    SETUP = (
        "XAUUSD SELL 4394\n\n"
        "TP 1 4391\nTP 2 4388\nTP 3 4385\nTP 4 4375\n\nSL @ 4408"
    )

    def _open(self, state: BridgeState) -> dict:
        sig = parser_ivan_vip(self.SETUP, CH_IVAN, state)
        assert sig["action"] == "OPEN"
        return sig

    def test_move_tp4_real_message(self, bridge_state: BridgeState):
        self._open(bridge_state)
        sig = parser_ivan_vip("Spostiamo TP 4 a 4376", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "UPDATE_TP"
        assert sig["direction"] == "SELL"
        assert sig["symbol"] == "XAUUSD"
        assert sig["new_tp"] == 4376.0
        assert sig["tp_index"] == 4
        assert sig["magic_base"] == 17000
        ok, err = validate_signal(sig)
        assert ok, err

    def test_only_the_named_tp_changes_in_state(self, bridge_state: BridgeState):
        self._open(bridge_state)
        parser_ivan_vip("Spostiamo TP 4 a 4376", CH_IVAN, bridge_state)
        last = bridge_state.ivan_last_trade
        assert last["tp_levels"] == [4391.0, 4388.0, 4385.0, 4376.0]
        assert last["entry"] == 4394.0
        assert last["sl"] == 4408.0

    @pytest.mark.parametrize("text,expected_tp,expected_idx", [
        ("Spostiamo TP 4 a 4376", 4376.0, 4),
        ("Spostiamo il TP 4 a 4376", 4376.0, 4),
        ("Sposto TP4 a 4376", 4376.0, 4),
        ("Modifichiamo TP 2 a 4386.5", 4386.5, 2),
        ("Portiamo il tp3 a 4384,5", 4384.5, 3),
        ("Cambiamo TP 1 4390", 4390.0, 1),
        ("Move TP 3 to 4383", 4383.0, 3),
        ("TP 4 a 4376", 4376.0, 4),
        # "take profit" da solo resta un commento ignorato: serve il verbo
        ("Spostiamo il take profit 4 a 4376", 4376.0, 4),
        # senza indice: il livello vale per tutte le posizioni del segnale
        ("Spostiamo i TP a 4390", 4390.0, None),
        # abbreviazione del canale: "76" con entry 4394 → 4376
        ("Spostiamo TP 4 a 76", 4376.0, 4),
    ])
    def test_variants(self, bridge_state: BridgeState, text: str,
                      expected_tp: float, expected_idx: int | None):
        self._open(bridge_state)
        sig = parser_ivan_vip(text, CH_IVAN, bridge_state)
        assert sig is not None, text
        assert sig["action"] == "UPDATE_TP"
        assert sig["new_tp"] == expected_tp
        assert sig.get("tp_index") == expected_idx

    def test_no_index_replaces_the_level_for_all(self, bridge_state: BridgeState):
        self._open(bridge_state)
        sig = parser_ivan_vip("Spostiamo i TP a 4390", CH_IVAN, bridge_state)
        assert "tp_index" not in sig
        assert bridge_state.ivan_last_trade["tp_levels"] == [4390.0]

    def test_tp_on_the_wrong_side_is_ignored(self, bridge_state: BridgeState):
        # Typo tipo "4488" su un SELL entrato a 4394: applicarlo chiuderebbe
        # la posizione a mercato in perdita.
        self._open(bridge_state)
        assert parser_ivan_vip("Spostiamo TP 4 a 4488", CH_IVAN, bridge_state) is None
        assert bridge_state.ivan_last_trade["tp_levels"] == [
            4391.0, 4388.0, 4385.0, 4375.0,
        ]

    @pytest.mark.parametrize("text", [
        "TP 3 HIT ✅ +100 PIPS",
        "Siamo vicini al TP 4, restiamo dentro",
        "Il TP 4 di ieri era a 4376 e lo abbiamo preso in pieno senza problemi",
    ])
    def test_informative_messages_do_not_move_tp(self, bridge_state: BridgeState,
                                                 text: str):
        self._open(bridge_state)
        sig = parser_ivan_vip(text, CH_IVAN, bridge_state)
        assert sig is None or sig["action"] != "UPDATE_TP"

    def test_update_tp_never_opens(self, bridge_state: BridgeState):
        # Senza setup precedente il comando non apre nulla.
        sig = parser_ivan_vip("Spostiamo TP 4 a 4376", CH_IVAN, bridge_state)
        assert sig is not None
        assert sig["action"] == "UPDATE_TP"
        assert "entry" not in sig
        assert bridge_state.ivan_last_trade is None

    def test_setup_message_is_still_an_open(self, bridge_state: BridgeState):
        # Un setup che contiene "TP 4 4375" resta un OPEN, non un UPDATE_TP.
        sig = self._open(bridge_state)
        assert sig["tp_levels"] == [4391.0, 4388.0, 4385.0, 4375.0]

    def test_edit_repeats_the_same_command(self, bridge_state: BridgeState):
        # NewMessage + MessageEdited identici: il secondo riemette lo stesso
        # UPDATE_TP (idempotente sull'EA), mai un'apertura.
        self._open(bridge_state)
        first = parser_ivan_vip("Spostiamo TP 4 a 4376", CH_IVAN, bridge_state)
        second = parser_ivan_vip("Spostiamo TP 4 a 4376", CH_IVAN, bridge_state)
        assert second["action"] == "UPDATE_TP"
        assert second["new_tp"] == first["new_tp"] == 4376.0
        assert bridge_state.ivan_last_trade["tp_levels"][3] == 4376.0


class TestGoldNakedAndSalvageFrom0814:
    """Sequenza reale CH_GOLD del 14/08: naked, update col typo nella zona, edit."""

    def test_go_sell_now_gold_is_a_naked_open(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Go sell now gold !", CH2, bridge_state)
        assert sig is not None
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "SELL"
        ok, err = validate_signal(sig)
        assert ok, err

    def test_go_buy_now_gold_is_a_naked_open(self, bridge_state: BridgeState):
        sig = parser_sala_gold("Go buy now gold", CH2, bridge_state)
        assert sig["action"] == "OPEN_NOW"
        assert sig["direction"] == "BUY"

    def test_naked_edit_with_price_only_keeps_waiting(self, bridge_state: BridgeState):
        assert parser_sala_gold("Go sell now gold !", CH2, bridge_state)["action"] == "OPEN_NOW"
        # L'edit aggiunge solo il prezzo: nessun livello da applicare.
        assert parser_sala_gold("Go sell now gold ! 4357", CH2, bridge_state) is None
        assert bridge_state.ch2_pending_open
        # Il setup completo che arriva dopo resta un UPDATE_OPEN.
        sig = parser_sala_gold(
            "Gold sell now 4342 - 4350 | SL: 4357 | Tp. 4332 | Tp: 4300",
            CH2,
            bridge_state,
        )
        assert sig["action"] == "UPDATE_OPEN"
        assert sig["sl"] == 4357.0

    def test_go_sell_now_gold_with_levels_is_a_setup(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Go sell now gold 4342 - 4350 SL: 4357 Tp: 4332", CH2, bridge_state
        )
        assert sig["action"] == "OPEN"
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4342.0, 4350.0]
        assert sig["sl"] == 4357.0

    def test_french_setup_is_parsed(self, bridge_state: BridgeState):
        sig = parser_sala_gold(
            "Or en vente 4342 - 4350 SL: 4357 Tp: 4332 Tp: 4300", CH2, bridge_state
        )
        assert sig["direction"] == "SELL"
        assert sig["entry_range"] == [4342.0, 4350.0]
        assert sig["tp_levels"] == [4332.0, 4300.0]

    def test_french_status_message_opens_nothing(self, bridge_state: BridgeState):
        assert parser_sala_gold("Tjrs en vente ici", CH2, bridge_state) is None
        assert parser_sala_gold("Still on sale here", CH2, bridge_state) is None

    def test_typo_zone_is_salvaged_as_levels_only(self, bridge_state: BridgeState):
        parser_sala_gold("Gold sell now", CH2, bridge_state)
        # Zona con 4450 invece di 4350: SL 4357 cadrebbe dentro la zona.
        sig = parser_sala_gold(
            "Gold sell now 4342  - 4450 | SL: 4357 | Tp. 4332 | Tp: 4300",
            CH2,
            bridge_state,
        )
        assert sig["action"] == "UPDATE_OPEN"
        assert sig["entry_range"] == [4342.0, 4450.0]
        ok, reason = validate_signal(sig)
        assert not ok
        salvaged = salvage_incoherent_entry_range(sig, reason)
        assert salvaged
        assert sig["entry_range"] is None
        assert sig["levels_only"] is True
        assert sig["sl"] == 4357.0
        assert sig["tp_levels"] == [4332.0, 4300.0]
        ok, err = validate_signal(sig)
        assert ok, err

    def test_salvage_refuses_incoherent_levels(self):
        # SL e TP dallo stesso lato: i livelli non sono usabili, resta scartato.
        sig = {
            "action": "UPDATE_OPEN",
            "direction": "SELL",
            "symbol": "XAUUSD",
            "entry_range": [4342.0, 4450.0],
            "sl": 4357.0,
            "tp_levels": [4360.0],
            "magic_base": 12000,
        }
        ok, reason = validate_signal(sig)
        assert not ok
        assert salvage_incoherent_entry_range(sig, reason) is None
        assert sig["entry_range"] == [4342.0, 4450.0]
        assert "levels_only" not in sig

    def test_salvage_only_for_update_open(self):
        sig = {
            "action": "OPEN",
            "direction": "SELL",
            "symbol": "XAUUSD",
            "entry_range": [4342.0, 4450.0],
            "sl": 4357.0,
            "tp_levels": [4332.0, 4300.0],
            "magic_base": 12000,
        }
        ok, reason = validate_signal(sig)
        assert not ok
        # Un OPEN senza zona utilizzabile non deve aprire a mercato.
        assert salvage_incoherent_entry_range(sig, reason) is None


class TestPayloadForEaFrom0831:
    """Segnali IVAN del 31/08 annullati dall'EA: `"entry_range": null` nel payload.

    Il lettore JSON dell'EA cercava la chiave e poi il primo `[` del file, che con
    entry_range nullo era `tp_levels`: TP1/TP2 diventavano la zona d'ingresso e il
    setup veniva cancellato per distanza (log: range=[4422,4425] su entry 4429).
    """

    def test_null_entry_range_is_not_written(self, tmp_path: Path):
        state = BridgeState(tmp_path / "st.json")
        sig = parser_ivan_vip(
            "XAUUSD SELL 4429\n\nTP1: 4425\nTP2: 4422\nTP3: 4420\nTP4: 4410\n\nSL: 4442",
            CH_IVAN,
            state,
        )
        assert sig["entry"] == 4429.0
        assert sig["entry_range"] is None
        payload = payload_for_ea(sig)
        assert "entry_range" not in payload
        assert payload["tp_levels"] == [4425.0, 4422.0, 4420.0, 4410.0]

    def test_a_real_entry_range_survives(self, tmp_path: Path):
        state = BridgeState(tmp_path / "st.json")
        sig = parser_ivan_vip(
            "XAUUSD BUY: 4591-4587 | TP1: 4596 | TP2: 4600 | SL: 4570",
            CH_IVAN,
            state,
        )
        assert payload_for_ea(sig)["entry_range"] == [4587.0, 4591.0]

    def test_only_the_none_fields_are_dropped(self):
        sig = {"action": "OPEN", "entry": None, "sl": 4442.0, "tp_levels": [4425.0],
               "levels_only": False, "trades": 0}
        payload = payload_for_ea(sig)
        assert payload == {"action": "OPEN", "sl": 4442.0, "tp_levels": [4425.0],
                           "levels_only": False, "trades": 0}
