import json
from datetime import datetime, timezone

import sertorio_zones as sz
from ivan_interpreter import InterpreterConfig
from sertorio_zones import (
    Analysis,
    SertorioInterpreter,
    SetupConfig,
    ShadowBook,
    build_setups,
    handle_analysis,
    is_analysis_text,
    parse_analysis_regex,
)

TS = datetime(2026, 10, 28, 12, 7, tzinfo=timezone.utc)
ANALISI = """🥇 ANALISI XAUUSD – Mercoledì 28 Ottobre 🥇
💰 Prezzo attuale: 4046 $
🔴 RESISTENZE
🔴 4057 – 4058 (massimo di Londra + apertura di giornata)
🔴 4065 – 4070 (massimo settimanale + EMA50 H4) 🔥 livello chiave
🔴 4100 (psicologico)
🟢 SUPPORTI
🟢 4036 – 4043 (EMA50 H1 + minimo delle 12:00)
🟢 4028 – 4030 (minimo di giornata) 🔥 livello chiave
🟢 4010
🟢 4000 (psicologico) e poi 3989 (minimo di ieri)
📈 PROBABILITÀ (mia opinione personale)
🐂 Long: 45%
🐻 Short: 55%
🐂 IDEA LONG
📍 Entrata: 4030 – 4033, solo con conferma di rifiuto sul minimo di giornata
🛑 Stop Loss: 4018
🎯 TP1: 4046
🎯 TP2: 4057
🎯 TP3: 4068
🐻 IDEA SHORT
📍 Entrata: 4064 – 4069, su rifiuto della zona del massimo settimanale
🛑 Stop Loss: 4080
🎯 TP1: 4046
🎯 TP2: 4030
🎯 TP3: 4012
🔑 Livello chiave di oggi: 4028. Finché tiene, il rimbalzo di ieri è vivo.
⚠️ Questa analisi rappresenta esclusivamente la mia opinione personale 4000""" + " x" * 50


def test_is_analysis_text():
    assert is_analysis_text(ANALISI)
    assert not is_analysis_text("SEGNALE IN SALA VIP MANDATO")


def test_regex_parser_zones_ideas_bias_key():
    a = parse_analysis_regex(ANALISI, TS)
    assert a.current_price == 4046 and a.bias_long_pct == 45 and a.key_level == 4028
    assert {(z.lo, z.hi) for z in a.resistances()} == {(4057, 4058), (4065, 4070), (4100, 4100)}
    assert (4028, 4030) in {(z.lo, z.hi) for z in a.supports()} and (3989, 3989) in {(z.lo, z.hi) for z in a.supports()}
    assert any(z.key for z in a.supports() if z.lo == 4028)
    assert [(i.direction, i.entry_lo, i.entry_hi, i.sl, i.tps) for i in a.ideas] == [
        ("BUY", 4030, 4033, 4018, [4046, 4057, 4068]), ("SELL", 4064, 4069, 4080, [4046, 4030, 4012])]


def test_llm_output_wins_and_parser_fills_gaps():
    reply = {"symbol": "XAUUSD", "current_price": 4046, "bias_long_pct": 45, "key_level": None,
             "zones": [{"kind": "support", "lo": 4028, "hi": 4030, "key": True}],
             "ideas": [], "summary": "compressione pre-Fed"}
    interp = SertorioInterpreter(InterpreterConfig(enabled=True), lambda s, u, c: "```json\n" + json.dumps(reply) + "\n```")
    a = interp.interpret(ANALISI, TS)
    assert a.source == "llm" and a.summary == "compressione pre-Fed"
    assert a.key_level == 4028 and len(a.ideas) == 2  # dal parser
    assert len(a.zones) == 1


def test_llm_failure_falls_back_to_parser():
    def boom(s, u, c):
        raise RuntimeError("rete")
    a = SertorioInterpreter(InterpreterConfig(enabled=True), boom).interpret(ANALISI, TS)
    assert a.source == "regex" and len(a.ideas) == 2


def test_build_setups_ideas_and_zone_derived_with_expiry():
    a = parse_analysis_regex(ANALISI, TS)
    setups = build_setups(a, SetupConfig(derive_from_zones=False), TS, "A1")
    assert [s.id for s in setups] == ["A1-I1", "A1-I2"] and all(s.expires_utc == "2026-10-28T21:00:00Z" for s in setups)
    a.ideas = []
    zs = build_setups(a, SetupConfig(sl_buffer=5.0, min_bias_pct=40), TS, "A1")
    buy = next(s for s in zs if s.direction == "BUY")
    assert (buy.entry_lo, buy.entry_hi, buy.sl) == (4036, 4043, 4031) and buy.tps[0] == 4057
    sell = next(s for s in zs if s.direction == "SELL")
    assert (sell.entry_lo, sell.entry_hi, sell.sl) == (4057, 4058, 4063) and sell.tps[0] == 4043
    late = build_setups(a, SetupConfig(), TS.replace(hour=22), "A2")
    assert late and late[0].expires_utc == "2026-10-29T21:00:00Z"


def _book(tmp_path):
    cfg = SetupConfig(derive_from_zones=False)
    a = parse_analysis_regex(ANALISI, TS)
    book = ShadowBook(cfg, tmp_path / "shadow.json", tmp_path / "shadow.csv")
    book.replace_setups(build_setups(a, cfg, TS, "A1"), TS)
    return book


def test_shadow_fill_tp_be_and_sl(tmp_path):
    book = _book(tmp_path)
    t = TS
    book.on_bar(t, 4050, 4045, 4047)            # nessun tocco
    assert len(book.pending) == 2 and not book.open
    book.on_bar(t, 4040, 4032, 4035)            # tocco zona long 4030-4033
    assert len(book.open) == 3 and all(leg["fill"] == 4033 for leg in book.open)
    book.on_bar(t, 4047, 4040, 4046)            # TP1 -> le altre a BE
    assert [leg["sl"] for leg in book.open] == [4033, 4033]
    book.on_bar(t, 4040, 4032, 4035)            # BE
    assert not book.open
    assert [c["reason"] for c in book.closed] == ["TP", "BE", "BE"]
    assert book.closed[0]["pips"] == 130.0
    book.on_bar(t, 4085, 4060, 4070)            # short: tocco e SL nella stessa barra -> annullato
    assert not book.pending and not book.open
    csv_text = (tmp_path / "shadow.csv").read_text(encoding="utf-8")
    assert "FILL" in csv_text and "CANCEL" in csv_text
    reloaded = ShadowBook(SetupConfig(), tmp_path / "shadow.json")
    assert len(reloaded.closed) == 3 and reloaded.stats(1, t)["pips"] == 130.0


def test_shadow_expiry_and_invalidation(tmp_path):
    book = _book(tmp_path)
    book.on_bar(TS, 4050, 4015, 4020)           # sotto SL long senza toccare prima il range... tocca e SL -> cancel
    assert all(p["direction"] == "SELL" for p in book.pending)
    book.on_bar(TS.replace(hour=21, minute=1), 4050, 4045, 4047)
    assert not book.pending


def test_handle_analysis_writes_state_and_no_live(tmp_path):
    ch = {"id": "CH_SERTORIO", "interpreter": {"enabled": False}, "setups": {"derive_from_zones": False}}
    doc = handle_analysis(ch, ANALISI, tmp_path, TS)
    assert doc["shadow"] is True and len(doc["setups"]) == 2
    saved = json.loads((tmp_path / sz.ZONES_FILE).read_text(encoding="utf-8"))
    assert saved["analysis_id"] == "SZ202610281207" and Analysis.from_dict(saved["analysis"]).key_level == 4028
    assert handle_analysis(ch, "TP PRESO", tmp_path, TS) is None
