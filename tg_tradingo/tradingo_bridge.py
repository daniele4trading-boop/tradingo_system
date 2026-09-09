"""
TG TradinGo Bridge - v2.24 (vedi BRIDGE_VERSION)
Sessione Telegram riutilizzata da C:\\TelegramBridge\\telegram_bridge_session.session

CANALI:
  CH1 - ZANNI VIP SIGNALS       (-1003026686847)
  CH2 - SALA GOLD VIP           (-1003302540529)
  CH3 - SALA VIP                (-1002890661441)
  CH4 - SALA STARK              (-1002073368935)
"""

import asyncio
import json
import math
import os
import re
import sys
import time
import logging
import traceback
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events

from bridge_core import (
    BridgeState,
    ProcessedMessageStore,
    apply_lot_rules,
    atomic_write_text,
    atomic_write_text_timed,
    is_unc_path,
    make_signal_id,
    match_break_even_intent,
    match_close_all_intent,
    match_close_price_followup,
    match_move_sl_price,
    match_move_tp_price,
    match_partial_close_intent,
    match_selective_close_intent,
    salvage_incoherent_entry_range,
    payload_for_ea,
    validate_signal,
)
from bridge_journal import append_bridge_event, write_heartbeat

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.environ.get(
    "TRADINGO_CONFIG",
    os.path.join(_BASE_DIR, "tradingo_config.json"),
)

def load_config():
    cfg_path = CONFIG_FILE
    if not os.path.exists(cfg_path):
        example = os.path.join(_BASE_DIR, "tradingo_config.example.json")
        if os.path.exists(example):
            cfg_path = example
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

BRIDGE_VERSION = "2.25"
HEARTBEAT_INTERVAL_SEC = 30
JOURNAL_RETENTION_DAYS = 90

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR  = Path(CONFIG["paths"]["logs"])
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file = LOG_DIR / f"tradingo_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("TradinGo")

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("TradinGo")

SIGNALS_DIR: Path | None = None
STATE_DIR: Path | None = None
BRIDGE_STATE: BridgeState | None = None
PROCESSED_MESSAGES: ProcessedMessageStore | None = None


def _paths() -> tuple[Path, Path]:
    signals = Path(CONFIG["paths"]["signals"])
    state = Path(CONFIG["paths"].get("state", Path(CONFIG["paths"]["base"]) / "state"))
    return signals, state


def _ensure_runtime() -> tuple[BridgeState, ProcessedMessageStore]:
    global SIGNALS_DIR, STATE_DIR, BRIDGE_STATE, PROCESSED_MESSAGES
    if BRIDGE_STATE is None:
        SIGNALS_DIR, STATE_DIR = _paths()
        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        BRIDGE_STATE = BridgeState(STATE_DIR / "bridge_state.json")
        PROCESSED_MESSAGES = ProcessedMessageStore(STATE_DIR / "processed_messages.json")
    return BRIDGE_STATE, PROCESSED_MESSAGES

def get_mt5_paths():
    return [i["signals_path"] for i in CONFIG.get("mt5_instances", []) if i.get("enabled", True)]

def write_signal(channel_cfg: dict, signal: dict, meta: dict | None = None):
    signal["timestamp"]    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    signal["channel_id"]   = channel_cfg["id"]
    signal["channel_name"] = channel_cfg["name"]
    written_targets: list[str] = []
    if meta:
        signal["message_id"]    = meta.get("message_id")
        signal["chat_id"]       = meta.get("chat_id")
        signal["telegram_date"] = meta.get("telegram_date")
        signal["event_type"]    = meta.get("event_type")
        if meta.get("chat_id") is not None and meta.get("message_id") is not None:
            signal["signal_id"] = make_signal_id(
                meta["chat_id"],
                meta["message_id"],
                meta.get("event_type") or "NEW",
            )

    apply_lot_rules(signal, channel_cfg)

    ok, reason = validate_signal(signal)
    if not ok:
        salvaged = salvage_incoherent_entry_range(signal, reason)
        if salvaged:
            log.warning(f"[{channel_cfg['id']}] {salvaged}")
            ok, reason = validate_signal(signal)
    if not ok:
        log.error(f"[{channel_cfg['id']}] Segnale non valido: {reason} | action={signal.get('action')}")
        return False

    payload = json.dumps(payload_for_ea(signal), indent=2)
    # Local disks first; UNC/Tailscale shares last so a hung SMB cannot delay Contabo.
    paths = sorted(
        get_mt5_paths(),
        key=lambda p: (1 if is_unc_path(p) else 0, str(p).lower()),
    )
    for mt5_path in paths:
        out_file = Path(mt5_path) / channel_cfg["signal_file"]
        try:
            # Short timeout on UNC so Telethon event loop never freezes for minutes.
            atomic_write_text_timed(
                out_file,
                payload,
                retries=3 if is_unc_path(out_file) else 5,
            )
            written_targets.append(str(out_file))
            log.info(f"[{channel_cfg['id']}] -> {out_file.name} | "
                     f"action={signal.get('action')} symbol={signal.get('symbol','')} "
                     f"dir={signal.get('direction','')} sid={signal.get('signal_id','')}")
        except Exception as e:
            log.error(f"[{channel_cfg['id']}] Errore scrittura {out_file}: {e}")
            # Keep going: Contabo local must succeed even if Gamehosting share hangs.
            continue
    if meta is not None:
        meta["written_targets"] = written_targets
    if not written_targets:
        log.error(f"[{channel_cfg['id']}] Nessun target mt5_instances scritto")
        return False
    return True


def coerce_edit_open_to_update(signal: dict | None, is_edit: bool) -> dict | None:
    """Avoid MSG+EDIT duplicate OPEN stacking on the EA."""
    if (
        signal
        and is_edit
        and signal.get("action") == "OPEN"
        and not signal.get("allow_stack")
    ):
        signal["action"] = "UPDATE_OPEN"
        log.info(
            f"EDIT OPEN → UPDATE_OPEN (avoid duplicate stack) "
            f"{signal.get('direction')} {signal.get('symbol')}"
        )
    return signal


def pf(s: str | None) -> float | None:
    """Parse float; return None on empty/invalid tokens (never raise)."""
    if s is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(s).strip().replace(",", "."))
    if cleaned in ("", ".", "..") or cleaned.count(".") > 1:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _close_signal(
    ch: dict,
    raw: str,
    reference_price: float | None = None,
    *,
    symbol: str | None = "XAUUSD",
) -> dict:
    sig: dict = {
        "action": "CLOSE_ALL_SYMBOL",
        "magic_base": ch["magic_base"],
        "raw_message": raw,
    }
    if symbol:
        sig["symbol"] = symbol
    if reference_price is not None:
        sig["reference_price"] = reference_price
    return sig


def _maybe_close_from_text(
    upper: str,
    ch: dict,
    raw: str,
    state: BridgeState | None = None,
    *,
    symbol: str | None = "XAUUSD",
) -> dict | None:
    """Shared close intent + optional price follow-up (after ignore_pats).

    La chiusura SELETTIVA ha la precedenza: "chiudiamo le entry meno premium"
    deve chiudere solo quelle, non tutto il simbolo.
    """
    keep = match_selective_close_intent(upper)
    if keep:
        if state is not None:
            state.pop_close_price_pending(ch["id"])
        log.info(f"[{ch['id']}] CLOSE_SELECTIVE keep={keep}: {raw[:60]}")
        sig: dict = {
            "action": "CLOSE_SELECTIVE",
            "keep": keep,
            "magic_base": ch["magic_base"],
            "raw_message": raw,
        }
        sig["symbol"] = symbol or "XAUUSD"
        return sig

    matched, ref = match_close_all_intent(upper)
    if matched:
        if state is not None and ref is None:
            state.set_close_price_pending(ch["id"])
        elif state is not None and ref is not None:
            state.pop_close_price_pending(ch["id"])
        log.info(f"[{ch['id']}] CLOSE_ALL_SYMBOL ref={ref}: {raw[:60]}")
        return _close_signal(ch, raw, ref, symbol=symbol)

    if state is not None:
        follow = match_close_price_followup(upper)
        if follow is not None and state.pop_close_price_pending(ch["id"]):
            log.info(f"[{ch['id']}] CLOSE_ALL_SYMBOL follow-up price={follow}: {raw[:60]}")
            return _close_signal(ch, raw, follow, symbol=symbol)
    return None

def normalize_symbol(raw: str) -> str:
    """Rimuove suffisso 'pm', mappa alias (GOLD -> XAUUSD)."""
    s = raw.upper().strip()
    s = re.sub(r"PM$", "", s)
    aliases = {"GOLD": "XAUUSD"}
    return aliases.get(s, s)

def strip_md(text: str) -> str:
    """Rimuove markdown e caratteri invisibili (zero-width spaces, ecc.)."""
    # Rimuove markdown
    text = re.sub(r"[*`_]", " ", text)
    # Rimuove caratteri zero-width e spazi non-breaking invisibili
    text = re.sub(r"[​‌‍‎‏﻿ ⁠]", " ", text)
    # Normalizza spazi multipli
    text = re.sub(r"  +", " ", text)
    return text


def fold_accents(text: str) -> str:
    """NFKD fold: METÀ → META, useful for Ivan lot-size keywords."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def contains_any(text: str, *words) -> bool:
    t = text.upper()
    return any(w.upper() in t for w in words)


# ─────────────────────────────────────────────────────────────────────────────
# IGNORE PATTERNS (per parser)
# ─────────────────────────────────────────────────────────────────────────────
#
# Un solo registro per parser: usato dai parser stessi e dal journal del bridge
# per etichettare l'outcome come IGNORED_PATTERN invece di UNPARSED.
# Gli ignore sono sempre subordinati al contenuto operativo del messaggio:
# ogni parser controlla prima se il testo contiene un segnale/comando valido.

IGNORE_PATTERNS: dict[str, tuple[str, ...]] = {
    "sala_gold": (
        r"ZOOM\.US", r"SALA\s+APERT", r"FORMAZIONE", r"STASERA\s+FORM",
        r"TP\d*\s+HIT", r"PIPS\s+BREAK",
        r"SL\s+HIT", r"\bSL\s+\d{4}\b",
        r"\bBE\s+HIT\b", r"PAREGGIO\s+(?:PRESO|RAGGIUNTO|FATTO)",
        # comunicazioni broker/promo ricorrenti (arrivano su GOLD e su FOREX)
        r"COMUNICAZIONE\s+IMPORTANTE", r"CONTO\s+FINANZIAT", r"NUOVA\s+DASHBOARD",
        r"TRAMITE\s+QUESTA\s+GUIDA", r"POSSESSORI\s+DI\s+UN\s+CONTO",
        r"BE\s+O\s+TP", r"RICORDO\s+A\s+TUTTI", r"MINUTI\s+E\s+APRIAMO",
        r"ID\s+DE\s+REUNI", r"CODIGO\s+DE\s+ACCESO",
        r"LINK\s+.*LIVE", r"SALA\s+APERTAAAA",
        r"PIPS\s+\d", r"\+\d+\s+PIPS",
    ),
    "sala_vip": (
        r"GIORNALIERO\s+RAPPORTO", r"SETTIMANALE\s+RAPPORTO", r"REPORT\s+SETTIMANALE",
        r"SALA\s+APERT", r"ZOOM\.US", r"FORMAZIONE\s+STRATEG", r"LIVE\s+DI\s+FORMAZIONE",
        r"LEZIONE\s+LIVE", r"VIDEO\s+ANALISI", r"RICORDO\s+A\s+TUTTI",
        r"ENTRATE\s+TUTTI", r"LINK\s+.*LIVE", r"ID\s+DE\s+REUNI",
        r"NEL\s+TRADING\s+NON", r"QUESTI\s+SONO\s+I\s+RISULTATI",
        r"ORDINI\s+ANCORA\s+IN\s+ESECUZIONE",
        r"QUESTO MESSAGGIO NON INCITA",
        r"COMUNICAZIONE\s+IMPORTANTE", r"CONTO\s+FINANZIAT", r"NUOVA\s+DASHBOARD",
        r"TRAMITE\s+QUESTA\s+GUIDA", r"POSSESSORI\s+DI\s+UN\s+CONTO",
    ),
    # ORO: \b evita di ignorare parole contenute (LIVELLO conteneva LIVE) e
    # FORMAZIONE in upper-case (il pattern misto "FORMazione" non matchava mai).
    "sala_oro": (
        r"\bREPORT\b",
        r"BOOO?MM",
        r"\bRAGAZZI\b",
        r"\bPOTREBBE\b",
        r"\bATTENDIAMO\b",
        r"\bDOVREBBE\b",
        r"FORMAZIONE",
        r"\bLIVE\b",
        r"^\d+\s+PIPS?\s*✅\s*$",
    ),
    "sala_stark": (
        r"SPOSTO\s+LO\s+STOP\s+LOSS\s+A",
        r"BREAK\s*EVEN",
        r"TAKE\s+PROFIT\s*\d*\s+PRESO",
        r"TAKE\s+PROFIT\s+PRESO",
        r"STOP\s+LOSS\s+PRESO",
        r"CHIUSA\s+A\s+BREAK",
        r"CI\s+VEDIAMO\b",
        r"WEBINARJAM",
        r"LINK\s+PER\s+PRENOTARE",
        r"COME\s+POTETE\s+CAPIRE",
        r"MINUTI\s+PRIMA",
        r"SI\s+COMINCIA",
        r"ENTRATE\s+TUTTI",
        r"LINK\s+LIVE",
        r"RAGAZZI\s+COME\s+POTETE",
    ),
    "ivan_vip": (
        r"^SL$",
        r"^PECCATO$",
        r"BUONGIORNO",
        r"GTA\s+FRATELLI",
        r"SCREEN\s+DI\s+PROFITTI",
        r"STO\s+(?:SEMPRE\s+)?VALUTANDO",
        r"^PROVIAMO$",
        r"BOOO+MM",
        r"EHEHE",
        r"TP\s*\d+\s+HIT",
        r"BE\s+HIT",
        r"TAKE\s+PROFIT",
        r"GESTIAMO\s+A\s+MERCATO",
        r"SE\s+NON\s+LI\s+PIACE",
        r"PRONTI\s+A\s+CHIUDERE",
        r"VI\s+ERO\s+MANCATO",
        r"CECCHINO",
        r"INIZIO\s+SETTIMANA",
        r"SIAMO\s+IN\s+LIVE",
        r"TIK\s*TOK",
        r"YOUTUBE\.COM",
        r"^HTTPS?://",
    ),
}


def matched_ignore_pattern(parser_name: str, upper: str) -> str | None:
    """First ignore pattern matching an already normalised/upper text."""
    for pat in IGNORE_PATTERNS.get(parser_name, ()):
        if re.search(pat, upper):
            return pat
    return None

# ─────────────────────────────────────────────────────────────────────────────
# PARSER CH1 — ZANNI VIP SIGNALS
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato segnale (multiriga):
#   [emoji] BUY/SELL SIMBOLO ENTRY
#   TP1: xxx
#   TP2: xxx
#   TP3: xxx
#   SL:  xxx
#
# Azioni emesse:
#   OPEN              -> nuovo trade
#   CHECK_AND_BE      -> verifica se TP1 raggiunto, se no sposta SL a BE ora
#   CHECK_AND_CLOSE_TP -> verifica se TP N gia' chiuso, se no chiudi ora

def parser_zanni_vip(text: str, ch: dict) -> dict | None:
    raw   = text.strip()
    upper = strip_md(raw).upper()

    # ── 1. BE signal ─────────────────────────────────────────────────────────
    # "TP1 ✅ Spostiamo SL a BE" / "TP1 preso, sposto a BE" / "porto a BE"
    # Anche senza menzione TP1: "porto stop a BE", "sposto a BE", "metto a BE"
    be_with_tp1 = re.search(r"TP\s*1", upper) and contains_any(upper, " BE", "BREAK EVEN", "PAREGGIO")
    # "sposto stop a BE", "portiamo lo stop loss in pareggio", "metto a BE", …
    be_generic  = bool(re.search(
        r"\b(?:SPOST|PORT|METT)(?:O|IAMO|IATE|ATE)\s+"
        r"(?:(?:LO|IL|LA)\s+)?(?:STOP(?:\s+LOSS)?|SL)?\s*"
        r"(?:A|IN)\s+(?:BE|PAREGGIO|BREAK\s*EVEN)\b",
        upper,
    )) or contains_any(upper, "MOVE TO BE", "SET BE")
    if be_with_tp1 or be_generic:
        log.info(f"[CH1] CHECK_AND_BE: {raw[:60]}")
        return {"action": "CHECK_AND_BE", "tp_index": 1, "raw_message": raw}

    # ── 1b. "TP2 preso" / "chiudiamo TP3" → chiudi quel TP ─────────────────
    # IMPORTANTE: questo check viene PRIMA del generico per evitare falsi positivi
    m_tp_preso = re.search(r"TP\s*(\d)\s*(?:PRESO|HIT|DONE|TAKEN|✅)", upper)
    if m_tp_preso:
        tp_n = int(m_tp_preso.group(1))
        log.info(f"[CH1] CHECK_AND_CLOSE_TP{tp_n} (preso): {raw[:60]}")
        return {"action": "CHECK_AND_CLOSE_TP", "tp_index": tp_n, "raw_message": raw}

    # CHIUDO / CHIUDI / CHIUDETE / CHIUDIAMO + "tp N"
    m_cl = re.search(r"(?:CHIUD[OIEA]\w*|CLOSING)\b.*?TP\s*(\d)", upper)
    if m_cl:
        tp_n = int(m_cl.group(1))
        log.info(f"[CH1] CHECK_AND_CLOSE_TP{tp_n}: {raw[:60]}")
        return {"action": "CHECK_AND_CLOSE_TP", "tp_index": tp_n, "raw_message": raw}

    # ── 2a. Chiusura generica (shared recognizer) ────────────────────────────
    if re.search(r"CAMBIO\s+DI\s+TREND", upper):
        log.info(f"[CH1] CLOSE_ALL_SYMBOL (trend): {raw[:60]}")
        return _close_signal(ch, raw, symbol="XAUUSD")
    close_sig = _maybe_close_from_text(upper, ch, raw, symbol="XAUUSD")
    if close_sig:
        return close_sig

    # ── 3. Segnale di apertura ───────────────────────────────────────────────
    # Gestisce sia "BUY XAUUSD 4819" che "EURJPY BUY 186.942"
    # Il punto nel prezzo (186.942) non viene matchato da \w+ quindi
    # usiamo [A-Z]{3,8} per il simbolo e un pattern specifico per il prezzo
    m_dir = re.search(
        r"(?:([A-Z]{3,8})\s+)?(BUY|SELL)\s+(?:([A-Z]{3,8})\s+)?(\d+[.,]\d+|\d{3,})",
        upper
    )
    if not m_dir:
        return None

    sym1, direction, sym2, price_str = m_dir.groups()
    symbol = normalize_symbol(sym1 or sym2 or "")
    if not symbol:
        return None
    entry = pf(price_str)
    if entry is None:
        return None

    tps, sl = [], None
    for line in raw.splitlines():
        lu = strip_md(line).upper().strip()
        m_tp = re.match(r"TP\s*\d\s*[:\s]\s*([\d.,]+)", lu)
        if m_tp:
            v = pf(m_tp.group(1))
            if v is not None:
                tps.append(v)
            continue
        m_sl = re.match(r"SL\s*[:\s]\s*([\d.,]+)", lu)
        if m_sl:
            sl = pf(m_sl.group(1))

    if not tps and sl is None:
        return None

    log.info(f"[CH1] OPEN {direction} {symbol} @ {entry} TP={tps} SL={sl}")
    return {
        "action":       "OPEN",
        "direction":    direction,
        "symbol":       symbol,
        "entry":        entry,
        "tp_levels":    tps,
        "sl":           sl,
        "magic_base":   ch["magic_base"],
        "raw_message":  raw,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PARSER CH2 — SALA GOLD VIP
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato naked (apre subito a mercato):
#   "Gold sell now"  /  "Sell gold now"  /  "Gold buy now"
#
# Formato completo (aggiorna il trade naked appena aperto, oppure standalone):
#   "Sell gold now 4775 - 4780\nSL: 4789\nTp: 4768*\nTp: 4755"
#
# Entry range: [min, max] — EA valuta se il prezzo e' nel range (non usa la media)
# BE: AUTOMATICO nell'EA su TP1 hit — messaggi BE/SL/TP hit ignorati

# ── CH2 GOLD: il canale pubblica in italiano e poi edita in inglese ─────────
# I sinonimi vengono normalizzati verso la forma canonica inglese, così tutta la
# logica esistente (naked → OPEN_NOW, range, SL/TP, dedup) vale per entrambe le
# lingue. Registro additivo: nuove forme si aggiungono, nulla viene rimosso.

GOLD_SELL_WORDS = (
    r"VENDIAMO", r"VENDETE", r"VENDERE", r"VENDIMO", r"VENDI", r"VENDO",
    r"VENDITA", r"SHORTIAMO", r"SHORTA", r"SHORT",
    # spagnolo: il canale traduce lo stesso setup anche in ES
    r"VENDEMOS", r"VENDAN", r"VENDER", r"VENDE", r"VENTA",
)
GOLD_BUY_WORDS = (
    r"COMPRIAMO", r"COMPRATE", r"COMPRARE", r"COMPRA", r"COMPRO",
    r"ACQUISTIAMO", r"ACQUISTATE", r"ACQUISTARE", r"ACQUISTA", r"ACQUISTO",
    r"LONGHIAMO", r"LONG",
    # spagnolo
    r"COMPRAMOS", r"COMPREN", r"COMPRAR", r"COMPRE",
)
GOLD_ASSET_WORDS = (r"ORO", r"GOLD", r"XAUUSD", r"XAU\/USD", r"XAU")
GOLD_NOW_WORDS = (
    r"ADESSO", r"ORA", r"SUBITO", r"IMMEDIATAMENTE", r"A\s+MERCATO",
    r"SUL\s+MERCATO", r"NOW", r"MARKET",
    # spagnolo
    r"AHORA", r"YA", r"AL\s+MERCADO",
)

# Locuzioni che danno la direzione senza verbo, tipiche della traduzione
# automatica del canale: "Gold on sale now" (oro in vendita ora), "Oro a la
# venta ahora". Vanno canonicalizzate prima dei singoli verbi, altrimenti
# "VENTA"/"SALE" verrebbero riscritti lasciando la preposizione a metà frase.
GOLD_SELL_PHRASES = (
    r"ON\s+SALE", r"FOR\s+SALE", r"IN\s+VENDITA", r"A\s+LA\s+VENTA",
    r"EN\s+VENTA",
    # FR: il canale scrive anche "Tjrs en vente ici", "Or a vendre 4342-4350".
    r"EN\s+VENTE", r"A\s+VENDRE",
)
GOLD_BUY_PHRASES = (
    r"FOR\s+PURCHASE", r"ON\s+PURCHASE", r"IN\s+ACQUISTO", r"A\s+LA\s+COMPRA",
    r"EN\s+COMPRA",
    r"A\s+L\s*ACHAT", r"EN\s+ACHAT",
)

# Etichette che il canale mette tra direzione e prezzi: "BUY XAUUSD ZONE 4425 -
# 4422", "Sell gold @ 4407", "Precio actual del oro: 4414,8 - 4422".
_GOLD_ENTRY_LABEL = (
    r"(?:ZONE|ZONA|AREA|ENTRY|ENTRATA|ENTRADA|PRICE|PREZZO|PRECIO|ACTUAL|DEL|"
    r"LIVELLO|LEVEL|NIVEL|RANGE)"
)
# Separatore tra la direzione e il primo prezzo: spazi, ':', '@' ed etichette.
_GOLD_ENTRY_SEP = rf"(?:\s|:|@|{_GOLD_ENTRY_LABEL})*"

# "Typ" è il typo ricorrente del canale per "Tp" (compare anche come "T.P.").
_GOLD_TP_KW = r"(?:TP|TYP|T\.P\.?|TAKE\s*PROFIT)"

# "Go sell now gold !": il canale mette NOW anche prima dell'asset.
_GOLD_NOW_OPT = r"(?:\s+NOW)?"

_GOLD_PENDING_ORDER_RE = re.compile(
    r"\b(?:BUY|SELL)\s+(?:STOP|LIMIT)\b|\b(?:STOP|LIMIT)\s+(?:BUY|SELL)\b"
)

# "Canceled" / "Annullato": il canale ritira il setup appena pubblicato (anche
# nelle rese automatiche ES/FR del bot di traduzione).
_GOLD_CANCEL_RE = re.compile(r"\b(?:CANCEL\w*|ANNULL\w*|ANULAD\w*|ANNULE\w*)\b")

# "Annulliamo il TP3" non annulla il trade: se il messaggio nomina un livello
# resta materia dei recognizer di gestione, non una chiusura.
_GOLD_LEVEL_WORD_RE = re.compile(rf"\bSL\b|\b{_GOLD_TP_KW}\d*\b")

# Comando naked di apertura: usato anche per non ignorare i messaggi compositi
# ("SL hit | | Sell gold now": la prima parte è rumore, la seconda è un ordine).
_GOLD_OPEN_NOW_RE = re.compile(
    r"(?:BUY|SELL)(?:\s+NOW)?\s+(?:GOLD|XAUUSD)\s+NOW\b|"
    r"(?:BUY|SELL)\s+NOW\s+(?:GOLD|XAUUSD)\b|"
    r"(?:GOLD|XAUUSD)\s+(?:BUY|SELL)\s+NOW\b"
)


def _gold_canonicalize(upper: str) -> str:
    """Traduce i sinonimi italiani nella forma canonica 'BUY/SELL GOLD [NOW]'.

    "VENDI oro ora 4063 - 4070 | SL:4078" → "SELL GOLD NOW 4063 - 4070 | SL:4078"
    """
    # Emoji e frecce decorative ("ZONA ➡️ 4425 - 4422", "TP1 ➡️ 4429") spezzano
    # l'estrazione dei livelli: via i caratteri non ASCII, gli accenti restano
    # leggibili come lettere base.
    out = re.sub(r"[^\x00-\x7F]+", " ", fold_accents(upper))
    for phrase in GOLD_SELL_PHRASES:
        out = re.sub(rf"\b{phrase}\b", "SELL", out)
    for phrase in GOLD_BUY_PHRASES:
        out = re.sub(rf"\b{phrase}\b", "BUY", out)
    for word in GOLD_SELL_WORDS:
        out = re.sub(rf"\b{word}\b", "SELL", out)
    for word in GOLD_BUY_WORDS:
        out = re.sub(rf"\b{word}\b", "BUY", out)
    for word in GOLD_ASSET_WORDS:
        out = re.sub(rf"\b{word}\b", "GOLD", out)
    # "NOW" solo se segue direzione+asset: evita di riscrivere "ora" nel testo libero.
    now_alt = "|".join(GOLD_NOW_WORDS)
    out = re.sub(
        rf"\b((?:BUY|SELL)\s+GOLD|GOLD\s+(?:BUY|SELL))\s+(?:{now_alt})\b",
        r"\1 NOW",
        out,
    )
    # canale mono-asset: "Compriamo ora 4063" → "BUY NOW 4063" (asset implicito)
    out = re.sub(rf"\b(BUY|SELL)\s+(?:{now_alt})\s+(?=[\d])", r"\1 NOW ", out)
    return re.sub(r"  +", " ", out)


def _gold_has_full_setup(upper: str) -> bool:
    """Direzione + prezzo + almeno un livello: il messaggio è un setup, non rumore."""
    if not re.search(
        rf"(?:BUY|SELL){_GOLD_NOW_OPT}\s+(?:GOLD|XAUUSD)|(?:GOLD|XAUUSD)\s+(?:BUY|SELL)",
        upper,
    ):
        return False
    if not re.search(r"\d{3,}", upper):
        return False
    return bool(
        re.search(r"\bSL\b", upper) or re.search(rf"\b{_GOLD_TP_KW}\d*\b", upper)
    )


def _gold_has_levels_setup(upper: str) -> bool:
    """Setup senza il nome dell'asset: direzione + prezzo + SL + TP.

    Il canale è mono-asset (oro), quindi "Compriamo ora 4063 - 4070 SL 4055
    TP 4080" è un setup valido anche se la parola "oro" non compare.
    """
    if not re.search(r"\b(?:BUY|SELL)\b", upper):
        return False
    if not re.search(r"\d{3,}", upper):
        return False
    if not re.search(r"\bSL\b\s*[:\s]\s*[\d.,]+", upper):
        return False
    return bool(re.search(rf"\b{_GOLD_TP_KW}\d*\b\s*[:.\s]\s*[\d.,]+", upper))


def _gold_levels_setup_no_direction(upper: str) -> bool:
    """Prezzi + SL + TP ma nessuna direzione: la traduzione l'ha persa.

    "Precio actual del oro: 4414,8 - 4422 SL: 4429 Tp: 4405" è l'edit ES di un
    sell: la direzione si ricava dai livelli (SL da un lato, TP dall'altro).
    """
    if re.search(r"\b(?:BUY|SELL)\b", upper):
        return False
    if not re.search(r"\bSL\b\s*[:\s]\s*[\d.,]+", upper):
        return False
    return bool(re.search(rf"\b{_GOLD_TP_KW}\d*\b\s*[:.\s]\s*[\d.,]+", upper))


def _gold_infer_direction(entry: float | None, entry_range: list[float] | None,
                          sl: float | None, tps: list[float]) -> str | None:
    """Direzione dai livelli: SL da un lato dell'entry, tutti i TP dall'altro."""
    if sl is None or not tps or any(tp is None for tp in tps):
        return None
    if entry_range and len(entry_range) == 2:
        lo, hi = min(entry_range), max(entry_range)
    elif entry is not None:
        lo = hi = entry
    else:
        return None
    if sl < lo and all(tp > hi for tp in tps):
        return "BUY"
    if sl > hi and all(tp < lo for tp in tps):
        return "SELL"
    return None


GOLD_REPOST_TTL_SEC = 1800.0
# Il canale ripubblica lo stesso comando di gestione prima in italiano e poi in
# inglese (EDIT): entro questa finestra un comando equivalente non viene riemesso.
GOLD_CMD_DEDUP_TTL_SEC = 180.0


def _gold_is_repost(last: dict | None, signal: dict) -> bool:
    """Stesso setup rinviato entro TTL: evita il secondo trade stacked.

    Il canale pubblica il setup in italiano e poi lo edita in inglese ritoccando
    l'entry di qualche decimo (4076-4086 → 4076.8-4086): stessi SL e TP +
    range sovrapposti o vicini = stesso setup, non un secondo segnale.
    """
    if not last:
        return False
    ts = last.get("ts")
    if not isinstance(ts, (int, float)) or time.time() - ts > GOLD_REPOST_TTL_SEC:
        return False
    if last.get("direction") != signal.get("direction"):
        return False
    if last.get("sl") != signal.get("sl"):
        return False
    if list(last.get("tp_levels") or []) != list(signal.get("tp_levels") or []):
        return False
    if _entry_interval(last) == _entry_interval(signal):
        return True
    return _same_trade_setup(last, signal, max_gap=2.0)


# Un "Canceled" vale solo per un setup recente: più tardi il canale sta
# commentando qualcosa d'altro e non va chiusa una posizione ormai autonoma.
GOLD_CANCEL_TTL_SEC = 1800.0


# Allargare lo stop è una scelta legittima del canale (dare respiro al prezzo),
# ma la size è stata calcolata sul rischio precedente: oltre questo multiplo il
# nuovo SL viene fissato al limite invece di essere seguito senza tetto.
GOLD_MAX_SL_WIDEN_FACTOR = 2.0


def _gold_entry_ref(trade: dict | None) -> float | None:
    """Prezzo di riferimento del setup: entry, o centro della zona."""
    if not trade:
        return None
    entry = trade.get("entry")
    if isinstance(entry, (int, float)):
        return float(entry)
    er = trade.get("entry_range")
    if isinstance(er, (list, tuple)) and len(er) == 2 and all(
            isinstance(v, (int, float)) for v in er):
        return (float(er[0]) + float(er[1])) / 2.0
    return None


def _gold_cap_widened_sl(direction: str | None, entry: float | None,
                         old_sl: float | None, new_sl: float) -> float:
    """Limita un SL che allontana lo stop al doppio del rischio precedente.

    Il 24/08 il canale ha scritto "SL 4660" su un SELL a 4639.55 che aveva SL
    4656: lo spostamento si esegue (il canale sta dando respiro al prezzo), ma
    un allargamento fuori scala — o il typo di un numero — moltiplicherebbe la
    perdita massima su una size già calcolata. Oltre il tetto lo SL è fissato lì.
    """
    if old_sl is None or entry is None or direction not in ("BUY", "SELL"):
        return new_sl
    widens = new_sl > old_sl if direction == "SELL" else new_sl < old_sl
    if not widens:
        return new_sl
    risk = abs(entry - old_sl)
    if risk <= 0:
        return new_sl
    max_risk = risk * GOLD_MAX_SL_WIDEN_FACTOR
    if abs(entry - new_sl) <= max_risk:
        return new_sl
    capped = entry + max_risk if direction == "SELL" else entry - max_risk
    return round(capped, 2)


def _gold_cancel_signal(state: BridgeState, ch: dict, raw: str) -> dict | None:
    """"Canceled" sul setup appena pubblicato → chiusura di quel trade."""
    last = state.gold_last_trade if isinstance(state.gold_last_trade, dict) else None
    fresh = False
    if last:
        ts = last.get("ts")
        fresh = isinstance(ts, (int, float)) and (time.time() - ts) <= GOLD_CANCEL_TTL_SEC
    if not fresh and not state.ch2_pending_open:
        log.info(f"[CH2] Annullamento senza segnale recente, nessuna azione: {raw[:60]}")
        return None
    # La chiave include il setup annullato: il canale ripete l'annullamento in
    # inglese (dedup), ma un annullamento di un setup successivo deve passare.
    cmd_key = f"CANCEL:{(last or {}).get('ts', 'pending')}"
    if _gold_cmd_is_repeat(state, cmd_key):
        log.info(f"[CH2] Annullamento già emesso di recente, ignorato: {raw[:60]}")
        return None
    state.set_gold_last_cmd(cmd_key)
    state.clear_ch2_pending()
    state.clear_gold_last_trade()
    log.info(f"[CH2] CANCELED → CLOSE_ALL_SYMBOL XAUUSD: {raw[:60]}")
    return _close_signal(ch, raw, symbol="XAUUSD")


def _gold_cmd_is_repeat(state: BridgeState, key: str) -> bool:
    """True se lo stesso comando di gestione è già stato emesso entro il TTL."""
    last = state.gold_last_cmd
    if not isinstance(last, dict) or last.get("key") != key:
        return False
    ts = last.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    return (time.time() - ts) <= GOLD_CMD_DEDUP_TTL_SEC


def parser_sala_gold(text: str, ch: dict, state: BridgeState | None = None) -> dict | None:
    if state is None:
        state, _ = _ensure_runtime()
    raw   = text.strip()
    upper = _gold_canonicalize(strip_md(raw).upper())

    # ── Ordini pendenti: non supportati, ma non vanno eseguiti a mercato ──────
    # "Vendi stop oro 4065" → SELL STOP: ingresso differito che il sistema non
    # gestisce. Meglio nessun trade che un ingresso a mercato non richiesto.
    if _GOLD_PENDING_ORDER_RE.search(upper):
        log.warning(f"[CH2] Ordine pendente non supportato, nessun segnale: {raw[:60]}")
        return None

    # ── Annullamento del setup appena pubblicato ────────────────────────────
    # "Canceled" 21 secondi dopo un OPEN_NOW: il trade va chiuso, non lasciato
    # correre fino allo stop. Un messaggio che contiene anche un setup completo
    # non è un annullamento (il canale sta ripubblicando).
    if _GOLD_CANCEL_RE.search(upper) and not (
        _gold_has_full_setup(upper)
        or _gold_has_levels_setup(upper)
        or _GOLD_LEVEL_WORD_RE.search(upper)
        # "Formazione annullata", "sala di stasera annullata": non è un trade.
        or contains_any(upper, "FORMAZION", "SALA", "ZOOM", "LEZION", "WEBINAR")
    ):
        return _gold_cancel_signal(state, ch, raw)

    # ── Partial / half + break even → CLOSE_HALF_BE ─────────────────────────
    # Deve stare PRIMA del recognizer di chiusura totale: "+70 pips close half
    # break even close" finiva in CLOSE_ALL_SYMBOL per il "close" finale.
    # E prima del match "N gold break even", altrimenti
    # "Partial close, break even +100 pips" cattura la virgola come prezzo.
    is_partial = match_partial_close_intent(upper) or contains_any(
        upper,
        "CLOSE HALF", "PARTIAL CLOSE", "PARTIAL CLOSURE", "HALF CLOSE",
        "CHIUDI META", "PARZIALE", "CLOSE PART", "PARTIAL",
    )
    is_be_msg = bool(
        match_break_even_intent(upper)
        or contains_any(upper, "BREAK EVEN", "BREAKEVEN", "BREAK-EVEN")
    )
    # "Rompere anche la chiusura parziale" = resa automatica di "break even +
    # partial close": ROMPERE vale come break-even solo insieme al parziale.
    if is_partial and re.search(r"\bROMP\w*", upper):
        is_be_msg = True
    if is_partial and is_be_msg:
        if _gold_cmd_is_repeat(state, "CLOSE_HALF_BE"):
            log.info(f"[CH2] CLOSE_HALF_BE già emesso di recente, ignorato: {raw[:60]}")
            return None
        state.set_gold_last_cmd("CLOSE_HALF_BE")
        log.info(f"[CH2] CLOSE_HALF_BE: {raw[:60]}")
        return {"action": "CLOSE_HALF_BE", "symbol": "XAUUSD",
                "magic_base": ch["magic_base"], "raw_message": raw}

    # ── Chiusura totale CH2 (shared recognizer) ─────────────────────────────
    close_sig = _maybe_close_from_text(upper, ch, raw, state)
    if close_sig:
        cmd_key = f"{close_sig.get('action')}:{close_sig.get('close_price')}"
        if _gold_cmd_is_repeat(state, cmd_key):
            log.info(f"[CH2] Chiusura già emessa di recente, ignorata: {raw[:60]}")
            return None
        state.set_gold_last_cmd(cmd_key)
        state.clear_gold_last_trade()
        return close_sig

    # ── BE con prezzo esplicito: "4789 gold break Even" ─────────────────────
    # Richiede almeno 3 cifre (prezzo XAU), non pips (+100) né virgole isolate.
    _be_kw = r"(?:BREAK\s*-?\s*EVEN|BREAKEVEN|BREKIVEN|PAREGGI\w*)"
    m_be_price = re.search(
        rf"(?<!\d)(\d{{3,}}(?:[.,]\d+)?)\s+(?:GOLD|XAUUSD)?\s*{_be_kw}|"
        rf"{_be_kw}\s+(?:(?:IN|SU|SUL|A|AL|SULL')\s*)?(?:GOLD|XAUUSD)?\s*"
        rf"(?<!\d)(\d{{3,}}(?:[.,]\d+)?)",
        upper,
    )
    if m_be_price:
        raw_px = m_be_price.group(1) or m_be_price.group(2)
        be_price = pf(raw_px)
        if be_price is None or be_price < 100:
            log.debug(f"[CH2] BE price token ignorato: {raw_px!r} -> CHECK_AND_BE")
            log.info(f"[CH2] CHECK_AND_BE (break even, no usable price): {raw[:60]}")
            return {
                "action": "CHECK_AND_BE",
                "symbol": "XAUUSD",
                "tp_index": 1,
                "magic_base": ch["magic_base"],
                "raw_message": raw,
            }
        if _gold_cmd_is_repeat(state, f"BREAK_EVEN_PRICE:{be_price}"):
            log.info(f"[CH2] BE {be_price} già emesso di recente, ignorato: {raw[:60]}")
            return None
        state.set_gold_last_cmd(f"BREAK_EVEN_PRICE:{be_price}")
        log.info(f"[CH2] BE con prezzo esplicito: {be_price}")
        return {"action": "BREAK_EVEN_PRICE", "be_price": be_price,
                "symbol": "XAUUSD", "magic_base": ch["magic_base"], "raw_message": raw}

    # ── "break even" standalone / manual BE instruction → SL a entry ───────
    # Esempi: "break Even", "MANUALLY SET A BREAK EVEN ON ALL YOUR POSITIONS!"
    if is_be_msg or re.search(r"MANUALLY\s+SET\s+A\s+BREAK\s*EVEN", upper):
        if _gold_cmd_is_repeat(state, "CHECK_AND_BE"):
            log.info(f"[CH2] CHECK_AND_BE già emesso di recente, ignorato: {raw[:60]}")
            return None
        state.set_gold_last_cmd("CHECK_AND_BE")
        log.info(f"[CH2] CHECK_AND_BE (break even): {raw[:60]}")
        return {
            "action": "CHECK_AND_BE",
            "symbol": "XAUUSD",
            "tp_index": 1,
            "magic_base": ch["magic_base"],
            "raw_message": raw,
        }

    # ── SL spostato senza altro contesto: "SL 4807" ─────────────────────────
    # Solo se c'è un trade GOLD noto e il livello è diverso da quello corrente,
    # altrimenti è una ripetizione del setup appena inviato.
    m_sl_only = re.match(r"^SL\s*[:\s]\s*(\d{3,}(?:[.,]\d+)?)\s*$", upper)
    if m_sl_only:
        new_sl = pf(m_sl_only.group(1))
        last = state.gold_last_trade
        if new_sl is None or not last:
            log.debug(f"[CH2] SL standalone senza trade noto, ignorato: {raw[:60]}")
            return None
        if last.get("sl") == new_sl:
            log.debug(f"[CH2] SL standalone uguale al corrente ({new_sl}), ignorato")
            return None
        capped = _gold_cap_widened_sl(
            last.get("direction"), _gold_entry_ref(last), last.get("sl"), new_sl)
        if capped != new_sl:
            log.warning(
                f"[CH2] UPDATE_SL limitato: {new_sl} allarga il rischio oltre "
                f"{GOLD_MAX_SL_WIDEN_FACTOR}x (SL corrente {last.get('sl')}), "
                f"applicato {capped}: {raw[:60]}"
            )
            new_sl = capped
        if last.get("sl") == new_sl:
            log.debug(f"[CH2] SL standalone uguale al corrente ({new_sl}), ignorato")
            return None
        state.set_gold_last_trade({**last, "sl": new_sl})
        log.info(f"[CH2] UPDATE_SL {last.get('direction')} XAUUSD SL={new_sl}")
        return {
            "action":      "UPDATE_SL",
            "direction":   last.get("direction"),
            "symbol":      "XAUUSD",
            "new_sl":      new_sl,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    # ── Messaggi da ignorare completamente ───────────────────────────────────
    # Solo se il messaggio non contiene un setup completo: i canali mescolano
    # spesso il segnale con testo promozionale ("+70 pips", "formazione", …).
    # Un comando di apertura dentro un messaggio composito ("SL hit | | Sell
    # gold now") vale come segnale: l'ignore copre solo la parte di cronaca.
    pat = matched_ignore_pattern("sala_gold", upper)
    if pat and not (
        _gold_has_full_setup(upper)
        or _gold_has_levels_setup(upper)
        or _GOLD_OPEN_NOW_RE.search(upper)
    ):
        log.debug(f"[CH2] Ignorato ({pat}): {raw[:60]}")
        return None

    # ── Controlla se ci sono numeri significativi nel testo ──────────────────
    has_numbers = bool(re.search(r"\d{3,}", upper))

    # ── Determina direzione ───────────────────────────────────────────────────
    m_dir = re.search(
        rf"(BUY|SELL){_GOLD_NOW_OPT}\s+(?:GOLD|XAUUSD)(?:\s+NOW)?|"
        r"(?:GOLD|XAUUSD)\s+(BUY|SELL)(?:\s+NOW)?",
        upper
    )
    # Fallback: canale mono-asset, la direzione può arrivare senza nominare l'oro
    # ("Compriamo ora 4063 - 4070 SL 4055 TP 4080"). Solo con setup completo,
    # così una frase di commento non apre nulla.
    if not m_dir and _gold_has_levels_setup(upper):
        m_dir = re.search(r"\b(BUY|SELL)\b(?:\s+NOW)?", upper)
    # Setup completo con la direzione persa dalla traduzione ("Precio actual del
    # oro: … SL … Tp …"): si prosegue e la si ricava dai livelli più sotto.
    infer_direction = m_dir is None and _gold_levels_setup_no_direction(upper)
    if not m_dir and not infer_direction:
        return None

    direction = None
    if m_dir:
        direction = m_dir.group(1) or (
            m_dir.group(2) if m_dir.lastindex and m_dir.lastindex >= 2 else None
        )

    # ── NAKED: messaggio senza numeri → OPEN_NOW a mercato ───────────────────
    if not has_numbers and direction:
        state.set_ch2_pending(direction)
        log.info(f"[CH2] OPEN_NOW (naked) {direction} XAUUSD — in attesa completamento")
        return {
            "action":       "OPEN_NOW",
            "direction":    direction,
            "symbol":       "XAUUSD",
            "entry":        None,
            "entry_range":  None,
            "tp_levels":    [],
            "sl":           None,
            "magic_base":   ch["magic_base"],
            "raw_message":  raw,
        }

    # ── Segnale completo con numeri ───────────────────────────────────────────
    # Entry (possibile range)
    # Il separatore tra direzione e prezzi ammette ":", "@" ed etichette di zona
    # ("Buy gold now: 4319 - 4310", "BUY XAUUSD ZONE 4425 - 4422").
    entry_raw_m = re.search(
        rf"(?:BUY|SELL){_GOLD_NOW_OPT}\s+(?:GOLD|XAUUSD)(?:\s+NOW)?{_GOLD_ENTRY_SEP}([\d.,\s\-]+)|"
        rf"(?:GOLD|XAUUSD)\s+(?:BUY|SELL)(?:\s+NOW)?{_GOLD_ENTRY_SEP}([\d.,\s\-]+)",
        upper
    )
    if not entry_raw_m:
        # forma senza asset: "BUY NOW 4063 - 4070" (canale mono-asset)
        entry_raw_m = re.search(
            rf"(?:BUY|SELL)(?:\s+NOW)?{_GOLD_ENTRY_SEP}([\d.,\s\-]+)", upper
        )
    entry_range = None
    entry       = None
    raw_entry = ""
    if entry_raw_m:
        raw_entry = next((g for g in entry_raw_m.groups() if g), "").strip()
    elif infer_direction:
        # Senza direzione i prezzi d'ingresso sono quelli che precedono SL/TP.
        head = re.split(rf"\bSL\b|\b{_GOLD_TP_KW}\d*\b", upper)[0]
        raw_entry = " ".join(re.findall(r"\d{3,}(?:[.,]\d+)?", head))
    if raw_entry:
        parts     = re.findall(r"[\d.,]+", raw_entry)
        if len(parts) >= 2:
            v1 = pf(parts[0])
            v2 = pf(parts[1])
            # Sanity check: se differenza > 500 punti, uno dei due è malformato
            # Usa solo il valore più grande (più vicino al prezzo reale)
            if abs(v1 - v2) > 500:
                log.warning(f"[CH2] Range anomalo [{v1},{v2}], uso solo il valore più grande")
                entry = max(v1, v2)
                entry_range = None
            else:
                entry_range = [min(v1, v2), max(v1, v2)]
                entry       = None
        elif len(parts) == 1:
            entry = pf(parts[0])

    # SL — cerca "SL: 4789" o riga "SL 4828"
    sl = None
    m_sl = re.search(r"\bSL\b\s*[:\s]\s*([\d.,]+)", upper)
    if m_sl:
        sl = pf(m_sl.group(1))

    # TP — gestisce "Tp: 4768*", "Tp. 4824", "TP1: 5195" e il typo "Typ: 4370"
    tp_matches = re.findall(rf"{_GOLD_TP_KW}\d*\s*[:.]\s*([\d.,]+)\*?", upper)
    if not tp_matches:
        # forma senza separatore: "TP 4080", "TP1 4069"
        tp_matches = re.findall(rf"\b{_GOLD_TP_KW}\d*\s+([\d.,]+)\*?", upper)
    tps        = [pf(v) for v in tp_matches]

    if direction is None:
        direction = _gold_infer_direction(entry, entry_range, sl, tps)
        if direction is None:
            log.debug(f"[CH2] Setup senza direzione ricavabile, ignorato: {raw[:60]}")
            return None
        log.info(f"[CH2] Direzione ricavata dai livelli: {direction}")

    signal = {
        "action":       "OPEN",
        "direction":    direction,
        "symbol":       "XAUUSD",
        "entry":        entry,
        "entry_range":  entry_range,
        "tp_levels":    tps,
        "sl":           sl,
        "magic_base":   ch["magic_base"],
        "raw_message":  raw,
    }

    # Determina se e' completamento di un OPEN_NOW o nuovo segnale standalone
    if state.ch2_pending_open and state.ch2_pending_dir == direction:
        # L'edit del naked che aggiunge solo il prezzo ("Go sell now gold ! 4357")
        # non porta livelli: un UPDATE_OPEN senza SL/TP non protegge nulla e su un
        # terminale senza posizioni aprirebbe allo scoperto. Resta in attesa.
        if sl is None and not tps:
            log.info(f"[CH2] Completamento senza SL/TP, resto in attesa: {raw[:60]}")
            return None
        signal["action"] = "UPDATE_OPEN"
        state.clear_ch2_pending()
        log.info(f"[CH2] UPDATE_OPEN {direction} range={entry_range} TP={tps} SL={sl}")
    else:
        state.clear_ch2_pending()
        if _gold_is_repost(state.gold_last_trade, signal):
            log.info(f"[CH2] Setup identico già inviato, ignorato: {raw[:60]}")
            return None
        log.info(f"[CH2] OPEN {direction} range={entry_range} TP={tps} SL={sl}")

    state.set_gold_last_trade(signal)
    return signal


# ─────────────────────────────────────────────────────────────────────────────
# PARSER CH3 — SALA VIP
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato apertura:
#   NUOVO ORDINE - XAUUSDpm Buy
#   Entrata: 4736.64 [Lotti: 0.01]   <- lotto ignorato
#   Nessuno SL / Nessuno TP
#
# Formato modifica:
#   XAUUSDpm Buy - Modificato
#   Nuovo TP: 4747.00 [103.6 Pips]
#   Nuovo SL: 1.64356 [15.9 Pips]
#   Stop spostato a pareggio         <- SL e' gia' il valore numerico di BE
#
# Formato chiusura:
#   CHIUSO - XAUUSDpm Buy            <- verifica se gia' chiuso, altrimenti chiudi

# Registro additivo dei termini usati dal canale FOREX (bot IT + EN).
_FX_DIR = r"(?:BUY|SELL|LONG|SHORT|COMPRA|VENDI|ACQUISTA|VENDITA|ACQUISTO)"
_FX_SEP = r"\s*(?:[-–—:|]|\s)\s*"
_FX_NEW = r"(?:NUOVO\s+ORDINE|NUOVA\s+OPERAZIONE|NUOVO\s+TRADE|ORDINE\s+APERTO|" \
          r"NEW\s+ORDER|NEW\s+TRADE|NEW\s+POSITION|ORDER\s+OPENED|OPENED)"
_FX_MOD = r"(?:MODIFICAT[OA]|MODIFICA|MODIFIED|UPDATED|UPDATE|AGGIORNAT[OA]|" \
          r"SPOSTAT[OA]|MOVED)"
_FX_CLOSE = r"(?:CHIUS[OAE]|CHIUSURA|CLOSED|CLOSE|EXIT|EXITED|TERMINAT[OA]|USCIT[OAI])"
_FX_TP = r"(?:TP|TAKE\s*PROFIT)"
_FX_SL = r"(?:SL|STOP\s*LOSS|STOP)"
_FX_LEVEL_PREFIX = r"(?:NUOVO|NUOVA|NEW|MODIFICATO|AGGIORNATO|SET|SPOSTATO|MOVED)"
_FX_ENTRY_LABEL = r"(?:ENTRATA|ENTRY|PREZZO|PRICE|APERTURA|OPEN(?:ED)?\s+AT)"


def _fx_direction(token: str) -> str | None:
    """Normalizza la direzione del canale FOREX (IT/EN) su BUY/SELL."""
    t = (token or "").upper()
    if t in ("BUY", "LONG", "COMPRA", "ACQUISTA", "ACQUISTO"):
        return "BUY"
    if t in ("SELL", "SHORT", "VENDI", "VENDITA"):
        return "SELL"
    return None


def parser_sala_vip(text: str, ch: dict, state: BridgeState | None = None) -> dict | None:
    if state is None:
        state, _ = _ensure_runtime()
    raw   = text.strip()
    upper = strip_md(raw).upper()

    # ── Apertura (IT / EN) — TP arriva nel messaggio Modified successivo ─────
    # Separatore e ordine simbolo/direzione tollerati in tutte le varianti viste.
    m_new = re.search(
        rf"{_FX_NEW}{_FX_SEP}(\w+)\s+({_FX_DIR})\b", upper
    ) or re.search(
        rf"(\w+)\s+({_FX_DIR}){_FX_SEP}{_FX_NEW}", upper
    )
    m_mod = re.search(
        rf"(\w+)\s+({_FX_DIR}){_FX_SEP}{_FX_MOD}", upper
    ) or re.search(
        rf"{_FX_MOD}{_FX_SEP}(\w+)\s+({_FX_DIR})\b", upper
    )
    m_close = re.search(
        rf"{_FX_CLOSE}{_FX_SEP}(\w+)\s+({_FX_DIR})\b", upper
    ) or re.search(
        rf"(\w+)\s+({_FX_DIR}){_FX_SEP}{_FX_CLOSE}\b", upper
    )
    # Un messaggio di modifica cita spesso anche "chiuso parzialmente"/"stop
    # spostato": la modifica ha precedenza sulla chiusura solo se porta livelli.
    if m_mod and m_close and re.search(rf"{_FX_LEVEL_PREFIX}\s+{_FX_TP}|{_FX_LEVEL_PREFIX}\s+{_FX_SL}", upper):
        m_close = None

    # ── Messaggi da ignorare ─────────────────────────────────────────────────
    # Solo se non c'è un blocco ordine: il canale allega il disclaimer
    # "Questo messaggio non incita a investire" a NUOVO ORDINE e CHIUSO, che
    # finivano quindi ignorati (nessun OPEN e nessun CHECK_AND_CLOSE emesso).
    if not (m_new or m_mod or m_close):
        pat = matched_ignore_pattern("sala_vip", upper)
        if pat:
            log.debug(f"[CH3] Ignorato ({pat}): {raw[:60]}")
            return None

    if m_new:
        symbol    = normalize_symbol(m_new.group(1))
        direction = _fx_direction(m_new.group(2))
        if direction is None:
            return None
        m_entry   = re.search(rf"{_FX_ENTRY_LABEL}\s*[:\s]\s*([\d.,]+)", upper)
        entry     = pf(m_entry.group(1)) if m_entry else None
        # "No SL"/"Nessuno TP" non portano numeri e non matchano: il livello
        # entra nel segnale solo se il canale lo scrive davvero.
        m_nsl     = re.search(rf"(?<!NO )(?<!NESSUNO ){_FX_SL}\s*:\s*([\d.,]+)", upper)
        m_ntp     = re.search(rf"(?<!NO )(?<!NESSUNO ){_FX_TP}\s*:\s*([\d.,]+)", upper)
        sl        = pf(m_nsl.group(1)) if m_nsl else None
        tp        = pf(m_ntp.group(1)) if m_ntp else None
        if sl is not None and sl <= 0:
            sl = None
        tps       = [tp] if tp is not None and tp > 0 else []
        # L'ordine nuovo apre subito, anche senza SL/TP: è l'unico messaggio che
        # dichiara un'apertura. Prima restava solo pending e l'apertura veniva
        # emessa dal Modified successivo, cioè da una modifica di livello.
        signal = {
            "action":      "OPEN",
            "direction":   direction,
            "symbol":      symbol,
            "entry":       entry,
            "tp_levels":   tps,
            "sl":          sl,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }
        log.info(
            f"[FOREX] OPEN (nuovo ordine) {direction} {symbol} @{entry} "
            f"TP={tps} SL={sl}"
        )
        state.set_forex_last_trade(signal)
        state.clear_forex_pending()
        return signal

    # ── Modifica TP/SL (IT / EN) ─────────────────────────────────────────────
    if m_mod:
        symbol    = normalize_symbol(m_mod.group(1))
        direction = _fx_direction(m_mod.group(2))
        if direction is None:
            return None

        m_tp = re.search(rf"{_FX_LEVEL_PREFIX}\s+{_FX_TP}\s*[:\s]\s*([\d.,]+)", upper)
        m_sl = re.search(rf"{_FX_LEVEL_PREFIX}\s+{_FX_SL}\s*[:\s]\s*([\d.,]+)", upper)

        if m_tp:
            tp_val = pf(m_tp.group(1))
            sl_val = pf(m_sl.group(1)) if m_sl else None
            # Solo per uno stato scritto dalle versioni precedenti, dove il
            # nuovo ordine restava pending senza aprire: il pending non viene
            # piu' creato, quindi una modifica non puo' piu' generare un OPEN.
            if (
                state.forex_pending_symbol == symbol
                and state.forex_pending_dir == direction
            ):
                entry = state.forex_pending_entry
                log.info(
                    f"[FOREX] OPEN (pending+modified) {direction} {symbol} "
                    f"@{entry} TP={tp_val} SL={sl_val}"
                )
                signal = {
                    "action":      "OPEN",
                    "direction":   direction,
                    "symbol":      symbol,
                    "entry":       entry,
                    "tp_levels":   [tp_val],
                    "sl":          sl_val,
                    "magic_base":  ch["magic_base"],
                    "raw_message": raw,
                }
                state.set_forex_last_trade(signal)
                state.clear_forex_pending()
                return signal

            known_trade = bool(
                state.forex_last_trade
                and state.forex_last_trade.get("symbol") == symbol
                and state.forex_last_trade.get("direction") == direction
            )
            last_entry = (
                state.forex_last_trade.get("entry") if known_trade else None
            )
            if known_trade:
                state.set_forex_last_trade({
                    **state.forex_last_trade,
                    "tp_levels": [tp_val],
                    "sl": sl_val or state.forex_last_trade.get("sl"),
                })
            # TP e SL nello stesso messaggio: UPDATE_TP porterebbe solo il TP e
            # lo stop nuovo andrebbe perso. UPDATE_OPEN è la sola azione che
            # applica entrambi i livelli alle posizioni già aperte.
            if sl_val is not None and known_trade:
                log.info(
                    f"[FOREX] UPDATE_OPEN (TP+SL) {symbol} {direction} "
                    f"TP={tp_val} SL={sl_val}"
                )
                return {
                    "action":      "UPDATE_OPEN",
                    "symbol":      symbol,
                    "direction":   direction,
                    "entry":       last_entry,
                    "tp_levels":   [tp_val],
                    "sl":          sl_val,
                    "magic_base":  ch["magic_base"],
                    "raw_message": raw,
                }
            log.info(f"[FOREX] UPDATE_TP {symbol} {direction} TP={tp_val}")
            return {
                "action":      "UPDATE_TP",
                "symbol":      symbol,
                "direction":   direction,
                "new_tp":      tp_val,
                "tp_levels":   [tp_val],
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }

        if m_sl:
            sl_val = pf(m_sl.group(1))
            # Come sopra: solo per un pending residuo di una versione
            # precedente, altrimenti quel segnale resterebbe in attesa.
            # Il pending non viene piu' creato dal nuovo ordine.
            if (
                state.forex_pending_symbol == symbol
                and state.forex_pending_dir == direction
            ):
                entry = state.forex_pending_entry
                log.info(
                    f"[FOREX] OPEN (pending+SL) {direction} {symbol} @{entry} SL={sl_val}"
                )
                signal = {
                    "action":      "OPEN",
                    "direction":   direction,
                    "symbol":      symbol,
                    "entry":       entry,
                    "tp_levels":   [],
                    "sl":          sl_val,
                    "magic_base":  ch["magic_base"],
                    "raw_message": raw,
                }
                state.set_forex_last_trade(signal)
                state.clear_forex_pending()
                return signal
            is_be  = match_break_even_intent(upper) or contains_any(
                upper, "PAREGGIO", "BREAK EVEN", "BREAKEVEN"
            )
            if state.forex_last_trade and state.forex_last_trade.get("symbol") == symbol:
                state.set_forex_last_trade({**state.forex_last_trade, "sl": sl_val})
            log.info(f"[FOREX] UPDATE_SL {symbol} {direction} SL={sl_val} be={is_be}")
            return {
                "action":      "UPDATE_SL",
                "symbol":      symbol,
                "direction":   direction,
                "new_sl":      sl_val,
                "is_be":       is_be,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }

        return None

    # ── Chiusura (IT / EN) ────────────────────────────────────────────────────
    if m_close:
        symbol    = normalize_symbol(m_close.group(1))
        direction = _fx_direction(m_close.group(2))
        if direction is None:
            return None
        if (
            state.forex_pending_symbol == symbol
            and state.forex_pending_dir == direction
        ):
            state.clear_forex_pending()
        if (
            state.forex_last_trade
            and state.forex_last_trade.get("symbol") == symbol
            and state.forex_last_trade.get("direction") == direction
        ):
            state.clear_forex_last_trade()
        log.info(f"[CH3] CHECK_AND_CLOSE {symbol} {direction}")
        return {
            "action":      "CHECK_AND_CLOSE",
            "symbol":      symbol,
            "direction":   direction,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# PARSER SALA ORO VIP
# ─────────────────────────────────────────────────────────────────────────────
#
# Canale sempre XAUUSD. Formati:
#   XAUUSD SELL 4020-4022 | TP 4010 | SL 4024
#   4020 sell  /  sell 4020  (senza simbolo)
#   Tp 4010 | Sl 4024  (completamento edit)
#   60 PIPS CLOSE OR BREKIVEN  -> CLOSE_HALF_BE (meta' + BE)

def _parse_oro_sl_tp(upper: str) -> tuple[float | None, list[float]]:
    sl = None
    m_sl = re.search(r"\bSL\s*[:\s|]\s*([\d.,]+)", upper)
    if m_sl:
        sl = pf(m_sl.group(1))
    tps = [pf(v) for v in re.findall(r"TP\d*\s*[:\s|]\s*([\d.,]+)", upper)]
    if not tps:
        tps = [pf(v) for v in re.findall(r"\bTP\s+([\d.,]+)", upper)]
    return sl, tps


def _parse_oro_direction_entry(upper: str) -> tuple[str | None, float | None, list[float] | None]:
    m = re.search(
        r"ZONA\s+(BUY|SELL)\s+([\d.,]+)\s*-\s*([\d.,]+)",
        upper,
    )
    if m:
        direction = m.group(1)
        v1 = pf(m.group(2))
        v2 = pf(m.group(3))
        return direction, None, [min(v1, v2), max(v1, v2)]

    # "3998-3995 zona buy": range prima della parola ZONA
    m = re.search(r"([\d.,]+)\s*-\s*([\d.,]+)\s+ZONA\s+(BUY|SELL)", upper)
    if m:
        v1 = pf(m.group(1))
        v2 = pf(m.group(2))
        if v1 is not None and v2 is not None:
            return m.group(3), None, [min(v1, v2), max(v1, v2)]

    m = re.search(
        r"(?:XAUUSD|GOLD)\s+(BUY|SELL)\s+([\d.,]+)(?:\s*-\s*([\d.,]+))?",
        upper,
    )
    if m:
        direction = m.group(1)
        entry1 = pf(m.group(2))
        entry2 = pf(m.group(3)) if m.group(3) else None
        if entry2 is not None:
            return direction, None, [min(entry1, entry2), max(entry1, entry2)]
        return direction, entry1, None

    # "Buy now 4042" / "SELL NOW @4038" — NOW must not block BUY/SELL + price
    m = re.search(r"\b(BUY|SELL)\s+NOW\s*@?\s*([\d.,]+)", upper)
    if m:
        return m.group(1), pf(m.group(2)), None
    m = re.search(r"\bNOW\s+(BUY|SELL)\s*@?\s*([\d.,]+)", upper)
    if m:
        return m.group(1), pf(m.group(2)), None

    m = re.search(r"([\d.,]+)\s+(BUY|SELL)\b", upper)
    if m:
        return m.group(2), pf(m.group(1)), None

    m = re.search(r"\b(BUY|SELL)\s+@?\s*([\d.,]+)", upper)
    if m:
        return m.group(1), pf(m.group(2)), None

    return None, None, None


def _oro_is_close_half_be(upper: str) -> bool:
    # Forma esplicita: "chiusura parziale in pareggio" (senza conteggio pips)
    if match_partial_close_intent(upper) and match_break_even_intent(upper):
        return True
    if not re.search(r"\d+\s*PIPS?", upper):
        return False
    return contains_any(
        upper,
        "CLOSE",
        "BREK",
        "BREAK",
        "BREAKEVEN",
        " BE ",
    )


def _is_reentry_intent(upper: str) -> bool:
    """Rientriamo / rientrate ora / riapriamo: riapri sull'ultimo setup del canale.

    Registro additivo: comprende anche le forme di entrata aggiuntiva viste in
    produzione ("Okay dentro anche da qui", "Rientri piccola da qui a 58",
    "E altra metà sui 4057").
    """
    folded = fold_accents(upper)
    return bool(
        re.search(r"\bRIENTR(?:O|I|IAMO|ATE|IAMOCI|ARE|IAMO\w*)\w*\b", folded)
        or re.search(r"\bRIAPR(?:O|IAMO|ITE|IRE)\b", folded)
        or re.search(r"\bRE[- ]?ENTR(?:Y|IAMO)\b", folded)
        or re.search(r"\bDENTRO\s+(?:ANCHE|PURE|ALTRA|ALTRO)\b", folded)
        or re.search(r"\b(?:ENTRIAMO|ENTRATE|ENTRA)\s+(?:ANCHE|PURE|ALTRA)\b", folded)
        or re.search(r"\bAGGIUNGIAMO\b|\bAGGIUNGO\b|\bADD\s+(?:ANOTHER|MORE)\b", folded)
        or re.search(r"\b(?:E\s+)?ALTRA\s+(?:META|MEZZA|SIZE|PARTE)\b", folded)
    )


_REENTRY_IMMEDIATE = (
    r"\bORA\b|\bADESSO\b|\bSUBITO\b|\bDA\s+QUI\b|\bQUI\b|\bA\s+MERCATO\b|"
    r"\bNOW\b|\bMARKET\b|\bDENTRO\b|\bOK(?:AY|EY)?\b"
)

_REENTRY_DEFERRED = (
    r"\bASPETT(?:IAMO|O|ATE|A|ANDO|IAMOCI)\b|\bATTENDIAMO\b|\bPOI\b|\bDOPO\b|"
    r"\bPIU\s+TARDI\b|\bPRONTI\b|\bCON\s+CALMA\b|"
    r"\bPAZIENZA\b|\bDOMANI\b|\bLATER\b|\bWAIT\b|"
    # Annuncio di una zona/livello: descrive dove si rientrerà, non ordina nulla
    # ("Zona reentry 56-53" aveva aperto a mercato).
    r"\bZON[AE]\b|\bAREA\b|\bLIVELL[OI]\b|\bRANGE\b|\bZONE\b"
)

# Condizionali forti: l'ordine è subordinato a un evento futuro, quindi non vale
# nemmeno con un marcatore operativo nella frase ("Se ritraccia rientriamo qui").
# Valgono solo se precedono il verbo di rientro: in "Rientriamo ora anche se il
# volume è basso" la subordinata non annulla l'ordine già dato.
_REENTRY_CONDITIONAL = (
    r"\bSE\b|\bQUANDO\b|\bAPPENA\b|\bIN\s+CASO\b|\bEVENTUALMENTE\b|"
    r"\bMAGARI\b|\bFORSE\b|\bPROBABILMENTE\b|\bSPERIAMO\b|\bVALUTIAMO\b|"
    r"\bVALUTO\b|\bVEDIAMO\b|\bPOTREMMO\b|\bPOTREI\b|\bMAYBE\b|\bIF\b"
)
# Condizione di prezzo futura: vale in qualunque posizione della frase.
_REENTRY_CONDITIONAL_ALWAYS = r"\bRITRA[CX]\w*|\bPULLBACK\b|\bSTORNO\b"
_REENTRY_VERB = (
    r"\bRIENTR\w*|\bRIAPR\w*|\bRE[- ]?ENTR\w*|\bDENTRO\b|\bENTR(?:IAMO|ATE|A)\b|"
    r"\bAGGIUNG\w*"
)


def _conditional_precedes_reentry(folded: str) -> bool:
    """True se un condizionale ('se', 'quando', …) apre la frase del rientro."""
    m_cond = re.search(_REENTRY_CONDITIONAL, folded)
    if not m_cond:
        return False
    m_verb = re.search(_REENTRY_VERB, folded)
    return m_verb is None or m_cond.start() < m_verb.start()


_REENTRY_NOT_YET = (
    r"\bRIENTRER(?:EMO|EMMO|O|EI|ESTE|ANNO|A)\b|\bRIAPRIR(?:EMO|EMMO|O|EI|ANNO)\b|"
    r"\bENTRER(?:EMO|EMMO|O|EI|ANNO)\b"
)

# Desiderativi: il canale esprime un'intenzione, non un ordine ("Vorrei
# rientrare sell ehhh" aveva aperto quattro posizioni a mercato). Valgono in
# qualunque posizione della frase e battono i marcatori operativi.
_REENTRY_WISH = (
    r"\bVORREI\b|\bVORREMMO\b|\bVORREBBE\b|\bVOLEVO\b|\bVOLEVAMO\b|"
    r"\bMI\s+PIACEREBBE\b|\bCI\s+PIACEREBBE\b|\bPIACEREBBE\b|"
    r"\bSAREBBE\s+(?:BELLO|MEGLIO|IDEALE|DA)\b|\bMI\s+SA\s+CHE\b|"
    r"\bPENSAVO\s+DI\b|\bPENSO\s+DI\b|\bSPEREREI\b|\bIDEA\s+(?:DI|E)\b|"
    r"\bWOULD\s+LIKE\b|\bI\s+WANT\s+TO\b|\bTHINKING\s+OF\b"
)


def _is_deferred_reentry(upper: str) -> bool:
    """True per le frasi di attesa: preannunciano un rientro, non lo ordinano.

    Es. "Aspettiamo migliori conferme e poi rientriamo con calma" — che in
    produzione aveva aperto quattro posizioni a mercato. Un marcatore operativo
    esplicito ("ora", "da qui", "a mercato") ha comunque la precedenza.
    """
    folded = fold_accents(upper)
    if re.search(_REENTRY_NOT_YET, folded):
        return True
    if re.search(_REENTRY_WISH, folded):
        return True
    if re.search(_REENTRY_CONDITIONAL_ALWAYS, folded):
        return True
    if _conditional_precedes_reentry(folded):
        return True
    if not re.search(_REENTRY_DEFERRED, folded):
        return False
    return not re.search(_REENTRY_IMMEDIATE, folded)


def _expand_short_price(value: float, reference: float | None) -> float:
    """'da qui a 58' con ultimo entry 4054 → 4058 (abbreviazione del canale)."""
    if reference is None or value >= 100:
        return value
    base = math.floor(reference / 100.0) * 100.0
    candidates = [base + value, base + value + 100.0, base + value - 100.0]
    return min(candidates, key=lambda c: abs(c - reference))


def _reduced_size_factor(upper: str) -> float | None:
    """'piccola', 'metà size', 'size ridotta' → lot_factor 0.5."""
    folded = fold_accents(upper)
    if re.search(
        r"\bPICCOL[AEIO]\b|\bPICCOLINA\b|\bRIDOTT[AEIO]\b|\bMINI\b|"
        r"\b(?:META|MEZZA|HALF)\s*(?:SIZE|SAZIE|POSIZIONE)?\b|\bSMALL\b",
        folded,
    ):
        return 0.5
    return None


IVAN_REENTRY_DEDUP_TTL_SEC = 180.0
IVAN_REENTRY_DEDUP_MAX_GAP = 3.0

# Apertura del canale: "XAUUSD SELL 4011", "XAUUSD BUY: 4591-4587" (dal 26/08 il
# canale scrive i due punti dopo la direzione e talvolta una zona d'ingresso).
_IVAN_OPEN_RE = (
    r"(XAUUSD|GOLD)\s+(BUY|SELL)\s*[:@]?\s*(\d+(?:[.,]\d+)?)"
    r"(?:\s*-\s*(\d+(?:[.,]\d+)?))?"
)


def _ivan_entry_ref(trade: dict | None) -> float | None:
    """Prezzo di riferimento del setup: entry, o centro della zona d'ingresso."""
    if not trade:
        return None
    entry = trade.get("entry")
    if isinstance(entry, (int, float)):
        return float(entry)
    er = trade.get("entry_range")
    if (isinstance(er, (list, tuple)) and len(er) == 2
            and all(isinstance(v, (int, float)) for v in er)):
        return (float(er[0]) + float(er[1])) / 2.0
    return None


def _ivan_reentry_is_repeat(last: dict | None, entry: float | None) -> bool:
    """True se lo stesso rientro è già stato emesso da poco (MSG poi EDIT)."""
    if not isinstance(last, dict) or not last.get("allow_stack"):
        return False
    ts = last.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > IVAN_REENTRY_DEDUP_TTL_SEC:
        return False
    prev = last.get("entry")
    if entry is None or prev is None:
        return True
    return abs(float(entry) - float(prev)) <= IVAN_REENTRY_DEDUP_MAX_GAP


# Ampiezza massima della forchetta SL→TP più lontano di un setup IVAN leggibile.
IVAN_TYPO_MAX_SPAN = 300.0

# Finestra entro cui "chiudiamo questa" dopo un rientro si riferisce al rientro.
IVAN_CLOSE_THIS_REENTRY_SEC = 30 * 60.0


def _ivan_close_targets_recent_reentry(upper: str, last: dict | None) -> bool:
    """"Chiudiamo questa" subito dopo un rientro: va chiuso solo il rientro.

    Il dimostrativo singolare indica l'ultima posizione aperta; senza un rientro
    recente (o con "tutto") resta una chiusura totale.
    """
    if not isinstance(last, dict) or not last.get("allow_stack"):
        return False
    ts = last.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > IVAN_CLOSE_THIS_REENTRY_SEC:
        return False
    text = fold_accents(upper)
    if re.search(r"\bTUTT[OIE]\b|\bALL\b|\bEVERYTHING\b|\bENTRAMB[EI]\b", text):
        return False
    return re.search(
        r"\b(?:QUEST[AO]|QUEST'ULTIM[AO]|L'ULTIM[AO]|THIS\s+ONE|THIS)\b", text
    ) is not None


def _ivan_repair_range_typo(a: float, b: float, sl: float,
                            tps: list[float]) -> tuple[float, float] | None:
    """Zona con una cifra sbagliata ("4401-3399" per 4401-4399).

    Se un estremo è coerente con la forchetta SL→TP e l'altro no, l'estremo
    fuori viene riallineato copiando le cifre iniziali di quello buono; vale
    solo se il risultato rientra nella forchetta. Altrimenti ``None``.
    """
    if any(tp is None or tp <= 0 for tp in tps):
        return None
    lo = min([sl] + list(tps))
    hi = max([sl] + list(tps))
    if hi - lo > IVAN_TYPO_MAX_SPAN:
        return None

    def inside(v: float) -> bool:
        return lo <= v <= hi

    if inside(a) == inside(b):
        return None
    good, bad = (a, b) if inside(a) else (b, a)
    gs, bs = f"{int(good)}", f"{int(bad)}"
    if len(gs) != len(bs) or len(gs) < 3:
        return None
    # Copia le cifre iniziali dal valore buono fin dove serve a rientrare.
    for n in range(1, len(gs) - 1):
        fixed = float(gs[:n] + bs[n:]) + (bad - int(bad))
        if inside(fixed) and abs(fixed - good) <= IVAN_TYPO_MAX_SPAN:
            return min(good, fixed), max(good, fixed)
    return None


def _ivan_entry_is_typo(direction: str, entry: float | None,
                        sl: float | None, tps: list[float]) -> bool:
    """Entry sbagliata di battitura ma SL e TP coerenti fra loro.

    "XAUUSD SELL 4326 / TP 4420 4417 4412 4408 / SL @ 4436": l'entry non regge
    (i TP sono sopra), mentre SL e TP delimitano una forchetta operativa
    plausibile intorno a 4427. In quel caso l'entry va ignorata, non il segnale.
    """
    if entry is None or sl is None or not tps:
        return False
    if direction not in ("BUY", "SELL"):
        return False
    if any(tp is None or tp <= 0 for tp in tps):
        return False
    # Entry coerente: nessun typo da correggere.
    if direction == "BUY" and sl < entry and all(tp > entry for tp in tps):
        return False
    if direction == "SELL" and sl > entry and all(tp < entry for tp in tps):
        return False
    # SL e TP devono stare su lati opposti, nel verso della direzione.
    if direction == "BUY" and not all(tp > sl for tp in tps):
        return False
    if direction == "SELL" and not all(tp < sl for tp in tps):
        return False
    lo = min([sl] + list(tps))
    hi = max([sl] + list(tps))
    if hi - lo > IVAN_TYPO_MAX_SPAN:
        return False
    # Il typo è tale se l'entry cade fuori dalla forchetta SL→TP.
    return not (lo <= entry <= hi)


def _ivan_update_tp_signal(tp_move: tuple[float, int | None], ch: dict, raw: str,
                           state: BridgeState) -> dict | None:
    """``UPDATE_TP`` da "Spostiamo TP 4 a 4376", ``None`` se il prezzo non regge.

    Il TP nominato vale solo per la posizione di quello split (``tp_index``);
    senza indice il livello vale per tutte le posizioni del segnale. Un prezzo
    dal lato sbagliato rispetto all'entry chiuderebbe la posizione a mercato,
    quindi non viene emesso.
    """
    new_tp, tp_index = tp_move
    last = state.ivan_last_trade or {}
    direction = last.get("direction")
    # Un setup pubblicato come zona ("BUY: 4591-4587") non ha entry singolo:
    # il riferimento è il centro della zona.
    entry = _ivan_entry_ref(last)
    new_tp = _expand_short_price(new_tp, entry)
    if new_tp < 100:
        return None
    if direction in ("BUY", "SELL") and entry is not None:
        profitable = new_tp > entry if direction == "BUY" else new_tp < entry
        if not profitable:
            log.warning(
                f"[IVAN] TP {new_tp} dal lato sbagliato per {direction} "
                f"entry {entry}, ignorato: {raw[:60]}"
            )
            return None
    tps = list(last.get("tp_levels") or [])
    if tp_index is not None and 1 <= tp_index <= len(tps):
        tps[tp_index - 1] = new_tp
    elif tp_index is None:
        tps = [new_tp]
    if last:
        state.set_ivan_last_trade({**last, "tp_levels": tps})
    log.info(
        f"[IVAN] UPDATE_TP XAUUSD {direction} TP={new_tp} "
        f"tp_index={tp_index if tp_index is not None else 'all'}"
    )
    signal = {
        "action":      "UPDATE_TP",
        "direction":   direction,
        "symbol":      "XAUUSD",
        "new_tp":      new_tp,
        "magic_base":  ch["magic_base"],
        "raw_message": raw,
    }
    if tp_index is not None:
        signal["tp_index"] = tp_index
    return signal


def _entry_interval(trade: dict) -> tuple[float, float] | None:
    er = trade.get("entry_range")
    if er and len(er) >= 2:
        return min(er[0], er[1]), max(er[0], er[1])
    entry = trade.get("entry")
    if entry is not None:
        return float(entry), float(entry)
    return None


def _same_trade_setup(a: dict, b: dict, *, max_gap: float = 5.0) -> bool:
    """Same direction and compatible entry (exact, overlapping range, or near).

    Treats refinements like entry 4060 → range 4060-4062 as the same setup so
    Telegram EDITs do not emit a second OPEN.
    """
    if a.get("direction") != b.get("direction"):
        return False
    ia = _entry_interval(a)
    ib = _entry_interval(b)
    if ia is None or ib is None:
        return False
    a_lo, a_hi = ia
    b_lo, b_hi = ib
    # Overlap (inclusive)
    if a_lo <= b_hi and b_lo <= a_hi:
        return True
    # Near but non-overlapping (range expansion/contraction within max_gap)
    gap = max(b_lo - a_hi, a_lo - b_hi)
    return gap <= max_gap


def _oro_resolve_context(state: BridgeState) -> tuple[str | None, float | None, list[float] | None]:
    if state.oro_pending_dir:
        return state.oro_pending_dir, state.oro_pending_entry, state.oro_pending_range
    if state.oro_last_trade:
        lt = state.oro_last_trade
        return lt.get("direction"), lt.get("entry"), lt.get("entry_range")
    return None, None, None


def _oro_try_emit_pending_open(state: BridgeState, ch: dict, raw: str) -> dict | None:
    """Emit OPEN when fragmented ORO messages collected direction + SL + TP."""
    if not state.oro_pending_dir:
        return None
    if state.oro_pending_sl is None or not state.oro_pending_tps:
        return None
    direction = state.oro_pending_dir
    entry = state.oro_pending_entry
    entry_range = state.oro_pending_range
    sl = state.oro_pending_sl
    tps = list(state.oro_pending_tps)
    entry_log = f"range={entry_range}" if entry_range else f"@{entry}"
    log.info(f"[ORO] OPEN (fragmented) {direction} XAUUSD {entry_log} TP={tps} SL={sl}")
    signal = {
        "action":      "OPEN",
        "direction":   direction,
        "symbol":      "XAUUSD",
        "entry":       entry,
        "entry_range": entry_range,
        "tp_levels":   tps,
        "sl":          sl,
        "magic_base":  ch["magic_base"],
        "raw_message": raw,
    }
    state.set_oro_last_trade(signal)
    state.clear_oro_pending()
    return signal


def _oro_parse_range_only(upper: str) -> list[float] | None:
    m = re.match(r"^([\d.,]+)\s*-\s*([\d.,]+)$", upper.strip())
    if not m:
        return None
    v1 = pf(m.group(1))
    v2 = pf(m.group(2))
    return [min(v1, v2), max(v1, v2)]


def parser_sala_oro(text: str, ch: dict, state: BridgeState | None = None) -> dict | None:
    if state is None:
        state, _ = _ensure_runtime()
    raw = text.strip()
    if not raw or raw in (".", "…", "-", "—"):
        return None

    upper = strip_md(raw).upper()

    sl, tps = _parse_oro_sl_tp(upper)
    direction, entry, entry_range = _parse_oro_direction_entry(upper)
    want_stack = _is_reentry_intent(upper) and not _is_deferred_reentry(upper)

    # Ignore solo se il messaggio non porta direzione, livelli né intento di
    # chiusura: le parole di contorno ("ragazzi", "live", …) convivono spesso
    # con il segnale ("Chiudiamo tutto ragazzi" era ignorato).
    close_intent, _ = match_close_all_intent(upper)
    pat = matched_ignore_pattern("sala_oro", upper)
    if pat and direction is None and sl is None and not tps and not close_intent:
        log.debug(f"[ORO] Ignorato ({pat}): {raw[:60]}")
        return None

    if _oro_is_close_half_be(upper):
        log.info(f"[ORO] CLOSE_HALF_BE: {raw[:60]}")
        return {
            "action":      "CLOSE_HALF_BE",
            "symbol":      "XAUUSD",
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    # Chiusura totale ("chiudiamo tutto", "usciamo qui a 4060"): il canale ORO
    # non aveva alcun recognizer di uscita, i messaggi finivano UNPARSED.
    if direction is None:
        close_sig = _maybe_close_from_text(upper, ch, raw, state)
        if close_sig:
            # Una chiusura selettiva lascia aperte le entry migliori: il setup
            # resta valido e non va dimenticato.
            if close_sig["action"] == "CLOSE_ALL_SYMBOL":
                state.clear_oro_pending()
                state.oro_last_trade = None
                state.save()
            return close_sig

    range_only = _oro_parse_range_only(upper)
    if range_only and state.oro_pending_dir:
        state.oro_pending_entry = None
        state.oro_pending_range = range_only
        state.save()
        log.info(f"[ORO] Pending range {range_only} for {state.oro_pending_dir}")
        return None

    if direction is None and (sl is not None or tps):
        direction, entry, entry_range = _oro_resolve_context(state)
        if not direction:
            log.debug(f"[ORO] SL/TP senza contesto: {raw[:60]}")
            return None

        if state.oro_pending_dir:
            state.oro_pending_add_levels(sl, tps if tps else None)
            log.info(
                f"[ORO] Fragment accumulate {direction} SL={state.oro_pending_sl} "
                f"TP={state.oro_pending_tps}"
            )
            return _oro_try_emit_pending_open(state, ch, raw)

        if tps and sl is None:
            log.info(f"[ORO] UPDATE_TP XAUUSD {direction} TP={tps[0]}")
            state.set_oro_last_trade({
                "direction": direction,
                "entry": entry,
                "entry_range": entry_range,
                "sl": state.oro_last_trade.get("sl") if state.oro_last_trade else None,
                "tp_levels": tps,
            })
            return {
                "action":      "UPDATE_TP",
                "symbol":      "XAUUSD",
                "direction":   direction,
                "new_tp":      tps[0],
                "tp_levels":   tps,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }
        if sl is not None and not tps:
            log.info(f"[ORO] UPDATE_SL XAUUSD {direction} SL={sl}")
            state.set_oro_last_trade({
                "direction": direction,
                "entry": entry,
                "entry_range": entry_range,
                "sl": sl,
                "tp_levels": state.oro_last_trade.get("tp_levels") if state.oro_last_trade else [],
            })
            return {
                "action":      "UPDATE_SL",
                "symbol":      "XAUUSD",
                "direction":   direction,
                "new_sl":      sl,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }
        # Combined SL+TP without pending: never inherit stale oro_last_trade
        # entry/range (07:51 bug — "Buy now 4042" missed → OPEN on [4061,4063]).
        if sl is not None and tps:
            log.info(
                f"[ORO] SL+TP senza pending entry (ignorato, no reuse last_trade): "
                f"{raw[:60]}"
            )
            return None

    if direction is None:
        return None

    if not tps and sl is None:
        # New entry far from last setup → drop stale last_trade so later
        # fragments cannot UPDATE_OPEN against the overnight range.
        last = state.oro_last_trade
        if last and not _same_trade_setup(
            {"direction": direction, "entry": entry, "entry_range": entry_range},
            last,
        ):
            log.info(
                f"[ORO] New entry resets last_trade "
                f"(was {last.get('direction')} "
                f"entry={last.get('entry')} range={last.get('entry_range')})"
            )
            state.oro_last_trade = None
        state.set_oro_pending(direction, entry, entry_range)
        log.info(f"[ORO] Pending {direction} XAUUSD entry={entry} range={entry_range}")
        return None

    signal = {
        "action":      "OPEN",
        "direction":   direction,
        "symbol":      "XAUUSD",
        "entry":       entry,
        "entry_range": entry_range,
        "tp_levels":   tps,
        "sl":          sl,
        "magic_base":  ch["magic_base"],
        "raw_message": raw,
    }

    last = state.oro_last_trade
    if last and _same_trade_setup(signal, last) and not want_stack:
        last_tps = last.get("tp_levels") or []
        last_sl = last.get("sl")
        tps_changed = tps != last_tps
        sl_changed = sl != last_sl
        entry_changed = _entry_interval(signal) != _entry_interval(last)
        if not tps_changed and not sl_changed and not entry_changed:
            log.info(f"[ORO] Duplicate OPEN ignored (same levels) {direction}")
            return None
        if tps_changed and not sl_changed and not entry_changed:
            log.info(f"[ORO] UPDATE_TP (edit) XAUUSD {direction} TP={tps}")
            signal = {
                "action":      "UPDATE_TP",
                "symbol":      "XAUUSD",
                "direction":   direction,
                "new_tp":      tps[0],
                "tp_levels":   tps,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }
        elif sl_changed and not tps_changed and not entry_changed:
            log.info(f"[ORO] UPDATE_SL (edit) XAUUSD {direction} SL={sl}")
            signal = {
                "action":      "UPDATE_SL",
                "symbol":      "XAUUSD",
                "direction":   direction,
                "new_sl":      sl,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }
        else:
            # Range refinement and/or combined SL+TP change → UPDATE_OPEN
            log.info(
                f"[ORO] UPDATE_OPEN (edit) XAUUSD {direction} "
                f"entry={entry} range={entry_range} TP={tps} SL={sl}"
            )
            signal["action"] = "UPDATE_OPEN"

    if signal["action"] == "OPEN":
        entry_log = f"range={entry_range}" if entry_range else f"@{entry}"
        if want_stack:
            signal["allow_stack"] = True
            log.info(
                f"[ORO] OPEN (re-entry/stack) {direction} XAUUSD {entry_log} "
                f"TP={tps} SL={sl}"
            )
        else:
            log.info(f"[ORO] OPEN {direction} XAUUSD {entry_log} TP={tps} SL={sl}")

    if signal["action"] in ("OPEN", "UPDATE_OPEN"):
        state.set_oro_last_trade(signal)
    elif signal["action"] == "UPDATE_TP":
        state.set_oro_last_trade({
            "direction": direction,
            "entry": entry,
            "entry_range": entry_range,
            "sl": last.get("sl") if last else sl,
            "tp_levels": tps,
        })
    elif signal["action"] == "UPDATE_SL":
        state.set_oro_last_trade({
            "direction": direction,
            "entry": entry,
            "entry_range": entry_range,
            "sl": sl,
            "tp_levels": last.get("tp_levels") if last else tps,
        })
    state.clear_oro_pending()
    return signal


# ─────────────────────────────────────────────────────────────────────────────
# PARSER CH4 — SALA STARK
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato markdown:
#   Apro una nuova operazione
#   XAUUSD BUY
#   Entry: 4725
#   SL: 4712.37
#   TP1: 4726.87 / TP2: 4730.77 / TP3: 4798.71 (opzionale)
#
# "Aggiungo un'altra operazione" -> is_add_signal=True, lotto dimezzato
#   - con SL/TP propri -> trade indipendente
#   - senza SL/TP -> inherit_from_first=True, EA copia valori del primo trade aperto
#
# Formato forex piatto:
#   BUY   GBPUSD 1.35864
#   SL    1.31230
#   TP    1.36050
#
# BE: automatico nell'EA su TP1 hit. "Sposto SL a Break Even" ignorato

def parser_sala_stark(text: str, ch: dict, state: BridgeState | None = None) -> dict | None:
    if state is None:
        state, _ = _ensure_runtime()
    raw   = text.strip()
    upper = strip_md(raw).upper()

    # ── Chiusura manuale dell'operazione ─────────────────────────────────
    # "Chiusa a Break Even" / "Chiusa in take profit": il canale ha chiuso tutto,
    # va replicato sulle nostre posizioni residue (prima degli ignore, che
    # matchano BREAK EVEN / TAKE PROFIT).
    if re.search(r"\bCHIUS[AO]\b\s+(?:A|IN)\b", upper):
        m_sym = re.search(r"\b(XAUUSD|GOLD|[A-Z]{3}(?:USD|JPY|EUR|GBP|CHF|CAD|AUD|NZD))\b", upper)
        symbol = (
            normalize_symbol(m_sym.group(1)) if m_sym
            else (state.stark_last_trade or {}).get("symbol")
        )
        if not symbol:
            log.debug(f"[CH4] Chiusura senza simbolo né trade noto: {raw[:60]}")
            return None
        state.clear_stark_last_trade()
        log.info(f"[CH4] CLOSE_ALL_SYMBOL {symbol}: {raw[:60]}")
        return {
            "action":      "CLOSE_ALL_SYMBOL",
            "symbol":      symbol,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    pat = matched_ignore_pattern("sala_stark", upper)
    if pat:
        log.debug(f"[CH4] Ignorato ({pat}): {raw[:60]}")
        return None

    is_add = bool(re.search(r"AGGIUNGO\s+UN", upper))

    # ── Formato markdown: "Apro" / "Aggiungo" ────────────────────────────────
    if re.search(r"(APRO|AGGIUNGO)\s+UN", upper):
        m_sd = re.search(
            r"(XAUUSD|GBPUSD|EURUSD|USDJPY|GBPJPY|AUDUSD|\w{6,7})\s+(BUY|SELL)",
            upper
        )
        if not m_sd:
            return None
        symbol    = normalize_symbol(m_sd.group(1))
        direction = m_sd.group(2)

        m_entry = re.search(r"ENTRY\s*[:\s]\s*([\d.,]+)", upper)
        entry   = pf(m_entry.group(1)) if m_entry else None

        m_sl = re.search(r"\bSL\b\s*[:\s]\s*([\d.,]+)", upper)
        sl   = pf(m_sl.group(1)) if m_sl else None

        tps = [pf(m.group(2))
               for m in re.finditer(r"TP\s*(\d)\s*[:\s]\s*([\d.,]+)", upper)]

        inherit = is_add and sl is None and not tps

        log.info(f"[CH4] {'ADD' if is_add else 'OPEN'} {direction} {symbol} @ {entry} "
                 f"SL={sl} TP={tps} inherit={inherit}")
        state.set_stark_last_trade({"symbol": symbol, "direction": direction})
        return {
            "action":             "OPEN",
            "direction":          direction,
            "symbol":             symbol,
            "entry":              entry,
            "tp_levels":          tps,
            "sl":                 sl,
            "is_add_signal":      is_add,
            "inherit_from_first": inherit,
            "magic_base":         ch["magic_base"],
            "raw_message":        raw,
        }

    # ── Formato piatto forex ──────────────────────────────────────────────────
    m_flat = re.search(r"(BUY|SELL)\s+([\w]{6,7})\s+([\d.,]+)", upper)
    if m_flat:
        direction = m_flat.group(1)
        symbol    = normalize_symbol(m_flat.group(2))
        entry     = pf(m_flat.group(3))

        m_sl = re.search(r"\bSL\b\s+([\d.,]+)", upper)
        sl   = pf(m_sl.group(1)) if m_sl else None

        tps = [pf(m.group(1)) for m in re.finditer(r"\bTP\d*\s+([\d.,]+)", upper)]

        log.info(f"[CH4] OPEN FLAT {direction} {symbol} @ {entry} SL={sl} TP={tps}")
        state.set_stark_last_trade({"symbol": symbol, "direction": direction})
        return {
            "action":             "OPEN",
            "direction":          direction,
            "symbol":             symbol,
            "entry":              entry,
            "tp_levels":          tps,
            "sl":                 sl,
            "is_add_signal":      False,
            "inherit_from_first": False,
            "magic_base":         ch["magic_base"],
            "raw_message":        raw,
        }

    return None


# ─────────────────────────────────────────────────────────────────────────────
# PARSER IVANTRADES VIP
# ─────────────────────────────────────────────────────────────────────────────
#
# Formato tipico:
#   XAUUSD SELL 4011
#   TP 1 4006
#   TP 2 4004
#   ...
#   SL @ 4022
#
# Gestione:
#   Spostiamo SL a BE  -> CHECK_AND_BE
#   CHIUDERE ORA       -> CLOSE_ALL_SYMBOL (XAUUSD)
#   Meta size / METÀ SIZE / MEZZA SIZE -> lot_factor 0.5

def parser_ivan_vip(text: str, ch: dict, state: BridgeState | None = None) -> dict | None:
    if state is None:
        state, _ = _ensure_runtime()
    raw = text.strip()
    if not raw:
        return None

    upper = strip_md(raw).upper()

    # Gli ignore DEVONO precedere il recognizer di chiusura (frasi preparatorie),
    # ma non devono coprire un setup completo: "TAKE PROFIT"/"TP n" compaiono sia
    # nei commenti sia nei segnali veri.
    has_setup = bool(
        re.search(_IVAN_OPEN_RE, upper)
        and re.search(r"\bSL\b", upper)
    )
    # "Spostiamo il take profit 4 a 4376" è un ordine, non il commento che
    # l'ignore "TAKE PROFIT" intercetta: la forma con verbo esplicito passa.
    has_move_tp_cmd = match_move_tp_price(upper, require_verb=True) is not None
    pat = matched_ignore_pattern("ivan_vip", upper)
    if pat and not has_setup and not has_move_tp_cmd:
        log.debug(f"[IVAN] Ignorato ({pat}): {raw[:60]}")
        return None

    # Un ordine di chiusura che nomina il rientro ("chiudo la rientry") non deve
    # passare dal ramo di apertura: il verbo di chiusura ha la precedenza.
    close_first = (
        match_selective_close_intent(upper) is not None
        or match_close_all_intent(upper)[0]
    )

    # "Rientrate ora" / "rientriamo": riapre l'ultimo setup del canale (solo GOLD)
    # nella stessa direzione, a mercato. Senza setup noto non si indovina nulla.
    if not has_setup and not close_first and _is_reentry_intent(upper):
        if _is_deferred_reentry(upper):
            log.info(f"[IVAN] Rientro annunciato ma non operativo, ignorato: {raw[:60]}")
            return None
        last = state.ivan_last_trade
        if not last or not last.get("direction"):
            log.warning(f"[IVAN] Rientro senza setup precedente: {raw[:60]}")
            return None
        direction = last["direction"]
        # "Rientrare ora a 4023": il prezzo nel messaggio è il nuovo entry.
        # "Rientri piccola da qui a 58": il canale abbrevia le ultime due cifre.
        m_px = re.search(r"\b(\d{2,}(?:[.,]\d+)?)\b", upper)
        raw_px = pf(m_px.group(1)) if m_px else None
        last_ref = _ivan_entry_ref(last)
        if raw_px is not None:
            raw_px = _expand_short_price(raw_px, last_ref)
        entry = raw_px or last_ref
        tps = list(last.get("tp_levels") or [])
        sl = last.get("sl")
        if entry is not None:
            # I livelli già superati dal nuovo entry non sono più coerenti.
            tps = [
                tp for tp in tps
                if (tp > entry if direction == "BUY" else tp < entry)
            ]
            if sl is not None and (
                sl >= entry if direction == "BUY" else sl <= entry
            ):
                sl = None
        # MSG + EDIT dello stesso rientro ("… a 58" poi "… a 58.5") non deve
        # aprire due volte: lo stack è consentito solo a distanza di tempo/prezzo.
        if _ivan_reentry_is_repeat(last, entry):
            log.info(f"[IVAN] Rientro già emesso di recente, ignorato: {raw[:60]}")
            return None

        # Il rientro è per definizione una posizione in più su un setup già
        # aperto: senza allow_stack l'EA lo tratterebbe come modifica SL/TP.
        lot_factor = _reduced_size_factor(upper) or last.get("lot_factor")
        log.info(
            f"[IVAN] OPEN (rientro) {direction} {last.get('symbol')} "
            f"@ {entry} TP={tps} SL={sl} lot_factor={lot_factor}"
        )
        signal = {
            "action":      "OPEN",
            "direction":   direction,
            "symbol":      last.get("symbol") or "XAUUSD",
            "entry":       entry,
            "tp_levels":   tps,
            "sl":          sl,
            "allow_stack": True,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }
        if lot_factor is not None:
            signal["lot_factor"] = lot_factor
        state.set_ivan_last_trade(signal)
        return signal

    if contains_any(upper, "SPOSTO SL A BE", "SPOSTIAMO SL A BE", "SL A BE"):
        log.info(f"[IVAN] CHECK_AND_BE: {raw[:60]}")
        return {
            "action":      "CHECK_AND_BE",
            "symbol":      "XAUUSD",
            "tp_index":    1,
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    # "Spostiamo lo stop a 4255": lo stop va aggiornato sulle posizioni aperte.
    # Senza questo ramo il messaggio era UNPARSED e le posizioni restavano sullo
    # SL originale.
    if not has_setup:
        new_sl = match_move_sl_price(upper)
        if new_sl is not None:
            last = state.ivan_last_trade
            direction = (last or {}).get("direction")
            if last:
                state.set_ivan_last_trade({**last, "sl": new_sl})
            log.info(f"[IVAN] UPDATE_SL XAUUSD {direction} SL={new_sl}")
            return {
                "action":      "UPDATE_SL",
                "direction":   direction,
                "symbol":      "XAUUSD",
                "new_sl":      new_sl,
                "magic_base":  ch["magic_base"],
                "raw_message": raw,
            }

    # "Spostiamo TP 4 a 4376": il canale allunga o accorcia un singolo target.
    # Senza questo ramo il messaggio era UNPARSED (17/08) e la posizione restava
    # sul TP pubblicato nel setup.
    if not has_setup:
        tp_move = match_move_tp_price(upper)
        if tp_move is not None:
            signal = _ivan_update_tp_signal(tp_move, ch, raw, state)
            if signal is not None:
                return signal

    # "Chiduamo questa" due minuti dopo "Rientriamo con piccola size": il
    # canale chiude il rientro, non il setup base (09/09 aveva chiuso tutto e
    # il setup ha poi preso TP3).
    if (
        not has_setup
        and match_close_all_intent(upper)[0]
        and match_selective_close_intent(upper) is None
        and _ivan_close_targets_recent_reentry(upper, state.ivan_last_trade)
    ):
        state.pop_close_price_pending(ch["id"])
        log.info(f"[IVAN] CLOSE_SELECTIVE keep=ALL_BUT_NEWEST (rientro): {raw[:60]}")
        return {
            "action":      "CLOSE_SELECTIVE",
            "keep":        "ALL_BUT_NEWEST",
            "symbol":      "XAUUSD",
            "magic_base":  ch["magic_base"],
            "raw_message": raw,
        }

    close_sig = _maybe_close_from_text(upper, ch, raw, state)
    if close_sig:
        return close_sig

    m_open = re.search(_IVAN_OPEN_RE, upper)
    if not m_open:
        return None

    symbol = normalize_symbol(m_open.group(1))
    direction = m_open.group(2)
    entry = pf(m_open.group(3))
    if entry is None:
        return None

    # "XAUUSD BUY: 4591-4587": il canale pubblica una zona d'ingresso invece del
    # prezzo singolo. Stessa convenzione degli altri canali oro: entry_range.
    entry_range = None
    entry2 = pf(m_open.group(4)) if m_open.group(4) else None
    if entry2 is not None:
        entry_range = [min(entry, entry2), max(entry, entry2)]
        entry = None

    tps: list[float] = []
    sl = None
    # I setup arrivano sia multiriga ("TP 1 4006") sia su una riga sola con
    # separatori ("| TP1: 4596 | ... | SL: 4570"): i segmenti valgono uguale.
    for line in re.split(r"[\n|]", raw):
        lu = strip_md(line).upper().strip()
        m_tp = re.match(r"TP\s*\d*\s*[:.@]?\s*([\d.,]+)", lu)
        if m_tp:
            v = pf(m_tp.group(1))
            if v is not None:
                tps.append(v)
            continue
        m_sl = re.match(r"SL\s*[:@]?\s*([\d.,]+)", lu)
        if m_sl:
            sl = pf(m_sl.group(1))

    if not tps or sl is None:
        log.debug(f"[IVAN] Segnale incompleto: {raw[:60]}")
        return None

    # Entry con typo di battitura ("XAUUSD SELL 4326" invece di 4427): se SL e
    # TP sono coerenti fra loro il setup è leggibile, si apre a mercato invece
    # di scartare il segnale. La distanza dal prezzo la valuta poi l'EA.
    if entry is not None and _ivan_entry_is_typo(direction, entry, sl, tps):
        log.warning(
            f"[IVAN] Entry {entry} incoerente con SL {sl} e TP {tps}: "
            f"apertura a mercato (ENTRY_TYPO_MARKET)"
        )
        entry = None

    # Zona con typo ("BUY 4401-3399" per 4401-4399): senza correzione la zona
    # era scartata come non plausibile e il setup non apriva (09/09).
    if entry_range is not None and entry_range[1] - entry_range[0] > IVAN_TYPO_MAX_SPAN:
        repaired = _ivan_repair_range_typo(entry_range[0], entry_range[1], sl, tps)
        if repaired is not None:
            log.warning(
                f"[IVAN] Zona {entry_range} con typo, corretta in "
                f"{list(repaired)} (RANGE_TYPO_FIXED)"
            )
            entry_range = list(repaired)

    # Accent-insensitive: METÀ SIZE, Meta size, MEZZA SIZE, typo META SAZIE
    folded = fold_accents(upper)
    lot_factor = 0.5 if re.search(
        r"(?:META|MEZZA|HALF)\s*(?:SIZE|SAZIE)",
        folded,
    ) else 1.0
    log.info(
        f"[IVAN] OPEN {direction} {symbol} "
        f"{'range=' + str(entry_range) if entry_range else '@ ' + str(entry)} "
        f"TP={tps} SL={sl} lot_factor={lot_factor}"
    )
    signal = {
        "action":      "OPEN",
        "direction":   direction,
        "symbol":      symbol,
        "entry":       entry,
        "entry_range": entry_range,
        "tp_levels":   tps,
        "sl":          sl,
        "magic_base":  ch["magic_base"],
        "raw_message": raw,
    }
    if lot_factor != 1.0:
        signal["lot_factor"] = lot_factor
    state.set_ivan_last_trade(signal)
    return signal


# ─────────────────────────────────────────────────────────────────────────────
# MAPPA PARSER
# ─────────────────────────────────────────────────────────────────────────────

PARSERS = {
    "zanni_vip":  parser_zanni_vip,
    "sala_gold":  parser_sala_gold,
    "sala_vip":   parser_sala_vip,
    "sala_oro":   parser_sala_oro,
    "sala_stark": parser_sala_stark,
    "ivan_vip":   parser_ivan_vip,
    "placeholder": lambda _t, _c: None,
}

def get_parser(name: str):
    p = PARSERS.get(name)
    if not p:
        log.warning(f"Parser '{name}' non trovato.")
    return p

# ─────────────────────────────────────────────────────────────────────────────
# CHANNEL MAP
# ─────────────────────────────────────────────────────────────────────────────

def build_channel_map() -> dict:
    return {
        int(ch["telegram_id"]): ch
        for ch in CONFIG["channels"]
        if ch.get("enabled", True)
    }

# ─────────────────────────────────────────────────────────────────────────────
# MAIN BRIDGE
# ─────────────────────────────────────────────────────────────────────────────

async def run_bridge():
    bridge_state, processed_messages = _ensure_runtime()
    tg_cfg = CONFIG["telegram"]
    log.info("=" * 60)
    log.info(f"TG TradinGo Bridge v{BRIDGE_VERSION}")
    log.info(f"Config:  {CONFIG_FILE}")
    log.info(f"Session: {tg_cfg['session_file']}")

    channel_map = build_channel_map()
    active_ids  = list(channel_map.keys())

    if not active_ids:
        log.error("Nessun canale abilitato. Uscita.")
        return

    for cid, cfg in channel_map.items():
        log.info(f"  {cfg['id']} [{cid}] {cfg['name']} (parser: {cfg['parser']})")

    # Inizializza file segnale vuoti (never crash the bridge if a UNC share is down)
    for mt5_path in get_mt5_paths():
        for cfg in channel_map.values():
            f = Path(mt5_path) / cfg["signal_file"]
            try:
                if f.exists():
                    continue
                # Do not mkdir UNC roots (\\host\share) — share must already exist.
                if not is_unc_path(f):
                    f.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text_timed(f, json.dumps({"action": "NONE"}, indent=2))
                log.info(f"Init: {f}")
            except Exception as exc:
                log.error(f"Init skipped {f}: {exc}")

    client = TelegramClient(tg_cfg["session_file"], tg_cfg["api_id"], tg_cfg["api_hash"])

    async def process_message(event, is_edit: bool = False):
        """Handler condiviso per messaggi nuovi e modificati."""
        try:
            chat_id = event.chat_id
            text    = (event.raw_text or "").strip()
            if not text:
                return
            ch_cfg = channel_map.get(int(chat_id))
            if not ch_cfg:
                return
            parser = get_parser(ch_cfg["parser"])
            if not parser:
                return

            event_type = "EDIT" if is_edit else "NEW"
            message_id = event.id
            dedup_key = ProcessedMessageStore.make_key(
                int(chat_id), int(message_id), event_type, text
            )
            if processed_messages.is_duplicate(dedup_key):
                log.debug(
                    f"[{ch_cfg['id']}] Duplicato ignorato {event_type} id={message_id}"
                )
                append_bridge_event(CONFIG, {
                    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "channel_id": ch_cfg["id"],
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "event_type": event_type,
                    "raw_text": text,
                    "outcome": "DUPLICATE",
                })
                return

            prefix = "EDIT" if is_edit else "MSG"
            log.info(f"[{ch_cfg['id']}] {prefix}: {text[:80].replace(chr(10), ' | ')}")

            # Per i messaggi modificati di CH2: se il testo contiene ora
            # sia la direzione che i dati completi (SL/TP), va trattato come
            # UPDATE_OPEN — forziamo il pending state se non era già impostato
            if is_edit and ch_cfg["parser"] == "sala_gold":
                upper = text.upper()
                has_numbers = bool(re.search(r"\d{3,}", upper))
                has_direction = bool(re.search(
                    r"(BUY|SELL)\s+(?:GOLD|XAUUSD)|(?:GOLD|XAUUSD)\s+(BUY|SELL)", upper
                ))
                if has_numbers and has_direction and not bridge_state.ch2_pending_open:
                    m = re.search(
                        r"(BUY|SELL)\s+(?:GOLD|XAUUSD)|(?:GOLD|XAUUSD)\s+(BUY|SELL)", upper
                    )
                    if m:
                        direction = m.group(1) or m.group(2)
                        bridge_state.set_ch2_pending(direction)
                        log.info(f"[CH2] EDIT con dati completi → forzo UPDATE_OPEN {direction}")

            matched_ignore = matched_ignore_pattern(
                ch_cfg["parser"], strip_md(text).upper()
            )

            try:
                if ch_cfg["parser"] in (
                    "sala_gold", "sala_oro", "sala_vip", "sala_stark", "ivan_vip"
                ):
                    signal = parser(text, ch_cfg, bridge_state)
                else:
                    signal = parser(text, ch_cfg)
            except Exception as parse_exc:
                append_bridge_event(CONFIG, {
                    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "channel_id": ch_cfg["id"],
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "event_type": event_type,
                    "raw_text": text,
                    "outcome": "PARSE_ERROR",
                    "error": f"{type(parse_exc).__name__}: {parse_exc}",
                })
                raise

            signal = coerce_edit_open_to_update(signal, is_edit)

            if signal:
                msg_date = getattr(event, "date", None)
                telegram_date = (
                    msg_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    if msg_date
                    else None
                )
                meta = {
                    "message_id": message_id,
                    "chat_id": int(chat_id),
                    "telegram_date": telegram_date,
                    "event_type": event_type,
                }
                if write_signal(ch_cfg, signal, meta):
                    # Un EDIT che non cambia il testo non porta informazione
                    # nuova: prenotiamo la sua chiave così non riemette il
                    # segnale (STARK ri-pubblica ogni setup come EDIT identico
                    # ~60s dopo e l'EA riapriva a mercato dopo SL/TP).
                    edit_alias = (
                        ()
                        if is_edit
                        else (
                            ProcessedMessageStore.make_key(
                                int(chat_id), int(message_id), "EDIT", text
                            ),
                        )
                    )
                    processed_messages.mark_processed(dedup_key, *edit_alias)
                    append_bridge_event(CONFIG, {
                        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "channel_id": ch_cfg["id"],
                        "chat_id": int(chat_id),
                        "message_id": int(message_id),
                        "event_type": event_type,
                        "raw_text": text,
                        "outcome": "EMITTED",
                        "signal_id": signal.get("signal_id"),
                        "action": signal.get("action"),
                        "payload": signal,
                        "targets": meta.get("written_targets") or [],
                    })
            else:
                outcome = "IGNORED_PATTERN" if matched_ignore else "UNPARSED"
                append_bridge_event(CONFIG, {
                    "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "channel_id": ch_cfg["id"],
                    "chat_id": int(chat_id),
                    "message_id": int(message_id),
                    "event_type": event_type,
                    "raw_text": text,
                    "outcome": outcome,
                    "matched_pattern": matched_ignore,
                })
                log.debug(f"[{ch_cfg['id']}] Ignorato")

        except Exception as e:
            log.error(f"Errore: {e}\n{traceback.format_exc()}")

    @client.on(events.NewMessage(chats=active_ids))
    async def on_message(event):
        await process_message(event, is_edit=False)

    @client.on(events.MessageEdited(chats=active_ids))
    async def on_edit(event):
        await process_message(event, is_edit=True)

    @client.on(events.Raw())
    async def on_raw(update):
        """Rileva ban da canali e lo segnala immediatamente."""
        pass  # Gestito dal BanDetector sul logger Telethon

    async def check_banned_channels():
        """Controlla periodicamente se siamo stati bannati da canali sconosciuti."""
        known_ids = set(active_ids)
        try:
            dialogs = await client.get_dialogs()
            for d in dialogs:
                entity = d.entity
                eid = getattr(entity, 'id', None)
                if eid and eid not in known_ids:
                    # Canale sconosciuto — non fa nulla ma lo monitoriamo
                    pass
        except Exception as e:
            log.error(f"[BAN CHECK] Errore: {e}")

    async with client:
        log.info("Connesso. In ascolto (NewMessage + MessageEdited)...")

        # Intercetta messaggi di ban dal logger Telethon
        import logging as _logging
        class BanDetector(_logging.Handler):
            def emit(self, record):
                msg = self.format(record)
                # Filtra solo i veri ban Telethon — formato esatto:
                # "Account is now banned in XXXXXXX"
                if "Account is now banned in" in msg:
                    # Estrai ID canale dal messaggio
                    import re as _re
                    m = _re.search(r"banned in (\d+)", msg)
                    if m:
                        banned_id = int(m.group(1))
                        if banned_id not in active_ids:
                            log.warning(
                                f"[BAN] ⚠️  Account bannato da canale ESTERNO ID={banned_id}. "
                                f"NON è uno dei 4 canali operativi. "
                                f"Esegui dump_channels.py per identificarlo."
                            )
                        else:
                            log.error(
                                f"[BAN] 🚨 Account bannato da canale OPERATIVO ID={banned_id}! "
                                f"Segnale da quel canale non sarà più ricevuto."
                            )

        ban_handler = BanDetector()
        ban_handler.setLevel(_logging.WARNING)
        _logging.getLogger("telethon").addHandler(ban_handler)

        async def _heartbeat_loop():
            while True:
                write_heartbeat(CONFIG, HEARTBEAT_INTERVAL_SEC)
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

        hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            write_heartbeat(CONFIG, HEARTBEAT_INTERVAL_SEC)
            await client.run_until_disconnected()
        finally:
            hb_task.cancel()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT CON AUTO-RESTART
# ─────────────────────────────────────────────────────────────────────────────

def main():
    delay = 10
    while True:
        try:
            log.info("Avvio bridge...")
            asyncio.run(run_bridge())
        except KeyboardInterrupt:
            log.info("Stop manuale.")
            break
        except Exception as e:
            log.error(f"Crash: {e}\n{traceback.format_exc()}")
            log.info(f"Restart in {delay}s...")
            time.sleep(delay)

if __name__ == "__main__":
    main()
