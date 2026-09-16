"""
Core utilities for TG TradinGo bridge: state persistence, deduplication,
payload validation, and atomic JSON writes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("TradinGo")

PLACEHOLDER_API_HASH = "INSERISCI_API_HASH_TELEGRAM"
PRODUCTION_CONFIG = Path(r"C:\TG_TradinGo\tradingo_config.json")


def resolve_config_path(explicit: str | os.PathLike | None = None) -> Path:
    """Return config path with real Telegram credentials when available."""
    script_dir = Path(__file__).resolve().parent
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("TRADINGO_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.extend([
        script_dir / "tradingo_config.json",
        PRODUCTION_CONFIG,
        script_dir / "tradingo_config.example.json",
    ])

    def has_telegram_creds(path: Path) -> bool:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tg = data.get("telegram", {})
            api_id = tg.get("api_id")
            api_hash = (tg.get("api_hash") or "").strip()
            return bool(api_id) and bool(api_hash) and api_hash != PLACEHOLDER_API_HASH
        except Exception:
            return False

    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        if has_telegram_creds(resolved):
            return resolved

    for path in candidates:
        resolved = path.resolve()
        if resolved.exists():
            return resolved
    return candidates[0].resolve()


VALID_ACTIONS = frozenset({
    "NONE",
    "OPEN",
    "OPEN_NOW",
    "UPDATE_OPEN",
    "CHECK_AND_BE",
    "CHECK_AND_CLOSE_TP",
    "CLOSE_ALL_SYMBOL",
    "CLOSE_SELECTIVE",
    "BREAK_EVEN_PRICE",
    "CLOSE_HALF_BE",
    "UPDATE_TP",
    "UPDATE_SL",
    "CHECK_AND_CLOSE",
})

ACTIONS_REQUIRING_DIRECTION = frozenset({
    "OPEN",
    "OPEN_NOW",
    "UPDATE_OPEN",
})

ACTIONS_REQUIRING_SYMBOL = frozenset({
    "OPEN",
    "OPEN_NOW",
    "UPDATE_OPEN",
    "UPDATE_TP",
    "UPDATE_SL",
    "CHECK_AND_CLOSE",
    "CLOSE_ALL_SYMBOL",
    "CLOSE_SELECTIVE",
    "BREAK_EVEN_PRICE",
    "CLOSE_HALF_BE",
})

# CLOSE_SELECTIVE: quali posizioni restano aperte.
#   BEST            -> tiene le entry migliori per la direzione (SELL: prezzo alto, BUY: basso)
#   HIGHEST         -> tiene le entry con prezzo più alto (chiude quelle sotto)
#   LOWEST          -> tiene le entry con prezzo più basso (chiude quelle sopra)
#   ALL_BUT_NEWEST  -> chiude solo l'ultimo blocco aperto (il rientro), tiene il resto
SELECTIVE_KEEP_MODES = frozenset({"BEST", "HIGHEST", "LOWEST", "ALL_BUT_NEWEST"})

def make_signal_id(chat_id: int | str, message_id: int | str, event_type: str) -> str:
    """Short deterministic id for JSON + MT5 comment (fits in 31-char budget)."""
    raw = f"{chat_id}:{message_id}:{event_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


# Massimo numero di parole entro cui un verbo di chiusura da solo
# ("Chiudiamo", "Esco") vale come comando e non come frase narrativa.
CLOSE_STANDALONE_MAX_WORDS = 6

# Consuntivi e riepiloghi: il verbo di chiusura racconta un risultato invece di
# ordinare un'uscita ("E anche oggi chiudiamo in Profitto" aveva chiuso il TP4
# ancora running). Registro additivo: nuove forme si AGGIUNGONO.
_RECAP_OUTCOME = (
    r"(?:PROFITT?\w*|PERDITA|GUADAGNO|BELLEZZA|POSITIVO|NEGATIVO|VERDE|ROSSO|"
    r"GAIN|LOSS|GREEN|PROFIT)"
)
_RECAP_PERIOD = r"(?:SETTIMANA|GIORNATA|GIORNO|MESE|SESSIONE|ANNO|WEEK|DAY|MONTH)"
CLOSE_RECAP_PATTERNS: tuple[str, ...] = (
    # "E anche oggi chiudiamo in Profitto", "Ieri abbiamo chiuso in verde"
    r"\b(?:ANCHE\s+)?(?:OGGI|IERI|STAMANE|STAMATTINA)\b.*\bCHIUD\w*",
    r"\bCHIUD\w*\b.*\b(?:ANCHE\s+)?(?:OGGI|IERI)\b",
    # "Chiudiamo in profitto" senza complemento operativo: è il consuntivo.
    rf"\bCHIUD\w*\s+(?:COSI\s+)?IN\s+{_RECAP_OUTCOME}\b",
    # "Chiudiamo la settimana", "chiudiamo il mese"
    rf"\bCHIUD\w*\s+(?:(?:LA|IL|QUEST[AO])\s+)?{_RECAP_PERIOD}\b",
    r"\bRECAP\b",
    r"\bRIEPILOGO\b",
)


def matched_close_recap_pattern(upper: str) -> str | None:
    """Prima forma di consuntivo trovata nel messaggio, altrimenti ``None``."""
    folded = _fold_accents_upper(upper)
    for pat in CLOSE_RECAP_PATTERNS:
        if re.search(pat, folded):
            return pat
    return None


def _fold_accents_upper(text: str) -> str:
    """PIÙ → PIU, METÀ → META: confronti insensibili agli accenti."""
    normalized = unicodedata.normalize("NFKD", text.upper())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


# Chiusure parziali SELETTIVE: il canale chiede di chiudere solo una parte
# delle entry ("le entry meno premium", "quelle più in basso") e di tenere le
# altre. Il registro è additivo: nuove forme si AGGIUNGONO, mai sostituite.
_SELECTIVE_ENTRY_NOUN = (
    r"(?:ENTR(?:Y|IES|ATA|ATE|I)|POSIZION[EI]|OPERAZION[EI]|ORDIN[EI]|"
    r"TRADE[S]?|QUELL[EAIO]|LE\s+ALTRE)"
)
# Qualificatori direzionali (prezzo) e qualitativi (rispetto alla direzione).
_SELECTIVE_HIGH = (
    r"(?:PIU\s+(?:IN\s+)?ALT[EIO]|PIU\s+SU|IN\s+ALTO|DA\s+SOPRA|DI\s+SOPRA|"
    r"SOPRA|ALTE|SUPERIOR[EI]|HIGHER|ABOVE|TOP)"
)
_SELECTIVE_LOW = (
    r"(?:PIU\s+(?:IN\s+)?BASS[EIO]|PIU\s+GIU|IN\s+BASSO|DA\s+SOTTO|DI\s+SOTTO|"
    r"SOTTO|BASSE|INFERIOR[EI]|LOWER|BELOW|BOTTOM)"
)
_SELECTIVE_WORST = (
    r"(?:MENO\s+PREMIUM|PEGGIOR[EI]|PIU\s+DEBOL[EI]|MENO\s+BUON[EI]|"
    r"IN\s+PERDITA|IN\s+LOSS|WORST|WEAKEST)"
)
_SELECTIVE_BEST = (
    r"(?:PIU\s+PREMIUM|MIGLIOR[EI]|PIU\s+FORT[EI]|BEST|STRONGEST)"
)
# "chiudo la rientry": chiude solo le posizioni del rientro, non il setup base.
_SELECTIVE_REENTRY_NOUN = (
    r"(?:RIENTR(?:O|I|Y|IES|ATA|ATE)|RE[- ]?ENTR(?:Y|IES|IE)|REENTRAT[AE]|"
    r"SECOND[AO]\s+(?:ENTRATA|ENTRY|POSIZIONE)|ULTIM[AO]\s+(?:ENTRATA|ENTRY|RIENTRO))"
)
# "lasciamo/teniamo solo quelle …": il qualificatore descrive ciò che RESTA.
SELECTIVE_KEEP_MAX_WORDS = 10
_SELECTIVE_KEEP_VERB = (
    r"(?:LASCIAMO|LASCIATE|LASCIA|LASCIANDO|TENIAMO|TENETE|TIENI|"
    r"MANTENIAMO|MANTENETE|RESTANO|RESTA|KEEP|HOLD)"
)


def match_selective_close_intent(upper: str) -> str | None:
    """Chiusura parziale selettiva: ritorna quali posizioni RESTANO aperte.

    Valori: ``BEST`` / ``HIGHEST`` / ``LOWEST`` (vedi SELECTIVE_KEEP_MODES),
    ``None`` se il messaggio non è una chiusura selettiva.

    Va valutata PRIMA di :func:`match_close_all_intent`, altrimenti
    "chiudiamo le entry meno premium" verrebbe eseguita come chiusura totale.
    """
    if not upper or not upper.strip():
        return None
    text = _fold_accents_upper(upper)

    has_noun = re.search(_SELECTIVE_ENTRY_NOUN, text) is not None

    # Forma "tieni": il qualificatore descrive le posizioni da MANTENERE.
    keep_form = re.search(
        rf"{_SELECTIVE_KEEP_VERB}\s+(?:SOLO\s+|SOLTANTO\s+|SOLE\s+)?",
        text,
    )
    # La forma "tieni" non ha verbo di chiusura: vale solo in messaggi brevi,
    # così una frase narrativa lunga non chiude posizioni.
    words = re.findall(r"[A-Za-z\u00c0-\u00ff]+", text)
    if keep_form and has_noun and len(words) <= SELECTIVE_KEEP_MAX_WORDS:
        if re.search(_SELECTIVE_HIGH, text):
            return "HIGHEST"
        if re.search(_SELECTIVE_LOW, text):
            return "LOWEST"
        if re.search(_SELECTIVE_BEST, text):
            return "BEST"
        if re.search(_SELECTIVE_WORST, text):
            # "lasciamo solo le peggiori" non ha senso operativo: non indovinare.
            return None

    # Forma "chiudi": il qualificatore descrive le posizioni da CHIUDERE.
    close_verb = (
        r"(?:CHIUDIAMO|CHIDUAMO|CHIUDAMO|CHIUDIAM|CHIUDETE|CHIUDERE|CHIUDO|"
        r"CHIUDI|USCIAMO|USCITE|ESCIAMO|CLOSE|CLOSING|EXIT)"
    )
    has_close_verb = re.search(rf"(?:^|[^\w]){close_verb}(?:[^\w]|$)", text) is not None
    # "Chiudo la rientry" / "chiudiamo il rientro": va chiuso l'ultimo blocco
    # aperto, non tutto il simbolo (in produzione portava via anche il setup base).
    # Il complemento deve seguire il verbo: "chiudiamo tutto" resta una chiusura
    # totale anche se la frase parla di rientri.
    if not re.search(r"\bTUTT[OIE]\b|\bALL\b|\bEVERYTHING\b", text) and re.search(
        rf"{close_verb}\s+(?:LA|IL|LE|I|GLI|LO|SOLO|SOLTANTO|THE)?\s*"
        rf"{_SELECTIVE_REENTRY_NOUN}",
        text,
    ):
        return "ALL_BUT_NEWEST"
    if not has_close_verb or not has_noun:
        return None
    if re.search(_SELECTIVE_WORST, text):
        return "BEST"          # chiudi le peggiori → restano le migliori
    if re.search(_SELECTIVE_LOW, text):
        return "HIGHEST"       # chiudi quelle sotto → restano quelle sopra
    if re.search(_SELECTIVE_HIGH, text):
        return "LOWEST"        # chiudi quelle sopra → restano quelle sotto
    if re.search(_SELECTIVE_BEST, text):
        return "LOWEST" if re.search(r"BUY|LONG", text) else "HIGHEST"
    return None


def match_close_all_intent(upper: str) -> tuple[bool, float | None]:
    """Detect explicit close-all / exit intent.

    Returns (matched, reference_price). ``reference_price`` is informational only;
    the EA always closes at market. Callers MUST run channel ignore_pats *before*
    this helper so preparatory phrases (PRONTI A CHIUDERE, GESTIAMO A MERCATO, …)
    never reach here.
    """
    if not upper or not upper.strip():
        return False, None

    # Imperative / 1st-person plural exit verbs + optional complement.
    # Il registro è additivo: ogni nuova forma vista in produzione va AGGIUNTA,
    # mai sostituita a una esistente.
    verb = (
        r"(?:"
        # uscire
        r"USCIAMO|USCITE|USCIRE|USCIAMOCI|"
        r"ESCIAMO|ESCO|ESCI|ESCITE|"
        # chiudere (CHIDUAMO/CHIUDAMO = typo mobile ricorrenti per CHIUDIAMO)
        r"CHIUDIAMO|CHIDUAMO|CHIUDAMO|CHIUDIAM|CHIUDETE|CHIUDERE|"
        r"CHIUDO|CHIUDI|CHIUSURA|"
        r"LIQUIDIAMO|LIQUIDA(?:RE|TE)?|"
        r"SVUOTIAMO|FLATTIAMO|"
        # inglese
        r"CLOSING|CLOSE|CLOSED|EXIT|EXITING|FLAT|"
        r"CLOSA(?:RE|TE|MO)?"
        r")"
    )
    complement = (
        r"(?:"
        r"ORA|QUI|TUTTO|TUTTI|TUTTE|ADESSO|SUBITO|IMMEDIATAMENTE|"
        r"POSIZIONI|POSIZIONE|OPERAZIONI|OPERAZIONE|TRADE|TRADES|"
        r"A\s+MERCATO|SUL\s+MERCATO|NOW|ALL(?:\s+POSITIONS)?|EVERYTHING"
        r")"
    )
    # Forme standalone: verbo imperativo da solo ("Chiudiamo", "Esco", "Uscite ✅").
    # Ammesse solo in messaggi brevi, così una frase narrativa
    # ("chiudiamo la settimana con questi risultati") non chiude i trade.
    standalone = (
        r"(?:"
        r"USCIAMO|USCITE|ESCIAMO|ESCO|ESCI|"
        r"CHIUDIAMO|CHIDUAMO|CHIUDAMO|CHIUDO|CHIUDETE|CHIUDI|"
        r"CLOSE|CLOSING|EXIT|FLAT"
        r")"
    )
    close_re = re.compile(
        rf"(?:^|[^\w])({verb})\s+({complement})\b|"
        rf"(?:^|[^\w])(USCIAMO|USCITE\s+TUTTI|CHIUDIAMO\s+TUTTO|CHIUDO\s+TUTTO|"
        rf"CLOSE\s*!*$|EXIT\s+ALL|CLOSE\s+ALL)\b",
        re.IGNORECASE,
    )
    standalone_re = re.compile(
        rf"(?:^|[^\w]){standalone}(?:[^\w]|$)",
        re.IGNORECASE,
    )
    if not close_re.search(upper):
        words = re.findall(r"[A-Za-zÀ-ÿ]+", upper)
        if len(words) > CLOSE_STANDALONE_MAX_WORDS or not standalone_re.search(upper):
            return False, None
        # Il verbo da solo dentro un consuntivo non è un ordine: "E anche oggi
        # chiudiamo in Profitto" racconta la giornata. Il comando esplicito
        # (verbo + complemento, es. "CHIUDIAMO ORA") passa dal ramo sopra.
        if matched_close_recap_pattern(upper):
            return False, None

    # Optional reference price: "a 5054", "@4060.5", "a 4060.5 -40 PIPS"
    price: float | None = None
    m_px = re.search(
        r"(?:^|\s)(?:A|@)\s*(\d{3,5}(?:[.,]\d+)?)\b",
        upper,
    )
    if not m_px:
        # "CHIUDIAMO ORA 4073", "Usciamo 4073": prezzo subito dopo il comando
        m_px = re.search(
            rf"(?:^|[^\w]){verb}(?:\s+{complement})?\s+(\d{{3,5}}(?:[.,]\d+)?)\b",
            upper,
        )
    if m_px:
        token = m_px.group(1).replace(",", ".")
        try:
            cand = float(token)
            # Gold-like absolute price, not tiny pip counts
            if cand >= 100:
                price = cand
        except ValueError:
            price = None
    return True, price


# ─────────────────────────────────────────────────────────────────────────────
# Break-even / chiusura parziale: registri di sinonimi condivisi (additivi)
# ─────────────────────────────────────────────────────────────────────────────

BREAK_EVEN_WORDS: tuple[str, ...] = (
    r"BREAK\s*-?\s*EVEN",
    r"BREAKEVEN",
    r"BREKIVEN",
    r"BREACK\s*EVEN",
    r"\bPAREGGIO\b",
    r"\bPAREGGI\w*",          # pareggia, pareggiamo, pareggiare, pareggiato
    r"\bPARI\b",
    r"IN\s+PARIT\w*",
    r"\bBE\s+MANUALE\b",
    r"(?:SL|STOP)\s+(?:A|AL|IN)\s+(?:BE|PAREGGIO|ENTRY|ENTRATA)",
)

PARTIAL_CLOSE_WORDS: tuple[str, ...] = (
    r"CLOSE\s+HALF",
    r"HALF\s+CLOSE",
    r"PARTIAL\s+CLOS\w*",
    r"\bPARTIAL\b",
    r"CLOSE\s+PART\w*",
    r"CHIUSURA\s+PARZIAL\w*",
    r"CHIUDI\w*\s+PARZIAL\w*",
    r"\bPARZIAL\w*",
    r"CHIUDI\w*\s+MET\w*",
    r"MET\w*\s+POSIZIONE",
    r"RIDUCIAMO\s+(?:LA\s+)?POSIZIONE",
)


def match_break_even_intent(upper: str) -> bool:
    """True se il testo contiene un riferimento a break-even/pareggio."""
    return any(re.search(p, upper) for p in BREAK_EVEN_WORDS)


def match_partial_close_intent(upper: str) -> bool:
    """True se il testo chiede una chiusura parziale / a metà."""
    return any(re.search(p, upper) for p in PARTIAL_CLOSE_WORDS)


# Spostamento dello stop a un prezzo esplicito: "spostiamo lo stop a 4255",
# "portiamo lo SL a 4255", "stop loss a 4255". Registro additivo.
_MOVE_SL_VERB = (
    r"(?:SPOST(?:IAMO|O|ATE|A|ARE)|MUOV(?:IAMO|O|ETE|I|ERE)|"
    r"PORT(?:IAMO|O|ATE|A|ARE)|ALZ(?:IAMO|O|ATE|A|ARE)|"
    r"ABBASS(?:IAMO|O|ATE|A|ARE)|METT(?:IAMO|O|ETE|I|ERE)|SETT(?:IAMO|O|ATE|A)|"
    r"MOVE|MOVING|SET)"
)
_MOVE_SL_NOUN = r"(?:STOP\s*LOSS|STOPLOSS|STOP|\bSL\b)"


def match_move_sl_price(upper: str) -> float | None:
    """Prezzo di un ordine "sposta lo stop a X", ``None`` se non è quel comando.

    Il break-even ha un recognizer suo (:func:`match_break_even_intent`) e va
    valutato prima: qui serve il caso con prezzo esplicito, che in produzione
    finiva UNPARSED ("Spostiamo lo stop a 4255" → le posizioni restavano sullo
    SL originale).
    """
    if not upper or not upper.strip():
        return None
    text = _fold_accents_upper(upper)
    m = re.search(
        rf"{_MOVE_SL_VERB}\s+(?:LO\s+|IL\s+|LA\s+|GLI\s+|THE\s+)?{_MOVE_SL_NOUN}"
        r"\s*(?:A|AL|SU|SUI|IN|TO|@)?\s*(\d{3,5}(?:[.,]\d+)?)\b",
        text,
    )
    if not m:
        # "Stop loss a 4255" senza verbo: solo se il messaggio è corto, così una
        # frase narrativa con numeri non muove gli stop.
        words = re.findall(r"[A-Za-z\u00c0-\u00ff]+", text)
        if len(words) > 6:
            return None
        m = re.search(
            rf"(?:^|[^\w]){_MOVE_SL_NOUN}\s*(?:A|AL|SU|IN|TO|@)\s*(\d{{3,5}}(?:[.,]\d+)?)\b",
            text,
        )
        if not m:
            return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return val if val >= 100 else None


# Spostamento di un take profit a un prezzo esplicito: "spostiamo TP 4 a 4376",
# "portiamo il tp2 a 4390", "move tp 3 to 4400". Registro additivo.
_MOVE_TP_VERB = (
    r"(?:SPOST(?:IAMO|O|ATE|A|ARE)|MUOV(?:IAMO|O|ETE|I|ERE)|"
    r"PORT(?:IAMO|O|ATE|A|ARE)|ALZ(?:IAMO|O|ATE|A|ARE)|"
    r"ABBASS(?:IAMO|O|ATE|A|ARE)|METT(?:IAMO|O|ETE|I|ERE)|SETT(?:IAMO|O|ATE|A)|"
    r"MODIFIC(?:HIAMO|O|ATE|A|ARE)|CAMBI(?:AMO|O|ATE|A|ARE)|"
    r"AGGIORN(?:IAMO|O|ATE|A|ARE)|ALLUNGH(?:IAMO|ERE)|ALLARGH(?:IAMO|IAMOLO)|"
    r"AVVICIN(?:IAMO|O|ATE|A|ARE)|ANTICIP(?:IAMO|O|ATE|A|ARE)|"
    r"MOVE|MOVING|SET|CHANGE|UPDATE|EXTEND)"
)
# "TYP" è il typo ricorrente del canale GOLD per "TP" ed è già trattato altrove.
_MOVE_TP_NOUN = r"(?:TAKE\s*PROFIT|TAKEPROFIT|TPS|TP|TYP|TARGET|OBIETTIVO)"
MOVE_TP_STANDALONE_MAX_WORDS = 8
MAX_TP_INDEX = 5


def match_move_tp_price(upper: str, *,
                        require_verb: bool = False) -> tuple[float, int | None] | None:
    """``(prezzo, indice_tp)`` di un ordine "sposta il TP n a X", altrimenti ``None``.

    ``indice_tp`` è ``None`` quando il messaggio non nomina un TP specifico
    ("portiamo i TP a 4390"): in quel caso il livello vale per tutte le
    posizioni del segnale. Il 17/08 ``Spostiamo TP 4 a 4376`` finiva UNPARSED e
    il TP4 restava sul livello originale.

    Il prezzo è restituito come scritto: l'espansione delle abbreviazioni
    ("TP 4 a 76") e la coerenza con la direzione spettano al parser del canale,
    che conosce l'ultimo setup.

    Con ``require_verb`` il comando vale solo nella forma esplicita ("spostiamo
    il TP 4 a 4376"): serve a distinguerlo da un commento nei canali che
    ignorano le frasi con "take profit".
    """
    if not upper or not upper.strip():
        return None
    text = _fold_accents_upper(upper)
    m = re.search(
        rf"{_MOVE_TP_VERB}\s+(?:LO\s+|IL\s+|LA\s+|GLI\s+|I\s+|THE\s+)?"
        rf"{_MOVE_TP_NOUN}\s*(?:(\d)\b\s*)?(?:A|AL|SU|SUI|IN|TO|@)?\s*"
        r"(\d{2,5}(?:[.,]\d+)?)\b",
        text,
    )
    if not m:
        # Ordine invertito: "TP 3 spostiamo a 4580" (28/08 finiva UNPARSED).
        m = re.search(
            rf"(?:^|[^\w]){_MOVE_TP_NOUN}\s*(?:(\d)\b\s*)?{_MOVE_TP_VERB}"
            r"\s*(?:A|AL|SU|SUI|IN|TO|@)?\s*(\d{2,5}(?:[.,]\d+)?)\b",
            text,
        )
    if not m:
        if require_verb:
            return None
        # "TP 4 a 4376" senza verbo: solo in messaggi corti, così un commento
        # con numeri ("TP 3 HIT +100 PIPS") non muove i livelli.
        words = re.findall(r"[A-Za-z\u00c0-\u00ff]+", text)
        if len(words) > MOVE_TP_STANDALONE_MAX_WORDS:
            return None
        m = re.search(
            rf"(?:^|[^\w]){_MOVE_TP_NOUN}\s*(?:(\d)\b\s*)?(?:A|AL|SU|IN|TO|@)\s*"
            r"(\d{2,5}(?:[.,]\d+)?)\b",
            text,
        )
        if not m:
            return None
    try:
        val = float(m.group(2).replace(",", "."))
    except ValueError:
        return None
    index = int(m.group(1)) if m.group(1) else None
    if index is not None and not 1 <= index <= MAX_TP_INDEX:
        return None
    return val, index


def match_close_price_followup(upper: str) -> float | None:
    """Price-only follow-up after a close signal without price ('A 4060.5')."""
    m = re.match(r"^(?:A|@)?\s*(\d{3,5}(?:[.,]\d+)?)\s*$", upper.strip())
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return val if val >= 100 else None


ACTIONS_WITH_SL_TP = frozenset({
    "OPEN",
    "UPDATE_OPEN",
})


def _is_retryable_write_error(exc: BaseException) -> bool:
    """True for Windows file-lock / access-denied (WinError 5) and POSIX EACCES/EAGAIN."""
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        winerr = getattr(exc, "winerror", None)
        if winerr in (5, 32):  # ACCESS_DENIED, SHARING_VIOLATION
            return True
        if getattr(exc, "errno", None) in (11, 13, 16):  # EAGAIN, EACCES, EBUSY
            return True
    return False


def atomic_write_text(
    path: Path,
    payload: str,
    *,
    retries: int = 5,
    backoff_ms: float = 40.0,
) -> None:
    """Write text atomically via temp file + os.replace.

    Retries on PermissionError / WinError 5 when the EA holds the JSON file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: BaseException | None = None

    for attempt in range(max(1, retries)):
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            os.replace(tmp_name, path)
            if attempt > 0:
                log.warning(
                    "atomic_write retry ok path=%s attempt=%s",
                    path,
                    attempt + 1,
                )
            return
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            if _is_retryable_write_error(exc) and attempt + 1 < retries:
                last_err = exc
                delay = (backoff_ms / 1000.0) * (attempt + 1)
                log.warning(
                    "atomic_write locked path=%s attempt=%s/%s err=%s; sleep=%.0fms",
                    path,
                    attempt + 1,
                    retries,
                    exc,
                    delay * 1000,
                )
                time.sleep(delay)
                continue
            raise

    if last_err is not None:
        raise last_err


_WRITE_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg_write")


def is_unc_path(path: Path | str) -> bool:
    s = str(path).replace("/", "\\")
    return s.startswith("\\\\")


def atomic_write_text_timed(
    path: Path,
    payload: str,
    *,
    timeout_sec: float | None = None,
    retries: int = 5,
    backoff_ms: float = 40.0,
) -> None:
    """Like atomic_write_text but never block the caller forever on hung SMB/UNC.

    Default timeout: 4s for UNC shares, 20s for local disks.
    """
    path = Path(path)
    if timeout_sec is None:
        timeout_sec = 4.0 if is_unc_path(path) else 20.0
    fut = _WRITE_POOL.submit(
        atomic_write_text,
        path,
        payload,
        retries=retries,
        backoff_ms=backoff_ms,
    )
    try:
        fut.result(timeout=timeout_sec)
    except FuturesTimeout as exc:
        raise TimeoutError(
            f"atomic_write timeout after {timeout_sec:.1f}s path={path}"
        ) from exc


def atomic_write_json(
    path: Path,
    data: dict,
    *,
    retries: int = 5,
    backoff_ms: float = 40.0,
) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=2),
        retries=retries,
        backoff_ms=backoff_ms,
    )


# Lo stato non è mai sulla strada critica del segnale: un lock del filesystem
# (antivirus, EA, backup) non deve impedire la scrittura del JSON per l'EA.
STATE_WRITE_RETRIES = 8
STATE_WRITE_BACKOFF_MS = 60.0


def save_state_json(path: Path, data: dict, label: str) -> bool:
    """Persist internal state best-effort; log and continue on failure."""
    try:
        atomic_write_json(
            path,
            data,
            retries=STATE_WRITE_RETRIES,
            backoff_ms=STATE_WRITE_BACKOFF_MS,
        )
        return True
    except (OSError, TimeoutError) as exc:
        log.error("Could not save %s %s: %s (stato solo in memoria)", label, path, exc)
        return False


class BridgeState:
    """Persistent CH2 naked→complete and ORO partial/update state."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.ch2_pending_dir: str | None = None
        self.ch2_pending_open: bool = False
        self.gold_last_trade: dict | None = None
        # ultimo comando di gestione GOLD emesso: {"key": str, "ts": float}
        self.gold_last_cmd: dict | None = None
        self.stark_last_trade: dict | None = None
        self.ivan_last_trade: dict | None = None
        self.oro_pending_dir: str | None = None
        self.oro_pending_entry: float | None = None
        self.oro_pending_range: list[float] | None = None
        self.oro_pending_sl: float | None = None
        self.oro_pending_tps: list[float] | None = None
        self.oro_last_trade: dict | None = None
        self.forex_pending_symbol: str | None = None
        self.forex_pending_dir: str | None = None
        self.forex_pending_entry: float | None = None
        self.forex_last_trade: dict | None = None
        # channel_id -> unix expiry: await price-only follow-up after CLOSE without price
        self.close_price_pending: dict[str, float] = {}
        self.load()

    def load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            ch2 = data.get("ch2_pending", {})
            self.ch2_pending_dir = ch2.get("pending_dir")
            self.ch2_pending_open = bool(ch2.get("pending_open", False))
            self.gold_last_trade = data.get("gold_last_trade")
            glc = data.get("gold_last_cmd")
            self.gold_last_cmd = glc if isinstance(glc, dict) else None
            self.stark_last_trade = data.get("stark_last_trade")
            self.ivan_last_trade = data.get("ivan_last_trade")
            oro_p = data.get("oro_pending", {})
            self.oro_pending_dir = oro_p.get("direction")
            self.oro_pending_entry = oro_p.get("entry")
            pr = oro_p.get("entry_range")
            self.oro_pending_range = pr if isinstance(pr, list) else None
            self.oro_pending_sl = oro_p.get("sl")
            pt = oro_p.get("tp_levels")
            self.oro_pending_tps = pt if isinstance(pt, list) else None
            self.oro_last_trade = data.get("oro_last_trade")
            fx_p = data.get("forex_pending", {})
            self.forex_pending_symbol = fx_p.get("symbol")
            self.forex_pending_dir = fx_p.get("direction")
            self.forex_pending_entry = fx_p.get("entry")
            self.forex_last_trade = data.get("forex_last_trade")
            cpp = data.get("close_price_pending") or {}
            self.close_price_pending = {
                str(k): float(v) for k, v in cpp.items() if v is not None
            }
            log.info(
                "Bridge state loaded: ch2_pending_open=%s dir=%s oro_pending=%s forex_pending=%s",
                self.ch2_pending_open,
                self.ch2_pending_dir,
                self.oro_pending_dir,
                self.forex_pending_symbol,
            )
        except Exception as exc:
            log.warning("Could not load bridge state %s: %s", self.state_file, exc)

    def save(self) -> None:
        payload = {
            "ch2_pending": {
                "pending_open": self.ch2_pending_open,
                "pending_dir": self.ch2_pending_dir,
            },
            "gold_last_trade": self.gold_last_trade,
            "gold_last_cmd": self.gold_last_cmd,
            "stark_last_trade": self.stark_last_trade,
            "ivan_last_trade": self.ivan_last_trade,
            "oro_pending": {
                "direction": self.oro_pending_dir,
                "entry": self.oro_pending_entry,
                "entry_range": self.oro_pending_range,
                "sl": self.oro_pending_sl,
                "tp_levels": self.oro_pending_tps,
            },
            "oro_last_trade": self.oro_last_trade,
            "forex_pending": {
                "symbol": self.forex_pending_symbol,
                "direction": self.forex_pending_dir,
                "entry": self.forex_pending_entry,
            },
            "forex_last_trade": self.forex_last_trade,
            "close_price_pending": self.close_price_pending,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state_json(self.state_file, payload, "bridge state")

    def set_close_price_pending(self, channel_id: str, ttl_sec: float = 120.0) -> None:
        self.close_price_pending[channel_id] = time.time() + ttl_sec
        self.save()

    def pop_close_price_pending(self, channel_id: str) -> bool:
        """Return True if a non-expired pending close-price wait existed."""
        exp = self.close_price_pending.pop(channel_id, None)
        if exp is None:
            return False
        self.save()
        return time.time() <= float(exp)

    def set_ch2_pending(self, direction: str) -> None:
        self.ch2_pending_dir = direction
        self.ch2_pending_open = True
        self.save()

    def clear_ch2_pending(self) -> None:
        self.ch2_pending_open = False
        self.ch2_pending_dir = None
        self.save()

    def set_gold_last_trade(self, trade: dict) -> None:
        self.gold_last_trade = {
            "ts": trade.get("ts", time.time()),
            "direction": trade.get("direction"),
            "entry": trade.get("entry"),
            "entry_range": trade.get("entry_range"),
            "sl": trade.get("sl"),
            "tp_levels": list(trade.get("tp_levels") or []),
        }
        self.save()

    def clear_gold_last_trade(self) -> None:
        self.gold_last_trade = None
        self.save()

    def set_gold_last_cmd(self, key: str) -> None:
        """Registra l'ultimo comando di gestione GOLD emesso (dedup IT/EN)."""
        self.gold_last_cmd = {"key": key, "ts": time.time()}
        self.save()

    def set_stark_last_trade(self, trade: dict) -> None:
        self.stark_last_trade = {
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
        }
        self.save()

    def clear_stark_last_trade(self) -> None:
        self.stark_last_trade = None
        self.save()

    def set_ivan_last_trade(self, trade: dict) -> None:
        self.ivan_last_trade = {
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "entry": trade.get("entry"),
            # Un setup pubblicato come zona ha entry=None: senza la zona i
            # comandi successivi (UPDATE_TP, rientri) perdono il riferimento e
            # il controllo di coerenza sui livelli non scatta.
            "entry_range": (
                list(trade["entry_range"]) if trade.get("entry_range") else None
            ),
            "sl": trade.get("sl"),
            "tp_levels": list(trade.get("tp_levels") or []),
            "lot_factor": trade.get("lot_factor"),
            "allow_stack": bool(trade.get("allow_stack")),
            # Entry pubblicato dal setup base: il BE del canale ("Spostiamo SL a
            # BE") si riferisce a questo prezzo, non al fill; un rientro lo
            # eredita cosi' il riferimento non si perde.
            "setup_entry": trade.get("setup_entry"),
            "ts": trade.get("ts", time.time()),
        }
        self.save()

    def set_oro_pending(
        self,
        direction: str,
        entry: float | None = None,
        entry_range: list[float] | None = None,
    ) -> None:
        self.oro_pending_dir = direction
        self.oro_pending_entry = entry
        self.oro_pending_range = entry_range
        self.oro_pending_sl = None
        self.oro_pending_tps = None
        self.save()

    def oro_pending_add_levels(self, sl: float | None, tps: list[float] | None) -> None:
        if sl is not None:
            self.oro_pending_sl = sl
        if tps:
            self.oro_pending_tps = tps
        self.save()

    def clear_oro_pending(self) -> None:
        self.oro_pending_dir = None
        self.oro_pending_entry = None
        self.oro_pending_range = None
        self.oro_pending_sl = None
        self.oro_pending_tps = None
        self.save()

    def set_oro_last_trade(self, trade: dict) -> None:
        self.oro_last_trade = {
            "direction": trade.get("direction"),
            "entry": trade.get("entry"),
            "entry_range": trade.get("entry_range"),
            "sl": trade.get("sl"),
            "tp_levels": list(trade.get("tp_levels") or []),
        }
        self.save()

    def set_forex_pending(
        self,
        symbol: str,
        direction: str,
        entry: float | None = None,
    ) -> None:
        self.forex_pending_symbol = symbol
        self.forex_pending_dir = direction
        self.forex_pending_entry = entry
        self.save()

    def clear_forex_pending(self) -> None:
        self.forex_pending_symbol = None
        self.forex_pending_dir = None
        self.forex_pending_entry = None
        self.save()

    def set_forex_last_trade(self, trade: dict) -> None:
        self.forex_last_trade = {
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "entry": trade.get("entry"),
            "sl": trade.get("sl"),
            "tp_levels": list(trade.get("tp_levels") or []),
        }
        self.save()

    def clear_forex_last_trade(self) -> None:
        self.forex_last_trade = None
        self.save()


class EphemeralBridgeState(BridgeState):
    """In-memory state for dry-run; never reads/writes disk (Windows-safe)."""

    def __init__(self):
        self.state_file = Path("_ephemeral_")
        self.ch2_pending_dir: str | None = None
        self.ch2_pending_open: bool = False
        self.gold_last_trade: dict | None = None
        self.gold_last_cmd: dict | None = None
        self.stark_last_trade: dict | None = None
        self.ivan_last_trade: dict | None = None
        self.oro_pending_dir: str | None = None
        self.oro_pending_entry: float | None = None
        self.oro_pending_range: list[float] | None = None
        self.oro_pending_sl: float | None = None
        self.oro_pending_tps: list[float] | None = None
        self.oro_last_trade: dict | None = None
        self.forex_pending_symbol: str | None = None
        self.forex_pending_dir: str | None = None
        self.forex_pending_entry: float | None = None
        self.forex_last_trade: dict | None = None
        self.close_price_pending: dict[str, float] = {}

    def load(self) -> None:
        return

    def save(self) -> None:
        return


class ProcessedMessageStore:
    """Persistent deduplication for Telegram events."""

    def __init__(self, store_file: Path, max_entries: int = 5000):
        self.store_file = store_file
        self.max_entries = max_entries
        self._keys: list[str] = []
        self._seen: set[str] = set()
        self.load()

    def load(self) -> None:
        if not self.store_file.exists():
            return
        try:
            data = json.loads(self.store_file.read_text(encoding="utf-8"))
            self._keys = list(data.get("keys", []))
            self._seen = set(self._keys)
            log.info("Loaded %d processed message keys", len(self._keys))
        except Exception as exc:
            log.warning("Could not load processed messages %s: %s", self.store_file, exc)

    def save(self) -> None:
        if len(self._keys) > self.max_entries:
            self._keys = self._keys[-self.max_entries :]
            self._seen = set(self._keys)
        save_state_json(
            self.store_file,
            {"keys": self._keys, "updated_at": datetime.now(timezone.utc).isoformat()},
            "processed messages",
        )

    @staticmethod
    def make_key(
        chat_id: int,
        message_id: int,
        event_type: str,
        text: str,
    ) -> str:
        if event_type == "EDIT":
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            return f"{chat_id}:{message_id}:EDIT:{digest}"
        return f"{chat_id}:{message_id}:NEW"

    def is_duplicate(self, key: str) -> bool:
        return key in self._seen

    def mark_processed(self, key: str, *aliases: str) -> None:
        """Mark key as processed; aliases pre-book equivalent future events."""
        added = False
        for k in (key, *aliases):
            if k in self._seen:
                continue
            self._keys.append(k)
            self._seen.add(k)
            added = True
        if added:
            self.save()


def _sl_coherent(direction: str, entry: float | None, sl: float) -> bool:
    if entry is None:
        return sl > 0
    if direction == "BUY":
        return sl < entry
    if direction == "SELL":
        return sl > entry
    return False


def _sl_coherent_with_range(direction: str, entry_range: list[float] | None, sl: float) -> bool:
    if sl <= 0:
        return False
    if not entry_range or len(entry_range) != 2:
        return True
    lo, hi = min(entry_range), max(entry_range)
    if direction == "BUY":
        return sl < lo
    if direction == "SELL":
        return sl > hi
    return False


def _tp_coherent(direction: str, entry: float | None, tps: list[float]) -> bool:
    if not tps:
        return True
    for tp in tps:
        if tp <= 0:
            return False
        if entry is None:
            continue
        if direction == "BUY" and tp <= entry:
            return False
        if direction == "SELL" and tp >= entry:
            return False
    return True


def _tp_coherent_with_range(direction: str, entry_range: list[float] | None, tps: list[float]) -> bool:
    if not tps:
        return True
    if not entry_range or len(entry_range) != 2:
        return _tp_coherent(direction, None, tps)
    lo, hi = min(entry_range), max(entry_range)
    for tp in tps:
        if tp <= 0:
            return False
        if direction == "BUY" and tp <= hi:
            return False
        if direction == "SELL" and tp >= lo:
            return False
    return True


def _entry_range_plausible(entry_range: list[float] | None) -> bool:
    if not entry_range:
        return True
    if len(entry_range) != 2:
        return False
    lo, hi = entry_range
    if lo <= 0 or hi <= 0:
        return False
    if lo > hi:
        return False
    return (hi - lo) <= 500


def _splits_valid(splits: list[float] | None) -> bool:
    if splits is None:
        return True
    if not splits:
        return False
    if any(s < 0 for s in splits):
        return False
    total = sum(splits)
    return 0.95 <= total <= 1.05


def validate_signal(signal: dict) -> tuple[bool, str]:
    """Validate payload before writing JSON for the EA."""
    action = signal.get("action")
    if action not in VALID_ACTIONS:
        return False, f"invalid action: {action!r}"

    if action in ACTIONS_REQUIRING_DIRECTION:
        direction = signal.get("direction")
        if direction not in ("BUY", "SELL"):
            return False, f"direction must be BUY/SELL, got {direction!r}"

    if action in ACTIONS_REQUIRING_SYMBOL:
        symbol = (signal.get("symbol") or "").strip()
        if not symbol:
            return False, "symbol is required"

    magic_base = signal.get("magic_base")
    if magic_base is not None:
        try:
            if int(magic_base) <= 0:
                return False, "magic_base must be positive"
        except (TypeError, ValueError):
            return False, "magic_base must be an integer"

    if not _splits_valid(signal.get("splits")):
        return False, "splits must sum to ~1.0 and be non-negative"

    if action in ACTIONS_WITH_SL_TP:
        direction = signal.get("direction", "")
        entry = signal.get("entry")
        entry_range = signal.get("entry_range")
        sl = signal.get("sl")
        tps = signal.get("tp_levels") or []

        if entry_range is not None and not _entry_range_plausible(entry_range):
            return False, "entry_range is not plausible"

        if sl is not None and direction in ("BUY", "SELL"):
            if entry_range is not None:
                if not _sl_coherent_with_range(direction, entry_range, sl):
                    return False, f"SL {sl} incoherent with {direction} range {entry_range}"
            elif not _sl_coherent(direction, entry, sl):
                return False, f"SL {sl} incoherent with {direction} entry {entry}"

        if tps and direction in ("BUY", "SELL"):
            if entry_range is not None:
                if not _tp_coherent_with_range(direction, entry_range, tps):
                    return False, f"TP levels {tps} incoherent with {direction} range {entry_range}"
            elif not _tp_coherent(direction, entry, tps):
                return False, f"TP levels {tps} incoherent with {direction} entry {entry}"

    if action == "CLOSE_SELECTIVE":
        keep = signal.get("keep")
        if keep not in SELECTIVE_KEEP_MODES:
            return False, f"CLOSE_SELECTIVE keep must be one of {sorted(SELECTIVE_KEEP_MODES)}, got {keep!r}"

    if action == "OPEN_NOW":
        if signal.get("entry") is not None:
            return False, "OPEN_NOW must not include entry"
        if signal.get("sl") is not None or signal.get("tp_levels"):
            return False, "OPEN_NOW must not include SL/TP"

    return True, ""


def payload_for_ea(signal: dict) -> dict:
    """Drop the null fields before writing the signal file.

    The EA reads arrays by looking for the key and then for the next ``[`` in the
    file: on ``"entry_range": null`` it picked up ``tp_levels`` instead, so TP1/TP2
    became the entry zone and every IVAN setup (single entry price) was cancelled
    for distance. Omitting the key keeps the reader on the right array.
    """
    return {k: v for k, v in signal.items() if v is not None}


def salvage_incoherent_entry_range(signal: dict, reason: str) -> str | None:
    """Rescue the SL/TP of an UPDATE_OPEN whose entry zone has a typo.

    On 2026-08-14 CH_GOLD posted ``Gold sell now 4342 - 4450`` (4350 mistyped)
    three minutes after the naked open: the payload was rejected as a whole and
    the tickets stayed without SL for 24 minutes, until the channel edited the
    message. The zone is only needed to decide whether to open; when SL and TP
    are coherent with each other they can still protect positions already open,
    so drop the zone and mark the payload ``levels_only`` (the EA then modifies
    but never opens). Returns a log line when salvaged, None otherwise.
    """
    if signal.get("action") != "UPDATE_OPEN":
        return None
    if "range" not in reason:
        return None
    entry_range = signal.get("entry_range")
    if entry_range is None:
        return None
    direction = signal.get("direction")
    if direction not in ("BUY", "SELL"):
        return None
    sl = signal.get("sl")
    tps = signal.get("tp_levels") or []
    if not sl or sl <= 0 or not tps or any((tp or 0) <= 0 for tp in tps):
        return None
    # SL on one side, every TP on the other: the levels alone are usable.
    if direction == "BUY" and min(tps) <= sl:
        return None
    if direction == "SELL" and max(tps) >= sl:
        return None

    signal["entry_range"] = None
    signal["levels_only"] = True
    return (
        f"entry_range {entry_range} scartato ({reason}): applico solo "
        f"SL {sl} e TP {tps} alle posizioni aperte (levels_only)"
    )


def apply_lot_rules(signal: dict, ch: dict) -> dict:
    """Fixed lots: 0.20 with one TP, 0.10 per TP when multiple levels are set.

    OPEN_NOW (naked, no TP yet) uses the channel's ``tp_levels_expected`` so GOLD
    (expected=2) opens 2x ``fixed_lot_per_tp`` immediately; channels with
    expected=1 keep a single ``fixed_lot_single`` ticket.
    """
    exec_cfg = ch.get("execution", {})
    lot_single = float(exec_cfg.get("fixed_lot_single", ch.get("fixed_lot_single", 0.20)))
    lot_per_tp = float(exec_cfg.get("fixed_lot_per_tp", ch.get("fixed_lot_per_tp", 0.10)))

    action = signal.get("action", "")
    if action not in ("OPEN", "OPEN_NOW", "UPDATE_OPEN"):
        return signal

    tps = signal.get("tp_levels") or []
    n_tp = len(tps)

    signal["use_fixed_lot"] = True
    signal.pop("risk_percent", None)

    lot_factor = float(signal.get("lot_factor", 1.0))

    if action == "OPEN_NOW" and n_tp == 0:
        expected = int(
            exec_cfg.get("tp_levels_expected", ch.get("tp_levels_expected", 1)) or 1
        )
        if expected < 1:
            expected = 1
        if expected >= 2:
            signal["trades"] = expected
            signal["fixed_lot"] = lot_per_tp * lot_factor
            signal["splits"] = [round(1.0 / expected, 4)] * expected
        else:
            signal["trades"] = 1
            signal["fixed_lot"] = lot_single * lot_factor
            signal["splits"] = [1.0]
        return signal

    if n_tp >= 2:
        signal["trades"] = n_tp
        signal["fixed_lot"] = lot_per_tp * lot_factor
        signal["splits"] = [round(1.0 / n_tp, 4)] * n_tp
    else:
        signal["trades"] = 1
        signal["fixed_lot"] = lot_single * lot_factor
        signal["splits"] = [1.0]

    return signal
