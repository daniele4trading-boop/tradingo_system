from __future__ import annotations

import tradingo_bridge as tb

GOLD = {"id": "CH_HYBRIDGOLD", "magic_base": 15000}
FX = {"id": "CH_HYBRIDFX", "magic_base": 16000}


def _p(text, ch=GOLD, **kw):
    return tb.parser_hybrid(text, ch, None, **kw)


def test_gold_open_tp3_open_is_runner():
    s = _p("▲ Buy xauusd.pro 4124\n\nSl: 4119 (50 pips)\n\nTp1: 4129\nTp2: 4134\nTp3: open", msg_id=6977)
    assert s["action"] == "OPEN" and s["direction"] == "BUY" and s["symbol"] == "XAUUSD"
    assert (s["entry"], s["sl"], s["tp_levels"], s["magic_base"]) == (4124, 4119, [4129, 4134], 15000)


def test_gold_open_low_risk_with_anticipo_note():
    s = _p("Low Risk\n\n▼ Sell xauusd.pro 4170\n\nSl: 4178 (80 pips)\n\nTp1: 4160\nTp2: 4150\nTp3: open", msg_id=6965)
    assert (s["direction"], s["entry"], s["sl"], s["tp_levels"]) == ("SELL", 4170, 4178, [4160, 4150])
    s = _p("▲ Buy xauusd.pro 4138\n\nSl: 4128 (100 pips)\n\nTp1: 4148 — possibile anticipo area 4145\nTp2: 4158\nTp3: open")
    assert s["tp_levels"] == [4148, 4158]


def test_forex_open_sl_after_tps():
    s = _p("🟢BUY CHFJPY 190.128\n\nTP1 190.760\nTP2 191.420\nTP3 open\n\nSL 189.500 (63 pips)", FX, msg_id=3064)
    assert (s["symbol"], s["entry"], s["sl"], s["tp_levels"]) == ("CHFJPY", 190.128, 189.5, [190.76, 191.42])
    s = _p("🔴SELL USDCAD 1.42579\n\nTP1 1.42210\nTP2 1.41850\nTP3 open \n\nSL 1.42951 (37 pips)", FX, msg_id=3058)
    assert s["direction"] == "SELL" and s["sl"] == 1.42951


def test_replies_follow_reply_to_not_last_signal():
    _p("🔴SELL AUDCHF 0.58150\n\nTP1 0.57700\nTP2 0.57300\nTP3 open\n\nSL 0.58570 (44 pips)", FX, msg_id=3043)
    _p("🟢BUY USDJPY 158.160\n\nTP1 159.000\nTP2 open\nTP3 open\n\nSL 157.300 (85 pips)", FX, msg_id=3045)
    s = _p("Noi anticipiamo il tp1 qui 🔥", FX, reply_to=3043)
    assert (s["action"], s["tp_index"], s["symbol"], s["direction"]) == ("CHECK_AND_CLOSE_TP", 1, "AUDCHF", "SELL")
    assert _p("Stoploss preso", FX, reply_to=3045)["action"] == "CHECK_AND_CLOSE"
    assert _p("Noi chiudiamo ora la posizione a 2.3R 🔥", FX, reply_to=3043)["action"] == "CLOSE_ALL_SYMBOL"
    assert _p("Break even preso", FX, reply_to=3043)["action"] == "CHECK_AND_BE"


def test_info_and_analysis_messages_ignored():
    _p("▲ Buy xauusd.pro 4122\nSl: 4112 (100 pips)\nTp1: 4132 Tp2: 4142 Tp3: open", msg_id=6971)
    for t in ("Tp 2 preso 🔥🔥", "Tp 1 effettivo preso 🔥", "Bella reazione 👀", "Attenzione alla volatilità"):
        assert _p(t, reply_to=6971) is None
    assert _p("📊 Aggiornamento operativo\n\nAbbiamo individuato un livello BUY a 4.104, che rispetta il nostro modello.") is None
    assert _p("☀️ Buongiorno a tutti!\n\n🟡 XAUUSD: 4.187–4.191\n🔵 CADCHF: 0.58700") is None
    assert _p("📊 Chiusura della giornata\n\nChiudiamo qui la giornata.", reply_to=6971) is None


def test_edit_of_known_signal_does_not_reopen():
    t = "▲ Buy xauusd.pro 4124\nSl: 4119\nTp1: 4129"
    assert _p(t, msg_id=1)["action"] == "OPEN"
    assert _p(t, msg_id=1, is_edit=True) is None


def test_sl_moves_and_false_closes():
    _p("🟢BUY EURUSD 1.16300\nTP1 1.16700\nTP2 open\nSL 1.16000 (30 pips)", FX, msg_id=50)
    s = _p("Portiamo lo Stoploss a 1.16514 così da avere un rischio praticamente a 0", FX, reply_to=50)
    assert (s["action"], s["new_sl"], s["symbol"]) == ("UPDATE_SL", 1.16514, "EURUSD")
    assert _p("Noi spostiamo lo stop loss a Break even (punto di entrata)", FX, reply_to=50)["action"] == "CHECK_AND_BE"
    assert _p("Abbiamo corretto lo Stoploss", FX, reply_to=50) is None
    assert _p("Stoploss", FX, reply_to=50)["action"] == "CHECK_AND_CLOSE"
    assert _p("Il nostro setup é andato in stop loss.", FX, reply_to=50)["action"] == "CHECK_AND_CLOSE"
    for t in ("Setup filtrato stop loss evitato 🔥",
              "Noi chiudiamo parte dell’operazione ora, e manteniamo come ultimo tp : 3385",
              "Siamo a 5 pips da take profit 1, per chi vuole può chiuderlo qui 🫶"):
        assert _p(t, FX, reply_to=50) is None, t
    assert _p("Noi chiudiamo qui a Tp 3 🤝", FX, reply_to=50)["action"] == "CLOSE_ALL_SYMBOL"


def test_sl_far_from_entry_ignored():
    _p("Buy ltcusd 55.20\nSl: 54.00\nTp1: 57.00", msg_id=60)
    assert _p("Come potete notare il nostro stop loss è di 1.24 euro", reply_to=60) is None
