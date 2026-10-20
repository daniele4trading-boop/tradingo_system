"""Interprete LLM dei messaggi di gestione del canale IVAN.

Il parser a parole chiave (:func:`tradingo_bridge.parser_ivan_vip`) resta la
fonte dei setup numerici (entry/SL/TP) e delle chiusure urgenti. Tutto il resto
("mettiamo a BE le entry più brutte", "lasciamo con stop originale le
migliori", "chiudiamo la reentry") è linguaggio naturale, spesso spezzato su
più messaggi consecutivi: qui viene interpretato da un modello Anthropic con
il contesto del setup aperto e degli ultimi messaggi, e tradotto in un payload
JSON del contratto EA (docs/EA_SPEC.md). Sotto la soglia di confidenza non si
emette nulla e si logga la frase dubbia.

Nessuna dipendenza nuova: chiamata HTTPS con urllib.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger("tradingo.ivan_llm")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_DEBOUNCE_SEC = 25.0
DEFAULT_CONTEXT_MESSAGES = 8
DEFAULT_TIMEOUT_SEC = 20.0

# Azioni che il modello può restituire e il payload EA corrispondente.
ACTIONS = (
    "NONE",             # chiacchiere, screenshot, TP hit, promo
    "BE_ALL",           # SL a pareggio su tutte le gambe
    "BE_SCOPE",         # SL a pareggio solo su un gruppo (scope)
    "CLOSE_ALL",        # chiudi tutto il simbolo
    "CLOSE_LAST_SETUP", # chiudi solo il blocco più recente (reentry)
    "CLOSE_SCOPE",      # chiudi un gruppo, tieni il resto (keep)
    "MOVE_SL",          # nuovo SL numerico
    "MOVE_TP",          # nuovo TP numerico (tp_index opzionale)
    "WAIT",             # attesa / nessuna operazione ora
)
SCOPES = ("WORST", "BEST", "HIGHEST", "LOWEST")
KEEPS = ("BEST", "HIGHEST", "LOWEST", "ALL_BUT_NEWEST")

_URGENT_RE = re.compile(r"\b(ORA|SUBITO|ADESSO|IMMEDIATAMENTE|NOW)\b")

SYSTEM_PROMPT = """Sei l'interprete dei messaggi di un canale Telegram di segnali sull'oro (XAUUSD).
Il trader (Ivan) pubblica un setup con 4 take profit (TP1..TP4) e poi, in italiano colloquiale e spesso
spezzato su più messaggi consecutivi, dà indicazioni per gestire le posizioni già aperte.
Noi copiamo il setup con 4 posizioni uguali (T1..T4, una per TP), tutte allo stesso prezzo di ingresso.

Devi tradurre l'INSIEME dei messaggi recenti (l'ultimo è il più importante, i precedenti sono contesto)
in UNA sola azione operativa tra:
- NONE: commenti, risultati ("TP1 HIT", "+80 pips"), promozioni, saluti, screenshot, domande.
- BE_ALL: portare a pareggio (break even, "BE", "stop a entry") TUTTE le posizioni.
- BE_SCOPE: portare a pareggio SOLO un gruppo di entry; scope = WORST (le peggiori/più brutte/meno premium),
  BEST (le migliori/più premium), HIGHEST (le più alte), LOWEST (le più basse). Le altre restano con lo SL originale.
  "Chiudere a BE le entry alte" è BE_SCOPE HIGHEST, NON una chiusura.
- CLOSE_ALL: chiudere tutto subito a mercato ("chiudiamo tutto", "chiudiamo ora", "usciamo").
- CLOSE_LAST_SETUP: chiudere solo il rientro/reentry (il blocco più recente), il setup iniziale resta.
- CLOSE_SCOPE: chiudere a mercato un gruppo e tenere il resto; keep = cosa RESTA aperto (BEST/HIGHEST/LOWEST).
- MOVE_SL: nuovo stop loss numerico (price).
- MOVE_TP: nuovo take profit numerico (price, tp_index 1-4 oppure null se riferito a tutti).
- WAIT: invito ad aspettare / non fare nulla ora ("aspettiamo prima di rientrare").

Regole:
- Un nuovo setup completo (BUY/SELL con prezzo, SL e TP) NON è compito tuo: rispondi NONE.
- Se i messaggi si contraddicono, vale l'ultimo. Se un messaggio precisa il precedente
  ("e lasciamo con stop originale le migliori"), combinali in un'unica azione.
- Nel dubbio rispondi NONE con confidenza bassa: non eseguire è meglio di eseguire male.
- Rispondi SOLO con un oggetto JSON: {"action": ..., "scope": ..., "keep": ..., "price": ..., "tp_index": ...,
  "confidence": 0.0-1.0, "reason": "breve motivazione in italiano"}. Campi non pertinenti: null."""


@dataclass
class InterpreterConfig:
    enabled: bool = False
    shadow: bool = True
    model: str = DEFAULT_MODEL
    api_key_env: str = "ANTHROPIC_API_KEY"
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    debounce_sec: float = DEFAULT_DEBOUNCE_SEC
    context_messages: int = DEFAULT_CONTEXT_MESSAGES
    timeout_sec: float = DEFAULT_TIMEOUT_SEC

    @classmethod
    def from_channel(cls, ch: dict) -> InterpreterConfig:
        raw = ch.get("interpreter") or {}
        if not isinstance(raw, dict):
            return cls()
        cfg = cls()
        for k in cfg.__dataclass_fields__:
            if k in raw and raw[k] is not None:
                setattr(cfg, k, raw[k])
        return cfg


@dataclass
class Interpretation:
    action: str = "NONE"
    scope: str | None = None
    keep: str | None = None
    price: float | None = None
    tp_index: int | None = None
    confidence: float = 0.0
    reason: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_model_json(cls, data: dict) -> Interpretation:
        action = str(data.get("action") or "NONE").upper()
        if action not in ACTIONS:
            action = "NONE"
        scope = data.get("scope")
        scope = str(scope).upper() if scope else None
        if scope not in SCOPES:
            scope = None
        keep = data.get("keep")
        keep = str(keep).upper() if keep else None
        if keep not in KEEPS:
            keep = None
        price = data.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        tp_index = data.get("tp_index")
        try:
            tp_index = int(tp_index) if tp_index is not None else None
        except (TypeError, ValueError):
            tp_index = None
        if tp_index is not None and not 1 <= tp_index <= 4:
            tp_index = None
        try:
            conf = float(data.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        if action == "BE_SCOPE" and scope is None:
            action, conf = "NONE", 0.0
        if action == "CLOSE_SCOPE" and keep is None:
            action, conf = "NONE", 0.0
        if action in ("MOVE_SL", "MOVE_TP") and price is None:
            action, conf = "NONE", 0.0
        return cls(action, scope, keep, price, tp_index, conf,
                   str(data.get("reason") or ""), data)


def is_urgent(text: str) -> bool:
    """Chiusure/ordini con "ORA/SUBITO": vanno eseguiti senza attendere il debounce."""
    return bool(_URGENT_RE.search(text.upper()))


def build_user_prompt(messages: list[str], last_trade: dict | None) -> str:
    lines = []
    if last_trade:
        tps = last_trade.get("tp_levels") or []
        entry = last_trade.get("entry")
        rng = last_trade.get("entry_range")
        entry_txt = f"{rng[0]}-{rng[1]}" if rng else str(entry)
        lines.append(
            f"SETUP APERTO: {last_trade.get('direction')} {last_trade.get('symbol')} "
            f"entry {entry_txt} SL {last_trade.get('sl')} TP {tps}"
            + (" (è un rientro/reentry)" if last_trade.get("allow_stack") else "")
        )
    else:
        lines.append("SETUP APERTO: nessuno noto")
    lines.append("MESSAGGI RECENTI (dal più vecchio al più nuovo):")
    for i, m in enumerate(messages, 1):
        lines.append(f"[{i}] {m.strip()}")
    lines.append("Interpreta l'ultimo messaggio alla luce dei precedenti e rispondi con il JSON.")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"nessun JSON nella risposta: {text[:120]!r}")
    return json.loads(m.group(0))


def anthropic_complete(system: str, user: str, cfg: InterpreterConfig) -> str:
    api_key = os.environ.get(cfg.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"variabile {cfg.api_key_env} assente")
    body = json.dumps({
        "model": cfg.model,
        "max_tokens": 300,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Anthropic HTTP {e.code}: {e.read()[:200]!r}") from e
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if p.get("type") == "text")


Completer = Callable[[str, str, InterpreterConfig], str]


class IvanInterpreter:
    def __init__(self, cfg: InterpreterConfig, completer: Completer | None = None):
        self.cfg = cfg
        self._complete = completer or anthropic_complete

    def interpret(self, messages: list[str], last_trade: dict | None) -> Interpretation:
        msgs = [m for m in messages if m and m.strip()][-self.cfg.context_messages:]
        if not msgs:
            return Interpretation()
        prompt = build_user_prompt(msgs, last_trade)
        try:
            text = self._complete(SYSTEM_PROMPT, prompt, self.cfg)
            result = Interpretation.from_model_json(_extract_json(text))
        except Exception as e:  # noqa: BLE001 - rete, JSON, chiave: mai bloccare il bridge
            log.warning(f"[IVAN_LLM] errore interprete: {e}")
            return Interpretation(reason=f"errore: {e}")
        log.info(
            f"[IVAN_LLM] {result.action} scope={result.scope} keep={result.keep} "
            f"price={result.price} conf={result.confidence:.2f} :: {result.reason[:80]} "
            f"<- {msgs[-1][:60]!r}"
        )
        if result.action not in ("NONE", "WAIT") and result.confidence < self.cfg.min_confidence:
            log.warning(
                f"[IVAN_LLM LOW_CONF] {result.action} conf={result.confidence:.2f} "
                f"< {self.cfg.min_confidence}: nessuna azione per {msgs[-1][:80]!r}"
            )
            return Interpretation(reason=f"low_conf:{result.action}", raw=result.raw)
        return result


def interpretation_to_signal(
    res: Interpretation, ch: dict, last_trade: dict | None, raw_message: str,
) -> dict | None:
    """Traduce l'azione interpretata nel payload JSON del contratto EA."""
    base = {
        "symbol": "XAUUSD",
        "magic_base": ch["magic_base"],
        "raw_message": raw_message,
        "source": "llm",
        "llm_reason": res.reason,
    }
    direction = (last_trade or {}).get("direction")
    if res.action in ("NONE", "WAIT"):
        return None
    if res.action == "BE_ALL":
        sig = {"action": "CHECK_AND_BE", "tp_index": 1, **base}
    elif res.action == "BE_SCOPE":
        sig = {"action": "CHECK_AND_BE", "tp_index": 1, "scope": res.scope, **base}
    elif res.action == "CLOSE_ALL":
        sig = {"action": "CLOSE_ALL_SYMBOL", **base}
    elif res.action == "CLOSE_LAST_SETUP":
        sig = {"action": "CLOSE_SELECTIVE", "keep": "ALL_BUT_NEWEST", **base}
    elif res.action == "CLOSE_SCOPE":
        sig = {"action": "CLOSE_SELECTIVE", "keep": res.keep, **base}
    elif res.action == "MOVE_SL":
        sig = {"action": "UPDATE_SL", "direction": direction, "new_sl": res.price, **base}
    elif res.action == "MOVE_TP":
        sig = {"action": "UPDATE_TP", "direction": direction, "new_tp": res.price, **base}
        if res.tp_index is not None:
            sig["tp_index"] = res.tp_index
    else:
        return None
    if res.action in ("BE_ALL", "BE_SCOPE") and last_trade:
        be = last_trade.get("setup_entry")
        if isinstance(be, (int, float)):
            sig["be_price"] = float(be)
    return sig


DEFAULT_WINDOW_SEC = 300.0


class IvanMessageBuffer:
    """Finestra dei messaggi recenti di un canale e coda dei non ancora valutati.

    ``push`` registra un messaggio; ``take_pending`` restituisce il contesto
    (ultimi ``window_sec``) e svuota la coda dei messaggi in attesa. Logica
    pura, senza asyncio: il bridge decide quando chiamarla (debounce).
    """

    def __init__(self, window_sec: float = DEFAULT_WINDOW_SEC):
        self.window_sec = window_sec
        self._history: list[tuple[float, int, str]] = []
        self._pending_ids: list[int] = []

    def push(self, ts: float, message_id: int, text: str) -> None:
        self._history = [h for h in self._history if h[1] != message_id]
        self._history.append((ts, message_id, text))
        self._history = [h for h in self._history if ts - h[0] <= self.window_sec]
        if message_id not in self._pending_ids:
            self._pending_ids.append(message_id)

    def has_pending(self) -> bool:
        return bool(self._pending_ids)

    def context(self, now: float) -> list[str]:
        return [t for ts, _, t in self._history if now - ts <= self.window_sec]

    def take_pending(self, now: float) -> tuple[list[str], list[int]]:
        pending = list(self._pending_ids)
        self._pending_ids.clear()
        return self.context(now), pending

    def reset(self) -> None:
        self._history.clear()
        self._pending_ids.clear()
