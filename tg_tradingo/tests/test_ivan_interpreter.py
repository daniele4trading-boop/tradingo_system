import json

from ivan_interpreter import (
    Interpretation,
    InterpreterConfig,
    IvanInterpreter,
    IvanMessageBuffer,
    build_user_prompt,
    interpretation_to_signal,
    is_urgent,
)

CH = {"id": "CH_IVAN", "magic_base": 17000}
TRADE = {
    "symbol": "XAUUSD", "direction": "SELL", "entry": None,
    "entry_range": [4164.0, 4168.0], "sl": 4176.0,
    "tp_levels": [4158.0, 4155.0, 4152.0, 4144.0], "setup_entry": 4166.0,
}


def _fake(reply: dict):
    calls = []

    def completer(system, user, cfg):
        calls.append(user)
        return "```json\n" + json.dumps(reply) + "\n```"
    return completer, calls


def test_be_worst_split_over_two_messages_maps_to_scope_worst():
    completer, calls = _fake({"action": "BE_SCOPE", "scope": "worst",
                              "confidence": 0.92, "reason": "BE solo peggiori"})
    interp = IvanInterpreter(InterpreterConfig(enabled=True), completer)
    msgs = ["Mettiamo a BE le entry più brutte",
            "E lasciamo con stop originale le entry migliori"]
    res = interp.interpret(msgs, TRADE)
    assert res.action == "BE_SCOPE" and res.scope == "WORST"
    sig = interpretation_to_signal(res, CH, TRADE, msgs[-1])
    assert sig["action"] == "CHECK_AND_BE"
    assert sig["scope"] == "WORST"
    assert sig["be_price"] == 4166.0
    assert sig["magic_base"] == 17000
    # il prompt contiene entrambi i messaggi e il setup aperto
    assert "[1] Mettiamo a BE" in calls[0] and "[2] E lasciamo" in calls[0]
    assert "SELL XAUUSD entry 4164.0-4168.0" in calls[0]


def test_low_confidence_yields_no_action():
    completer, _ = _fake({"action": "CLOSE_ALL", "confidence": 0.4, "reason": "?"})
    interp = IvanInterpreter(InterpreterConfig(enabled=True), completer)
    res = interp.interpret(["chissà, forse chiudiamo"], TRADE)
    assert res.action == "NONE"
    assert interpretation_to_signal(res, CH, TRADE, "x") is None


def test_close_last_setup_and_move_sl_payloads():
    r = Interpretation.from_model_json({"action": "CLOSE_LAST_SETUP", "confidence": 0.9})
    assert interpretation_to_signal(r, CH, TRADE, "chiudiamo la reentry") == {
        "action": "CLOSE_SELECTIVE", "keep": "ALL_BUT_NEWEST", "symbol": "XAUUSD",
        "magic_base": 17000, "raw_message": "chiudiamo la reentry",
        "source": "llm", "llm_reason": "",
    }
    r = Interpretation.from_model_json({"action": "MOVE_SL", "price": "4170", "confidence": 1})
    sig = interpretation_to_signal(r, CH, TRADE, "sl a 4170")
    assert sig["action"] == "UPDATE_SL" and sig["new_sl"] == 4170.0
    assert sig["direction"] == "SELL"


def test_invalid_model_output_degrades_to_none():
    assert Interpretation.from_model_json({"action": "BE_SCOPE"}).action == "NONE"
    assert Interpretation.from_model_json({"action": "MOVE_TP", "confidence": 0.9}).action == "NONE"
    assert Interpretation.from_model_json({"action": "FLY", "confidence": 0.9}).action == "NONE"
    r = Interpretation.from_model_json({"action": "MOVE_TP", "price": 4150, "tp_index": 7,
                                        "confidence": 0.9})
    assert r.action == "MOVE_TP" and r.tp_index is None


def test_api_error_never_raises():
    def boom(system, user, cfg):
        raise RuntimeError("rete giù")
    res = IvanInterpreter(InterpreterConfig(enabled=True), boom).interpret(["BE"], TRADE)
    assert res.action == "NONE" and "rete" in res.reason


def test_buffer_keeps_context_and_pending_separately():
    buf = IvanMessageBuffer(window_sec=300)
    buf.push(1000.0, 1, "Mettiamo a BE le entry più brutte")
    buf.push(1010.0, 2, "E lasciamo con stop originale le entry migliori")
    ctx, pending = buf.take_pending(1030.0)
    assert ctx == ["Mettiamo a BE le entry più brutte",
                   "E lasciamo con stop originale le entry migliori"]
    assert pending == [1, 2]
    assert not buf.has_pending()
    buf.push(1100.0, 3, "Chiudiamo tutto")
    ctx, pending = buf.take_pending(1120.0)
    assert pending == [3] and len(ctx) == 3  # i precedenti restano come contesto
    buf.push(2000.0, 4, "TP1 hit")
    ctx, _ = buf.take_pending(2001.0)
    assert ctx == ["TP1 hit"]  # finestra 5 min scaduta


def test_urgent_and_config_from_channel():
    assert is_urgent("CHIUDIAMO ORA")
    assert is_urgent("Usciamo subito")
    assert not is_urgent("Mettiamo a BE le entry più brutte")
    cfg = InterpreterConfig.from_channel({"interpreter": {"enabled": True, "shadow": False,
                                                          "debounce_sec": 10}})
    assert cfg.enabled and not cfg.shadow and cfg.debounce_sec == 10
    assert cfg.model == "claude-sonnet-4-5"
    assert InterpreterConfig.from_channel({}).enabled is False


def test_prompt_without_setup():
    p = build_user_prompt(["ciao"], None)
    assert "nessuno noto" in p and "[1] ciao" in p


def test_executed_action_leaves_context_and_is_passed_to_prompt():
    buf = IvanMessageBuffer(window_sec=300)
    buf.push(1000.0, 1, "Chiudiamo ORA")
    ctx, _ = buf.take_pending(1000.0)
    buf.mark_executed(1000.0, "CHECK_AND_BE scope=WORST")
    buf.push(1020.0, 2, "Non ha mosso nulla")
    ctx, pending = buf.take_pending(1045.0)
    assert ctx == ["Non ha mosso nulla"] and pending == [2]
    assert buf.executed(1045.0) == ["CHECK_AND_BE scope=WORST"]
    p = build_user_prompt(ctx, TRADE, buf.executed(1045.0))
    assert "AZIONI GIÀ ESEGUITE" in p and "CHECK_AND_BE" in p
    buf.reset()
    assert buf.executed(1045.0) == []


def test_after_close_all_buffer_is_mute_until_new_setup():
    buf = IvanMessageBuffer(window_sec=300)
    buf.push(1000.0, 1, "Chiudiamo ORA")
    buf.take_pending(1000.0)
    buf.mark_executed(1000.0, "CLOSE_ALL_SYMBOL")
    buf.push(1400.0, 2, "Testa a domani")
    assert buf.take_pending(1430.0) == ([], [])
    buf.reset()
    buf.push(2000.0, 3, "Spostiamo SL a BE")
    assert buf.take_pending(2030.0)[1] == [3]
