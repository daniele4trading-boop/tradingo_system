from __future__ import annotations

from datetime import UTC, datetime

import levels_extract as le
from levels_store import from_csv

SERTORIO = """📊 XAUUSD | Analisi del 9 Ottobre 2026 🥇
📅 Ieri laterale 4115-4145 per gran parte della giornata
🌍 Quadro macro 🇨🇳 Supporto di fondo: la banca centrale cinese ha comprato 21 tonnellate d'oro a settembre 2026
🔴 Resistenze 4188-4190 (neckline rotta + SMA 4H) 4200-4207 (doppio massimo) 4227-4231 (SMA 100 sul 4H e Fibonacci del 61,8%)
🟢 Supporti 4174-4176 (prima pausa) 4168-4170 (zona chiave) 4145-4150 4104 (Fibonacci del 78,6%)
🐻 Scenario ribassista (55%) Rifiuto da 4188-4190 📍 Short su retest 4186-4190 🛑 SL 4198 ✅ TP1 4170 | TP2 4160
🐂 Scenario rialzista (45%) Se 4168-4170 tiene 📍 Long in area 4168-4170 con conferma 🛑 SL 4160 ✅ TP1 4188 | TP2 4200
🔑 Livello chiave della giornata: 4170 Sopra = pullback sano
⚠️ Disclaimer: questa analisi è una mia opinione personale 4000"""

SERTORIO_DOTS = """🌅 BUONGIORNO ! ANALISI XAUUSD – VENERDÌ 2 OTTOBRE 🥇
🔴 RESISTENZE 🔴 4.190 – 4.200 → il muro 🔴 4.215 – 4.230 → zona chiave
🟢 SUPPORTI 🟢 4.160 → primo appoggio 🟢 4.110 → minimo di 8 settimane"""


def _set(levels):
    return {(lv["kind"], lv["price"], lv["price_to"]) for lv in levels}


def test_sertorio_sections_scenarios_and_key():
    got = le.extract_sertorio(SERTORIO)
    s = _set(got)
    assert {("resistance", 4188, 4190), ("resistance", 4200, 4207), ("resistance", 4227, 4231)} <= s
    assert {("support", 4174, 4176), ("support", 4168, 4170), ("support", 4145, 4150), ("support", 4104, None)} <= s
    assert ("key", 4170, None) in s
    assert ("watch_sell", 4186, 4190) in s and ("watch_buy", 4168, 4170) in s
    assert all(4000 < lv["price"] < 4300 for lv in got)
    short = next(lv for lv in got if lv["kind"] == "watch_sell")
    assert short["label"] == "Short 55% SL 4198 TP 4170/4160"


def test_sertorio_thousand_dots():
    s = _set(le.extract_sertorio(SERTORIO_DOTS))
    assert {("resistance", 4190, 4200), ("resistance", 4215, 4230), ("support", 4160, None), ("support", 4110, None)} == s


def test_hybrid_watch_levels_and_clear():
    lv, clear = le.extract_hybrid("📍 Livelli in osservazione\n• 🟢 BUY 4.121\n• 🔴 SHORT 4.187–4.191", "XAUUSD")
    assert _set(lv) == {("watch_buy", 4121, None), ("watch_sell", 4187, 4191)} and not clear
    lv, _ = le.extract_hybrid("☀️ Buon inizio settimana!\n🟢 GBPJPY — BUY 210.700\n🟢 GBPAUD — BUY 1.87800", None)
    assert {(x["symbol"], x["price"]) for x in lv} == {("GBPJPY", 210.7), ("GBPAUD", 1.878)}
    lv, _ = le.extract_hybrid("☀️ Buongiorno!\n🟡 XAUUSD: 4.187–4.191\n🔵 CADCHF: 0.58700", None)
    assert {(x["symbol"], x["kind"], x["price"]) for x in lv} == {("XAUUSD", "watch", 4187), ("CADCHF", "watch", 0.587)}
    assert le.extract_hybrid("☀️ Buongiorno! Al momento non abbiamo livelli attivi.", "XAUUSD") == ([], True)
    assert le.extract_hybrid("▲ Buy xauusd.pro 4124\nSl: 4119\nTp1: 4129", "XAUUSD") == ([], False)


def test_update_levels_file_lifecycle(tmp_path):
    f = tmp_path / "tradingo" / "tradingo_levels.csv"
    now = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    assert le.update_levels(f, "CH_SERTORIO", SERTORIO, now) >= 9
    assert le.update_levels(f, "CH_HYBRIDGOLD", "Livello BUY 4.104 in osservazione", now, "XAUUSD") == 1
    rows = from_csv(f.read_text(encoding="utf-8"))
    hy = [lv for lv in rows if lv.source == "HYBRID_GOLD"]
    assert len(hy) == 1 and hy[0].valid_until_utc == datetime(2026, 10, 9, 21, 0, tzinfo=UTC)
    le.update_levels(f, "CH_HYBRIDGOLD", "▲ Buy xauusd.pro 4104\nSl: 4094\nTp1: 4114", now)
    rows = from_csv(f.read_text(encoding="utf-8"))
    assert not [lv for lv in rows if lv.source == "HYBRID_GOLD"]
    assert [lv for lv in rows if lv.source == "SERTORIO"]
    assert le.update_levels(f, "CH_IVAN", "BUY 4100", now) == 0
