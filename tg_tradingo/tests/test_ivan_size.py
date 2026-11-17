import json

from ivan_interpreter import (
    InterpreterConfig,
    IvanMessageBuffer,
    build_size_add_signal,
    classify_size,
    is_urgent,
    size_hint_regex,
)

CH = {"id": "CH_IVAN", "magic_base": 17000}
SETUP_RANGE = {
    "action": "OPEN", "symbol": "XAUUSD", "direction": "SELL",
    "entry_range": [4060.0, 4065.0], "sl": 4075.0,
    "tp_levels": [4053.0, 4050.0, 4047.0, 4040.0],
    "raw_message": "XAUUSD SELL 4060-4065 TP1:4053 TP2:4050 TP3:4047 TP4:4040 SL:4075",
}
SETUP_SINGLE = {
    "action": "OPEN", "symbol": "XAUUSD", "direction": "SELL", "entry": 4124.0,
    "sl": 4137.0, "tp_levels": [4048.0, 4050.0, 4053.0, 4065.0],
    "raw_message": "XAUUSD SELL 4124",
}
CFG = InterpreterConfig(enabled=True, shadow=False, size_upgrade=True)


def _fake(reply):
    calls = []

    def completer(system, user, cfg):
        calls.append((system, user))
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply)
    return completer, calls


def test_size_medio_basse_keeps_medium():
    completer, calls = _fake({"size": "KEEP", "confidence": 0.95, "reason": "size medio basse"})
    dec = classify_size(CFG, SETUP_SINGLE, ["Size medio basse!"], completer)
    assert dec.size == "KEEP" and dec.source == "llm"
    assert "[1] Size medio basse!" in calls[0][1]
    assert "SELL XAUUSD entry 4124.0" in calls[0][1]


def test_no_message_upgrades_to_full():
    completer, calls = _fake({"size": "NONE", "confidence": 0.9, "reason": "nessuna indicazione"})
    dec = classify_size(CFG, SETUP_RANGE, [], completer)
    assert dec.size == "NONE"
    assert "(nessuno)" in calls[0][1]


def test_llm_error_falls_back_to_regex():
    completer, _ = _fake(RuntimeError("timeout"))
    assert classify_size(CFG, SETUP_RANGE, ["*trade rischioso"], completer).size == "KEEP"
    dec = classify_size(CFG, SETUP_RANGE, ["Entrate 🔥"], completer)
    assert dec.size == "NONE" and dec.source == "regex"


def test_low_confidence_is_prudent():
    completer, _ = _fake({"size": "NONE", "confidence": 0.3, "reason": "?"})
    assert classify_size(CFG, SETUP_RANGE, ["mah"], completer).size == "KEEP"


def test_invalid_label_uses_regex():
    completer, _ = _fake({"size": "BOH", "confidence": 0.9})
    assert classify_size(CFG, SETUP_RANGE, ["size piccole"], completer).size == "KEEP"


def test_size_hint_regex():
    assert size_hint_regex(["Size medie"]) == "KEEP"
    assert size_hint_regex(["piccole size, attenzione news"]) == "KEEP"
    assert size_hint_regex(["metà size"]) == "KEEP"
    assert size_hint_regex(["VAMOOO", "Entrate"]) == "NONE"
    assert size_hint_regex([]) == "NONE"


def test_add_signal_range_stack_or_better():
    sig = build_size_add_signal(SETUP_RANGE, CH)
    assert sig["action"] == "OPEN"
    assert sig["allow_stack"] is True and sig["entry_or_better"] is True
    assert sig["entry_range"] == [4060.0, 4065.0] and "entry" not in sig
    assert sig["sl"] == 4075.0 and sig["tp_levels"] == [4053.0, 4050.0, 4047.0, 4040.0]
    assert sig["magic_base"] == 17000 and sig["source"] == "llm_size"
    assert "lot_factor" not in sig and "is_add_signal" not in sig


def test_add_signal_single_entry():
    sig = build_size_add_signal(SETUP_SINGLE, CH)
    assert sig["entry"] == 4124.0 and "entry_range" not in sig


def test_buffer_messages_since_setup():
    buf = IvanMessageBuffer()
    buf.push(100.0, 1, "vecchio")
    buf.push(110.0, 2, "Size medie")
    assert buf.messages_since(105.0) == ["Size medie"]


def test_config_from_channel_live_instances():
    cfg = InterpreterConfig.from_channel({"interpreter": {
        "enabled": True, "shadow": False, "live_instances": ["VANTAGE"],
        "size_upgrade": True, "size_window_sec": 25,
    }})
    assert cfg.live_instances == ["VANTAGE"] and cfg.size_upgrade
    assert cfg.size_window_sec == 25
    assert InterpreterConfig().live_instances == [] and not InterpreterConfig().size_upgrade


def test_close_all_messages_are_urgent():
    assert is_urgent("CHIUDERE TUTTO")
    assert is_urgent("chiudiamo tutte")
    assert is_urgent("USCIAMO")
    assert not is_urgent("Size medio basse!")


def test_write_signal_routes_by_instance(tmp_path, monkeypatch):
    import tradingo_bridge as tb

    dirs = {n: tmp_path / n for n in ("VANTAGE", "XLENCE", "AXI")}
    for d in dirs.values():
        d.mkdir()
    monkeypatch.setitem(tb.CONFIG, "mt5_instances", [
        {"name": n, "signals_path": str(d), "enabled": True} for n, d in dirs.items()
    ])
    ch = {"id": "CH_IVAN", "name": "Ivan", "signal_file": "signal_ch_ivan.json"}
    sig = {"action": "CLOSE_ALL_SYMBOL", "symbol": "XAUUSD", "magic_base": 17000}
    assert tb.write_signal(ch, dict(sig), only=["VANTAGE"])
    assert (dirs["VANTAGE"] / "signal_ch_ivan.json").exists()
    assert not (dirs["XLENCE"] / "signal_ch_ivan.json").exists()
    assert tb.write_signal(ch, dict(sig), exclude=["VANTAGE"])
    assert (dirs["XLENCE"] / "signal_ch_ivan.json").exists()
    assert (dirs["AXI"] / "signal_ch_ivan.json").exists()
    assert tb.get_mt5_paths() == [str(d) for d in dirs.values()]
