"""Test della traduzione segnale TradinGo -> ordini DXtrade.

I payload usati sono quelli reali emessi dal bridge 2.15 il 3 agosto 2026
(journal ``events_20260803.jsonl``), così i test coprono le forme di segnale
che circolano davvero.
"""

from __future__ import annotations

import pytest

from dxtrade_mapper import (
    DEFAULT_SYMBOL_MAP,
    STATUS_CANCELLED,
    STATUS_DIRECT,
    STATUS_IN_RANGE,
    STATUS_OPEN_NOW,
    STATUS_TOLERANCE,
    InstrumentMeta,
    MappingError,
    build_close_position,
    evaluate_entry,
    lots_to_units,
    map_symbol,
    metadata_for,
    opposite,
    order_code,
    plan_close,
    plan_open,
    plan_update_open,
    select_positions,
)

XAU = InstrumentMeta(
    symbol="XAU/USD", lot_size=100, price_increment=0.01, quantity_increment=1
)
EUR = InstrumentMeta(
    symbol="EUR/USD", lot_size=100000, price_increment=0.00001, quantity_increment=1000
)

# CH_GOLD OPEN_NOW 32cf923e6b — 2 ticket da 0.10 (comportamento bridge 2.15)
GOLD_OPEN_NOW = {
    "action": "OPEN_NOW",
    "direction": "SELL",
    "symbol": "XAUUSD",
    "entry": None,
    "entry_range": None,
    "tp_levels": [],
    "sl": None,
    "magic_base": 12000,
    "channel_id": "CH_GOLD",
    "signal_id": "32cf923e6b",
    "use_fixed_lot": True,
    "trades": 2,
    "fixed_lot": 0.1,
}

# CH_GOLD UPDATE_OPEN 0448ae351d — livelli che arrivano dopo l'OPEN_NOW
GOLD_UPDATE_OPEN = {
    "action": "UPDATE_OPEN",
    "direction": "SELL",
    "symbol": "XAUUSD",
    "entry": None,
    "entry_range": [4059.0, 4066.0],
    "tp_levels": [4052.0, 4035.0],
    "sl": 4076.0,
    "magic_base": 12000,
    "channel_id": "CH_GOLD",
    "signal_id": "0448ae351d",
    "use_fixed_lot": True,
    "trades": 2,
    "fixed_lot": 0.1,
}

# CH_IVAN OPEN 00ff1ab5dd — 4 TP, il segnale più redditizio della giornata
IVAN_OPEN = {
    "action": "OPEN",
    "direction": "SELL",
    "symbol": "XAUUSD",
    "entry": 4059.0,
    "tp_levels": [4055.0, 4052.0, 4045.0, 4020.0],
    "sl": 4071.0,
    "magic_base": 17000,
    "channel_id": "CH_IVAN",
    "signal_id": "00ff1ab5dd",
    "use_fixed_lot": True,
    "trades": 4,
    "fixed_lot": 0.1,
}


# ── simboli e quantità ────────────────────────────────────────────────────


def test_map_symbol_uses_dxtrade_naming():
    assert map_symbol("XAUUSD") == "XAU/USD"
    assert map_symbol("eurusd") == "EUR/USD"
    assert DEFAULT_SYMBOL_MAP["XAGUSD"] == "XAG/USD"


def test_map_symbol_override_wins():
    assert map_symbol("XAUUSD", {"XAUUSD": "GOLD"}) == "GOLD"


def test_map_symbol_unknown_passes_through():
    assert map_symbol("US30") == "US30"


def test_map_symbol_empty_raises():
    with pytest.raises(MappingError):
        map_symbol("")


def test_lots_to_units_gold():
    assert lots_to_units(0.10, XAU) == 10
    assert lots_to_units(0.20, XAU) == 20
    assert lots_to_units(0.01, XAU) == 1


def test_lots_to_units_forex_snaps_to_increment():
    assert lots_to_units(0.01, EUR) == 1000
    assert lots_to_units(0.20, EUR) == 20000


def test_lots_to_units_rejects_zero_and_negative():
    for bad in (0, -0.1, None):
        with pytest.raises(MappingError):
            lots_to_units(bad, XAU)


def test_lots_to_units_rejects_quantity_rounded_to_zero():
    tiny = InstrumentMeta(symbol="X", lot_size=1, price_increment=0.01, quantity_increment=1000)
    with pytest.raises(MappingError):
        lots_to_units(0.01, tiny)


# ── identità ──────────────────────────────────────────────────────────────


def test_order_code_is_deterministic_and_unique_per_ticket():
    first = order_code("CH_GOLD", "32cf923e6b", 1)
    assert first == "TG-GOLD-32cf923e6b-T1O"
    assert first == order_code("CH_GOLD", "32cf923e6b", 1)
    assert first != order_code("CH_GOLD", "32cf923e6b", 2)
    assert first != order_code("CH_ORO", "32cf923e6b", 1)


def test_order_code_sanitizes_unsafe_characters():
    assert order_code("CH_GOLD/1", "a b:c", 1) == "TG-GOLD-1-a-b-c-T1O"


def test_metadata_carries_channel_signal_and_tp():
    meta = metadata_for("CH_IVAN", "00ff1ab5dd", 3, bridge_version="2.15")
    assert meta == {
        "tg_channel": "CH_IVAN",
        "tg_signal": "00ff1ab5dd",
        "tg_tp": "3",
        "tg_bridge": "2.15",
    }


def test_metadata_truncates_long_values():
    meta = metadata_for("CH_GOLD", "x", 1, extra={"note": "y" * 400})
    assert len(meta["note"]) == 256


def test_opposite_side():
    assert opposite("BUY") == "SELL"
    assert opposite("SELL") == "BUY"


# ── valutazione entry (parità con l'EA) ───────────────────────────────────


def test_entry_without_range_is_direct():
    decision = evaluate_entry("SELL", 4059.0, 4059.3, None, 0.01, 150)
    assert decision.execute is True
    assert decision.status == STATUS_DIRECT


def test_entry_inside_range_executes():
    decision = evaluate_entry("SELL", 4060.0, 4060.3, [4059.0, 4066.0], 0.01, 150)
    assert (decision.execute, decision.status, decision.distance_points) == (
        True,
        STATUS_IN_RANGE,
        0.0,
    )


def test_entry_within_tolerance_executes_like_gold_35c28e416b():
    """Caso reale: range 4058-4066, bid 4056.50 -> 150 punti, tolleranza 150."""
    decision = evaluate_entry("SELL", 4056.50, 4056.80, [4058.0, 4066.0], 0.01, 150)
    assert decision.execute is True
    assert decision.status == STATUS_TOLERANCE
    assert decision.distance_points == pytest.approx(150.0)


def test_entry_beyond_tolerance_is_cancelled():
    decision = evaluate_entry("SELL", 4056.50, 4056.80, [4058.0, 4066.0], 0.01, 149)
    assert decision.execute is False
    assert decision.status == STATUS_CANCELLED
    assert decision.distance_points == pytest.approx(150.0)


def test_entry_uses_ask_for_buy_and_bid_for_sell():
    buy = evaluate_entry("BUY", 4020.0, 4028.5, [4027.0, 4029.0], 0.01, 10)
    sell = evaluate_entry("SELL", 4020.0, 4028.5, [4027.0, 4029.0], 0.01, 10)
    assert buy.status == STATUS_IN_RANGE
    assert sell.status == STATUS_CANCELLED


def test_entry_requires_quote_when_range_present():
    with pytest.raises(MappingError):
        evaluate_entry("SELL", None, None, [4058.0, 4066.0], 0.01, 150)


def test_entry_rejects_invalid_direction():
    with pytest.raises(MappingError):
        evaluate_entry("FLAT", 1.0, 1.0, None, 0.01, 150)


# ── plan_open ─────────────────────────────────────────────────────────────


def test_plan_open_now_opens_two_naked_tickets():
    plans = plan_open(GOLD_OPEN_NOW, XAU, bridge_version="2.15")
    assert [p.intent for p in plans] == [STATUS_OPEN_NOW, STATUS_OPEN_NOW]
    assert [p.order_code for p in plans] == [
        "TG-GOLD-32cf923e6b-T1O",
        "TG-GOLD-32cf923e6b-T2O",
    ]
    for plan in plans:
        payload = plan.payload
        assert payload["type"] == "MARKET"
        assert payload["instrument"] == "XAU/USD"
        assert payload["side"] == "SELL"
        assert payload["quantity"] == 10
        assert payload["positionEffect"] == "OPEN"
        assert "orders" not in payload  # nessun gruppo: apertura nuda
        assert payload["metadata"]["tg_tp"] in ("1", "2")


def test_plan_open_with_levels_builds_if_then_group_per_ticket():
    plans = plan_open(IVAN_OPEN, XAU, bridge_version="2.15")
    assert len(plans) == 4
    assert all(p.intent == "OPEN_WITH_PROTECTIONS" for p in plans)

    first = plans[0].payload
    assert first["contingencyType"] == "IF-THEN"
    open_order, sl_order, tp_order = first["orders"]
    assert (open_order["type"], open_order["side"], open_order["quantity"]) == (
        "MARKET",
        "SELL",
        10,
    )
    assert (sl_order["type"], sl_order["side"], sl_order["stopPrice"]) == (
        "STOP",
        "BUY",
        4071.0,
    )
    assert (tp_order["type"], tp_order["side"], tp_order["limitPrice"]) == (
        "LIMIT",
        "BUY",
        4055.0,
    )
    # le protezioni ereditano la quantità dalla posizione
    assert sl_order["quantity"] == 0 and tp_order["quantity"] == 0
    assert sl_order["positionEffect"] == "CLOSE"


def test_plan_open_assigns_one_tp_per_ticket_in_order():
    plans = plan_open(IVAN_OPEN, XAU)
    tps = [p.payload["orders"][2]["limitPrice"] for p in plans]
    assert tps == [4055.0, 4052.0, 4045.0, 4020.0]


def test_plan_open_rejects_wrong_action():
    with pytest.raises(MappingError):
        plan_open({**IVAN_OPEN, "action": "CLOSE_ALL_SYMBOL"}, XAU)


def test_plan_open_rejects_invalid_direction():
    with pytest.raises(MappingError):
        plan_open({**IVAN_OPEN, "direction": ""}, XAU)


# ── plan_update_open ──────────────────────────────────────────────────────


def test_plan_update_open_attaches_protections_to_existing_position():
    plans = plan_update_open(
        GOLD_UPDATE_OPEN, XAU, position_codes={1: "63649", 2: "63650"}
    )
    assert [p.intent for p in plans] == ["SET_SL", "SET_TP", "SET_SL", "SET_TP"]
    sl1, tp1, sl2, tp2 = (p.payload for p in plans)
    assert sl1["positionCode"] == "63649" and sl1["stopPrice"] == 4076.0
    assert tp1["positionCode"] == "63649" and tp1["limitPrice"] == 4052.0
    assert sl2["positionCode"] == "63650" and sl2["stopPrice"] == 4076.0
    assert tp2["positionCode"] == "63650" and tp2["limitPrice"] == 4035.0
    # protezioni separate: due POST distinti, mai una richiesta unica
    assert all("orders" not in p.payload for p in plans)


def test_plan_update_open_fills_missing_slot_like_ea_215():
    plans = plan_update_open(GOLD_UPDATE_OPEN, XAU, position_codes={1: "63649"})
    intents = [p.intent for p in plans]
    assert intents == ["SET_SL", "SET_TP", "EXECUTED_UPDATE_FILL"]

    fill = plans[-1]
    assert fill.tp_index == 2
    assert fill.payload["contingencyType"] == "IF-THEN"
    open_order = fill.payload["orders"][0]
    assert open_order["type"] == "MARKET"
    assert open_order["quantity"] == 10
    assert fill.payload["orders"][1]["stopPrice"] == 4076.0
    assert fill.payload["orders"][2]["limitPrice"] == 4035.0


def test_plan_update_open_without_positions_opens_all_slots():
    plans = plan_update_open(GOLD_UPDATE_OPEN, XAU, position_codes={})
    assert [p.intent for p in plans] == ["EXECUTED_UPDATE_FILL"] * 2
    assert [p.tp_index for p in plans] == [1, 2]


def test_plan_update_open_order_codes_do_not_collide_with_open():
    fill = plan_update_open(GOLD_UPDATE_OPEN, XAU, position_codes={})[0]
    opened = plan_open({**GOLD_UPDATE_OPEN, "action": "OPEN"}, XAU)[0]
    assert fill.order_code != opened.order_code


# ── chiusure ──────────────────────────────────────────────────────────────


def test_build_close_position_uses_opposite_side_and_zero_quantity():
    payload = build_close_position("XAU/USD", "SELL", "63649", "TG-GOLD-x-T1C")
    assert payload == {
        "orderCode": "TG-GOLD-x-T1C",
        "type": "MARKET",
        "instrument": "XAU/USD",
        "quantity": 0,
        "positionEffect": "CLOSE",
        "positionCode": "63649",
        "side": "BUY",
        "tif": "GTC",
    }


def test_build_close_position_requires_position_code():
    with pytest.raises(MappingError):
        build_close_position("XAU/USD", "SELL", "", "code")


def test_plan_close_emits_one_order_per_position():
    positions = [
        {"positionCode": "63649", "symbol": "XAU/USD", "side": "SELL"},
        {"positionCode": "63650", "symbol": "XAU/USD", "side": "SELL"},
    ]
    signal = {
        "action": "CLOSE_ALL_SYMBOL",
        "symbol": "XAUUSD",
        "channel_id": "CH_GOLD",
        "signal_id": "deadbeef01",
    }
    plans = plan_close(signal, positions)
    assert [p.payload["positionCode"] for p in plans] == ["63649", "63650"]
    assert all(p.payload["side"] == "BUY" for p in plans)
    assert len({p.payload["orderCode"] for p in plans}) == 2


def test_plan_close_skips_positions_without_code():
    plans = plan_close(
        {"symbol": "XAUUSD", "channel_id": "CH_GOLD", "signal_id": "s"},
        [{"symbol": "XAU/USD", "side": "SELL"}],
    )
    assert plans == []


def test_select_positions_filters_symbol_and_side():
    positions = [
        {"positionCode": "1", "symbol": "XAU/USD", "side": "SELL"},
        {"positionCode": "2", "symbol": "XAU/USD", "side": "BUY"},
        {"positionCode": "3", "symbol": "EUR/USD", "side": "SELL"},
    ]
    assert [p["positionCode"] for p in select_positions(positions, "XAU/USD")] == ["1", "2"]
    assert [p["positionCode"] for p in select_positions(positions, "XAU/USD", "SELL")] == ["1"]
    assert [p["positionCode"] for p in select_positions(positions, side="SELL")] == ["1", "3"]


def test_instrument_meta_from_api_requires_symbol():
    with pytest.raises(MappingError):
        InstrumentMeta.from_api({"lotSize": 100})


def test_instrument_meta_defaults_on_missing_fields():
    meta = InstrumentMeta.from_api({"symbol": "XAU/USD"})
    assert (meta.lot_size, meta.price_increment, meta.quantity_increment) == (1.0, 0.01, 0.0)
