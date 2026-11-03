"""Analisi Sertorio -> zone strutturate -> setup condizionali (shadow/paper).

Flusso:
  testo analisi  --SertorioInterpreter-->  Analysis (zone S/R, bias, livello chiave, idee LONG/SHORT)
                 --build_setups-->          Setup "a tocco" (entry range, SL, TP, scadenza)
                 --ShadowBook-->            fill/SL/TP virtuali sui prezzi (nessun ordine reale)

L'interprete usa il modello (Anthropic, stesso client di ivan_interpreter) quando
configurato e ripiega su un parser deterministico (le analisi hanno un formato
abbastanza stabile: sezioni Resistenze/Supporti, "Idea LONG/SHORT" con
Ingresso/SL/TP, "Livello chiave"). Il canale non emette mai segnali reali finché
`shadow` è true: le zone e gli esiti servono a misurare la strategia prima di
metterci soldi.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ivan_interpreter import InterpreterConfig, _extract_json, anthropic_complete

log = logging.getLogger("tradingo.sertorio")

TAG = "SZ"
DEFAULT_SYMBOL = "XAUUSD"
PIP = 0.1  # XAUUSD: 1 pip = 0.1 $

_NUM = r"\d{1,2}[.,]?\d{3}(?:[.,]\d+)?"


def _num(tok: str) -> float | None:
    t = tok.strip().replace(" ", "")
    if re.fullmatch(r"\d{1,2}\.\d{3}", t):  # 4.190 -> 4190
        t = t.replace(".", "")
    t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    return v if 500 < v < 20000 else None


def _range(seg: str) -> tuple[float, float] | None:
    nums = [n for n in (_num(x) for x in re.findall(_NUM, seg)) if n is not None]
    if not nums:
        return None
    lo, hi = min(nums[:2]), max(nums[:2])
    return (lo, hi)


# ── modello dati ───────────────────────────────────────────────────────────

@dataclass
class Zone:
    kind: str            # support | resistance
    lo: float
    hi: float
    note: str = ""
    key: bool = False

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0


@dataclass
class Idea:
    direction: str       # BUY | SELL
    entry_lo: float
    entry_hi: float
    sl: float
    tps: list[float]
    condition: str = ""
    prob: int | None = None

    def valid(self) -> bool:
        if self.direction not in ("BUY", "SELL") or not self.tps or self.sl <= 0:
            return False
        if self.direction == "BUY":
            return self.sl < self.entry_lo and all(tp > self.entry_hi for tp in self.tps)
        return self.sl > self.entry_hi and all(tp < self.entry_lo for tp in self.tps)


@dataclass
class Analysis:
    symbol: str = DEFAULT_SYMBOL
    date: str = ""                 # YYYY-MM-DD (UTC) della pubblicazione
    bias_long_pct: int | None = None
    key_level: float | None = None
    current_price: float | None = None
    zones: list[Zone] = field(default_factory=list)
    ideas: list[Idea] = field(default_factory=list)
    summary: str = ""
    source: str = "regex"          # regex | llm

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Analysis:
        a = cls(**{k: v for k, v in d.items() if k not in ("zones", "ideas")})
        a.zones = [Zone(**z) for z in d.get("zones", [])]
        a.ideas = [Idea(**i) for i in d.get("ideas", [])]
        return a

    def supports(self) -> list[Zone]:
        return sorted((z for z in self.zones if z.kind == "support"), key=lambda z: -z.hi)

    def resistances(self) -> list[Zone]:
        return sorted((z for z in self.zones if z.kind == "resistance"), key=lambda z: z.lo)


def is_analysis_text(text: str) -> bool:
    up = text.upper()
    if len(text) < 300:
        return False
    return ("XAUUSD" in up or "ORO" in up or "GOLD" in up) and bool(
        re.search(r"RESISTENZ|SUPPORT", up)) and bool(re.search(r"ANALISI|AGGIORNAMENTO|SCENARIO|IDEA", up))


# ── parser deterministico ─────────────────────────────────────────────────

_SPLIT = re.compile(r"\n+")
_IDEA_HDR = re.compile(r"(IDEA|SCENARIO)\s+(LONG|SHORT|RIALZ\w*|RIBASS\w*)", re.IGNORECASE)
_ENTRY = re.compile(rf"(?:INGRESSO|ENTRATA|IN AREA|RETEST(?:\s+DI)?)\D{{0,30}}?({_NUM}(?:\s*[-–/]\s*{_NUM})?)", re.IGNORECASE)
_SL = re.compile(rf"(?:\bSL\b|STOP\s*LOSS)\s*:?\s*({_NUM})", re.IGNORECASE)
_TP = re.compile(rf"TP\s*\d?\s*:?\s*({_NUM})", re.IGNORECASE)
_PROB = re.compile(r"(\d{2})\s*%")


def parse_analysis_regex(text: str, now: datetime | None = None) -> Analysis:
    now = now or datetime.now(timezone.utc)
    a = Analysis(date=now.strftime("%Y-%m-%d"))
    body = re.split(r"DISCLAIMER|⚠️\s*(?:QUESTA ANALISI|IL TRADING|ANALISI PURAMENTE)", text, flags=re.IGNORECASE)[0]
    lines = [ln.strip() for ln in _SPLIT.split(body) if ln.strip()]
    section: str | None = None
    idea: dict | None = None
    idea_text: list[str] = []
    long_pct = short_pct = None

    def flush_idea():
        nonlocal idea
        if idea and idea.get("entry") and idea.get("sl") and idea.get("tps"):
            lo, hi = idea["entry"]
            a.ideas.append(Idea(idea["dir"], lo, hi, idea["sl"], idea["tps"],
                                condition=" ".join(idea_text)[:200], prob=idea.get("prob")))
        idea = None
        idea_text.clear()

    for ln in lines:
        up = ln.upper()
        m = re.search(rf"PREZZO ATTUALE\D{{0,10}}({_NUM})", ln, re.IGNORECASE)
        if m and a.current_price is None:
            a.current_price = _num(m.group(1))
        if re.match(r"^\W*LIVELLO\s+CHIAVE", up):
            flush_idea()
            section = None
            r = _range(ln.split(":", 1)[-1] if ":" in ln else re.sub(r"(?i)livello\s+chiave\D*", " ", ln))
            if r and a.key_level is None:
                a.key_level = r[0]
            continue
        hm = _IDEA_HDR.search(ln)
        if hm:
            flush_idea()
            section = "idea"
            d = hm.group(2).upper()
            direction = "BUY" if d.startswith(("LONG", "RIALZ")) else "SELL"
            pm = _PROB.search(ln)
            prob = int(pm.group(1)) if pm else None
            if prob is not None:
                if direction == "BUY":
                    long_pct = prob
                else:
                    short_pct = prob
            idea = {"dir": direction, "entry": None, "sl": None, "tps": [], "prob": prob}
            # setup sulla stessa riga (formato compatto)
            em, sm = _ENTRY.search(ln), _SL.search(ln)
            if em:
                idea["entry"] = _range(em.group(1))
            if sm:
                idea["sl"] = _num(sm.group(1))
            idea["tps"] = [t for t in (_num(x) for x in _TP.findall(ln)) if t]
            continue
        if re.search(r"^\W*(LONG|SHORT)\s*:?\s*\d{2}\s*%", up):
            pm = _PROB.search(ln)
            if pm:
                if "LONG" in up:
                    long_pct = int(pm.group(1))
                else:
                    short_pct = int(pm.group(1))
            continue
        if section == "idea" and idea is not None:
            em = _ENTRY.search(ln)
            if em and not idea["entry"]:
                idea["entry"] = _range(em.group(1))
                idea_text.append(ln)
                continue
            sm = _SL.search(ln)
            if sm and not idea["sl"]:
                idea["sl"] = _num(sm.group(1))
                continue
            tps = [t for t in (_num(x) for x in _TP.findall(ln)) if t]
            if tps:
                idea["tps"].extend(tps)
                continue
            if re.search(r"PERCH[EÉ]|SOLO CON|CONFERMA|RIFIUTO", up):
                idea_text.append(ln)
            if re.search(r"RESISTENZ|SUPPORT", up) and not _ENTRY.search(ln):
                section = None  # torna alle sezioni livelli
            else:
                continue
        if "RESISTENZ" in up and not re.search(r"\d", up.split("RESISTENZ")[0][-15:]):
            flush_idea()
            section = "resistance"
            rest = re.sub(r"(?i)resistenz\w*", " ", ln)
            r = _range(rest)
            if r:
                a.zones.append(Zone("resistance", *r, note=_note(ln), key="CHIAVE" in up))
            continue
        if "SUPPORT" in up and not re.search(r"\d", up.split("SUPPORT")[0][-15:]):
            flush_idea()
            section = "support"
            rest = re.sub(r"(?i)support\w*", " ", ln)
            r = _range(rest)
            if r:
                a.zones.append(Zone("support", *r, note=_note(ln), key="CHIAVE" in up))
            continue
        if section in ("support", "resistance"):
            if re.match(r"^\W*\d", ln) or ln[:1] in "🔴🟢":
                for part in re.split(r"\s+E\s+POI\s+|\s+e\s+poi\s+", ln):
                    r = _range(part)
                    if r:
                        a.zones.append(Zone(section, *r, note=_note(part), key="CHIAVE" in part.upper()))
                continue
            section = None
    flush_idea()
    if long_pct is not None or short_pct is not None:
        a.bias_long_pct = long_pct if long_pct is not None else 100 - short_pct
    a.zones = _dedupe_zones(a.zones, a.current_price)
    a.ideas = [i for i in a.ideas if i.valid()]
    return a


def _note(ln: str) -> str:
    m = re.search(r"\(([^)]{2,60})\)", ln)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:→|->|–|-)\s*([^\d(]{3,50})$", ln)
    return m.group(1).strip() if m else ""


def _dedupe_zones(zones: list[Zone], ref: float | None) -> list[Zone]:
    if not zones:
        return []
    mid = ref or sorted(z.mid for z in zones)[len(zones) // 2]
    out, seen = [], set()
    for z in zones:
        if abs(z.mid - mid) / mid > 0.08:
            continue
        k = (z.kind, round(z.lo, 1), round(z.hi, 1))
        if k in seen:
            continue
        seen.add(k)
        out.append(z)
    return out


# ── interprete LLM ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sei l'assistente di un sistema di trading automatico. Ricevi l'analisi giornaliera
in italiano di un analista su XAUUSD (oro). Devi trasformarla in dati strutturati, senza inventare
nulla: usa solo i numeri presenti nel testo.

Rispondi SOLO con un JSON (niente testo fuori dal JSON) con questo schema:
{
 "symbol": "XAUUSD",
 "current_price": numero o null,
 "bias_long_pct": intero 0-100 (probabilità scenario rialzista) o null,
 "key_level": numero o null (il "livello chiave della giornata"),
 "zones": [ {"kind": "support"|"resistance", "lo": numero, "hi": numero, "note": "breve", "key": true|false} ],
 "ideas": [ {"direction": "BUY"|"SELL", "entry_lo": numero, "entry_hi": numero, "sl": numero,
             "tps": [numeri], "condition": "breve condizione di ingresso", "prob": intero o null} ],
 "summary": "una frase sul quadro della giornata"
}
Regole: una zona con un solo prezzo ha lo == hi. Le "ideas" sono solo i setup espliciti
(Idea LONG/SHORT, Scenario con ingresso+SL+TP). Prezzi con il punto delle migliaia (4.190) vanno
scritti come 4190. Se un dato manca usa null o lista vuota."""


Completer = Callable[[str, str, InterpreterConfig], str]


class SertorioInterpreter:
    """LLM con fallback deterministico; mai solleva verso il bridge."""

    def __init__(self, cfg: InterpreterConfig, completer: Completer | None = None):
        self.cfg = cfg
        self._complete = completer or anthropic_complete

    def interpret(self, text: str, now: datetime | None = None) -> Analysis:
        now = now or datetime.now(timezone.utc)
        base = parse_analysis_regex(text, now)
        if not self.cfg.enabled:
            return base
        try:
            raw = self._complete(SYSTEM_PROMPT, text, self.cfg)
            data = _extract_json(raw)
            llm = analysis_from_model_json(data, now)
        except Exception as e:  # noqa: BLE001 - rete/JSON: si usa il parser
            log.warning(f"[{TAG}_LLM] errore interprete, uso parser: {e}")
            return base
        return merge_analysis(llm, base)


def analysis_from_model_json(data: dict, now: datetime) -> Analysis:
    a = Analysis(date=now.strftime("%Y-%m-%d"), source="llm")
    a.symbol = str(data.get("symbol") or DEFAULT_SYMBOL).upper()
    a.current_price = _opt_float(data.get("current_price"))
    a.key_level = _opt_float(data.get("key_level"))
    b = data.get("bias_long_pct")
    a.bias_long_pct = int(b) if isinstance(b, (int, float)) and 0 <= b <= 100 else None
    a.summary = str(data.get("summary") or "")[:200]
    for z in data.get("zones") or []:
        kind = str(z.get("kind") or "").lower()
        lo, hi = _opt_float(z.get("lo")), _opt_float(z.get("hi"))
        if kind not in ("support", "resistance") or lo is None:
            continue
        hi = hi if hi is not None else lo
        a.zones.append(Zone(kind, min(lo, hi), max(lo, hi), str(z.get("note") or "")[:60], bool(z.get("key"))))
    for i in data.get("ideas") or []:
        d = str(i.get("direction") or "").upper()
        d = "BUY" if d in ("BUY", "LONG") else "SELL" if d in ("SELL", "SHORT") else ""
        lo, hi, sl = _opt_float(i.get("entry_lo")), _opt_float(i.get("entry_hi")), _opt_float(i.get("sl"))
        tps = [t for t in (_opt_float(x) for x in (i.get("tps") or [])) if t]
        if not d or lo is None or sl is None or not tps:
            continue
        hi = hi if hi is not None else lo
        p = i.get("prob")
        idea = Idea(d, min(lo, hi), max(lo, hi), sl, tps, str(i.get("condition") or "")[:200],
                    int(p) if isinstance(p, (int, float)) else None)
        if idea.valid():
            a.ideas.append(idea)
    a.zones = _dedupe_zones(a.zones, a.current_price)
    return a


def merge_analysis(llm: Analysis, base: Analysis) -> Analysis:
    """Il modello comanda; il parser riempie i buchi (zone/idee che il modello ha saltato)."""
    out = llm
    if not out.zones:
        out.zones = base.zones
    if not out.ideas:
        out.ideas = base.ideas
    if out.key_level is None:
        out.key_level = base.key_level
    if out.bias_long_pct is None:
        out.bias_long_pct = base.bias_long_pct
    if out.current_price is None:
        out.current_price = base.current_price
    return out


def _opt_float(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        return _num(v)
    return None


# ── setup condizionali ────────────────────────────────────────────────────

@dataclass
class SetupConfig:
    shadow: bool = True
    use_ideas: bool = True
    derive_from_zones: bool = True
    min_bias_pct: int = 45        # bias minimo nella direzione per i setup derivati dalle zone
    sl_buffer: float = 5.0        # $ oltre la zona (setup derivati)
    max_tps: int = 3
    expire_utc_hour: int = 21     # fine giornata: scadenza dei setup non ancora toccati
    touch_tolerance: float = 0.0  # $ di anticipo sul bordo della zona
    max_open_hours: int = 48      # chiusura forzata gambe aperte

    @classmethod
    def from_channel(cls, ch: dict) -> SetupConfig:
        raw = ch.get("setups") or {}
        cfg = cls()
        for k in cfg.__dataclass_fields__:
            if k in raw and raw[k] is not None:
                setattr(cfg, k, raw[k])
        return cfg


@dataclass
class Setup:
    id: str
    symbol: str
    direction: str
    entry_lo: float
    entry_hi: float
    sl: float
    tps: list[float]
    expires_utc: str
    source: str            # idea | zone
    analysis_ts: str
    condition: str = ""
    prob: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def build_setups(a: Analysis, cfg: SetupConfig, ts: datetime, analysis_id: str) -> list[Setup]:
    exp = ts.replace(hour=cfg.expire_utc_hour, minute=0, second=0, microsecond=0)
    if exp <= ts:
        exp += timedelta(days=1)
    exp_s = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[Setup] = []
    if cfg.use_ideas:
        for n, i in enumerate(a.ideas, 1):
            out.append(Setup(f"{analysis_id}-I{n}", a.symbol, i.direction, i.entry_lo, i.entry_hi, i.sl,
                             i.tps[:cfg.max_tps], exp_s, "idea", analysis_id, i.condition, i.prob))
    if cfg.derive_from_zones:
        have = {s.direction for s in out}
        bias = a.bias_long_pct
        ref = a.current_price
        if "BUY" not in have and (bias is None or bias >= cfg.min_bias_pct):
            sups = [z for z in a.supports() if ref is None or z.hi < ref]
            if sups:
                z = sups[0]
                tps = [r.lo for r in a.resistances() if r.lo > z.hi + cfg.sl_buffer][:cfg.max_tps]
                if tps:
                    out.append(Setup(f"{analysis_id}-Z1", a.symbol, "BUY", z.lo, z.hi, round(z.lo - cfg.sl_buffer, 2),
                                     tps, exp_s, "zone", analysis_id, f"supporto {z.note}".strip(), bias))
        if "SELL" not in have and (bias is None or (100 - bias) >= cfg.min_bias_pct):
            ress = [z for z in a.resistances() if ref is None or z.lo > ref]
            if ress:
                z = ress[0]
                tps = [s.hi for s in a.supports() if s.hi < z.lo - cfg.sl_buffer][:cfg.max_tps]
                if tps:
                    out.append(Setup(f"{analysis_id}-Z2", a.symbol, "SELL", z.lo, z.hi, round(z.hi + cfg.sl_buffer, 2),
                                     tps, exp_s, "zone", analysis_id, f"resistenza {z.note}".strip(),
                                     (100 - bias) if bias is not None else None))
    return out


# ── paper engine ───────────────────────────────────────────────────────────

SHADOW_HEADER = ["event", "ts_utc", "setup_id", "symbol", "direction", "tp_index", "entry_lo", "entry_hi",
                 "fill", "sl", "tp", "close", "reason", "pips", "source"]


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class ShadowBook:
    """Setup pendenti -> gambe virtuali (una per TP) -> chiusure; tutto in memoria + JSON/CSV."""

    def __init__(self, cfg: SetupConfig, state_path: Path | None = None, csv_path: Path | None = None):
        self.cfg = cfg
        self.state_path = state_path
        self.csv_path = csv_path
        self.pending: list[dict] = []
        self.open: list[dict] = []
        self.closed: list[dict] = []
        self.events: list[dict] = []
        if state_path and state_path.exists():
            try:
                d = json.loads(state_path.read_text(encoding="utf-8"))
                self.pending, self.open = d.get("pending", []), d.get("open", [])
                self.closed = d.get("closed", [])[-500:]
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{TAG}] stato shadow illeggibile: {e}")

    # ── setup ──
    def replace_setups(self, setups: list[Setup], ts: datetime, reason: str = "new_analysis") -> None:
        for p in self.pending:
            self._event("CANCEL", ts, p, reason=reason)
        self.pending = [s.to_dict() for s in setups]
        for p in self.pending:
            self._event("WATCH", ts, p)
        self.save()

    # ── prezzi ──
    def on_bar(self, ts: datetime, high: float, low: float, close: float) -> None:
        """Barra (M1 nel replay). Ordine prudente: prima SL, poi TP."""
        self._expire(ts, close)
        for p in list(self.pending):
            if p["direction"] == "BUY":
                invalid = low <= p["sl"]
                touched = low <= p["entry_hi"] + self.cfg.touch_tolerance
                fill = min(p["entry_hi"], max(close, p["entry_lo"])) if touched else None
            else:
                invalid = high >= p["sl"]
                touched = high >= p["entry_lo"] - self.cfg.touch_tolerance
                fill = max(p["entry_lo"], min(close, p["entry_hi"])) if touched else None
            if touched and invalid:
                self.pending.remove(p)
                self._event("CANCEL", ts, p, reason="sl_in_same_bar")
                continue
            if invalid:
                self.pending.remove(p)
                self._event("CANCEL", ts, p, reason="price_beyond_sl")
                continue
            if touched:
                self.pending.remove(p)
                self._fill(ts, p, fill)
        for leg in list(self.open):
            buy = leg["direction"] == "BUY"
            hit_sl = low <= leg["sl"] if buy else high >= leg["sl"]
            hit_tp = high >= leg["tp"] if buy else low <= leg["tp"]
            if hit_sl:
                reason = "BE" if abs(leg["sl"] - leg["fill"]) < 1e-9 else "SL"
                self._close(ts, leg, leg["sl"], reason)
            elif hit_tp:
                self._close(ts, leg, leg["tp"], "TP")
                self._be_siblings(ts, leg)
            elif ts - _parse_ts(leg["open_ts"]) > timedelta(hours=self.cfg.max_open_hours):
                self._close(ts, leg, close, "TIMEOUT")
        self.save()

    def on_tick(self, ts: datetime, bid: float, ask: float) -> None:
        mid = (bid + ask) / 2.0
        self.on_bar(ts, mid, mid, mid)

    # ── interno ──
    def _expire(self, ts: datetime, close: float) -> None:
        for p in list(self.pending):
            if ts >= _parse_ts(p["expires_utc"]):
                self.pending.remove(p)
                self._event("EXPIRE", ts, p, reason="end_of_day")

    def _fill(self, ts: datetime, p: dict, fill: float) -> None:
        for n, tp in enumerate(p["tps"], 1):
            leg = {**{k: p[k] for k in ("id", "symbol", "direction", "entry_lo", "entry_hi", "sl", "source")},
                   "setup_id": p["id"], "tp_index": n, "tp": tp, "fill": fill, "open_ts": _ts(ts)}
            self.open.append(leg)
            self._event("FILL", ts, leg)

    def _be_siblings(self, ts: datetime, done: dict) -> None:
        for leg in self.open:
            if leg["setup_id"] == done["setup_id"] and leg is not done and leg["sl"] != leg["fill"]:
                buy = leg["direction"] == "BUY"
                if (buy and leg["fill"] > leg["sl"]) or (not buy and leg["fill"] < leg["sl"]):
                    leg["sl"] = leg["fill"]
                    self._event("BE", ts, leg)

    def _close(self, ts: datetime, leg: dict, price: float, reason: str) -> None:
        self.open.remove(leg)
        sign = 1 if leg["direction"] == "BUY" else -1
        pips = round(sign * (price - leg["fill"]) / PIP, 1)
        rec = {**leg, "close": price, "close_ts": _ts(ts), "reason": reason, "pips": pips}
        self.closed.append(rec)
        self.closed = self.closed[-500:]
        self._event("CLOSE", ts, rec, reason=reason, pips=pips)

    def _event(self, kind: str, ts: datetime, obj: dict, reason: str = "", pips: float | None = None) -> None:
        row = {
            "event": kind, "ts_utc": _ts(ts), "setup_id": obj.get("setup_id") or obj.get("id"),
            "symbol": obj.get("symbol"), "direction": obj.get("direction"), "tp_index": obj.get("tp_index", ""),
            "entry_lo": obj.get("entry_lo"), "entry_hi": obj.get("entry_hi"), "fill": obj.get("fill", ""),
            "sl": obj.get("sl"), "tp": obj.get("tp", ""), "close": obj.get("close", ""),
            "reason": reason, "pips": "" if pips is None else pips, "source": obj.get("source", ""),
        }
        self.events.append(row)
        log.info(f"[{TAG} shadow] {kind} {row['setup_id']} {row['direction']} tp={row['tp_index']} "
                 f"fill={row['fill']} close={row['close']} {reason} {'' if pips is None else f'{pips:+.1f} pips'}")
        if self.csv_path:
            try:
                new = not self.csv_path.exists()
                self.csv_path.parent.mkdir(parents=True, exist_ok=True)
                with self.csv_path.open("a", encoding="utf-8", newline="") as fh:
                    w = csv.DictWriter(fh, fieldnames=SHADOW_HEADER)
                    if new:
                        w.writeheader()
                    w.writerow(row)
            except Exception as e:  # noqa: BLE001
                log.warning(f"[{TAG}] scrittura CSV shadow fallita: {e}")

    def save(self) -> None:
        if not self.state_path:
            return
        atomic_write_json(self.state_path, {**getattr(self, "extra", {}), "pending": self.pending, "open": self.open,
                                            "closed": self.closed[-500:], "stats_7d": self.stats(7),
                                            "stats_30d": self.stats(30),
                                            "updated_utc": _ts(datetime.now(timezone.utc))})

    # ── statistiche ──
    def stats(self, days: int, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        rows = [c for c in self.closed if _parse_ts(c["close_ts"]) >= since]
        wins = [c for c in rows if c["pips"] > 0]
        loss = [c for c in rows if c["pips"] < 0]
        return {"legs": len(rows), "pips": round(sum(c["pips"] for c in rows), 1), "win": len(wins),
                "loss": len(loss), "be": len(rows) - len(wins) - len(loss),
                "setups": len({c["setup_id"] for c in rows})}


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sz_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def shadow_rows_from_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


# ── integrazione bridge ───────────────────────────────────────────────────

ZONES_FILE = "sertorio_zones.json"
SHADOW_STATE_FILE = "sertorio_shadow.json"
SHADOW_CSV_FILE = "sertorio_shadow.csv"


def handle_analysis(ch_cfg: dict, text: str, state_dir: Path, now: datetime | None = None,
                    completer: Completer | None = None) -> dict | None:
    """Analisi -> interprete -> setup -> state/sertorio_zones.json (letto da shadow e webapp).

    Ritorna il documento scritto, None se il testo non è un'analisi. Nessun segnale
    reale viene emesso: in shadow il monitoraggio dei tocchi è di tools/sertorio_shadow.py.
    """
    if not is_analysis_text(text):
        return None
    now = now or datetime.now(timezone.utc)
    icfg = InterpreterConfig.from_channel(ch_cfg)
    icfg.max_tokens = max(icfg.max_tokens, 1500)
    scfg = SetupConfig.from_channel(ch_cfg)
    a = SertorioInterpreter(icfg, completer).interpret(text, now)
    if not a.zones and not a.ideas:
        log.info(f"[{TAG}] analisi senza zone né idee: ignorata ({text[:50]!r})")
        return None
    aid = now.strftime("SZ%Y%m%d%H%M")
    setups = build_setups(a, scfg, now, aid)
    doc = {
        "channel_id": ch_cfg.get("id"), "analysis_id": aid, "ts_utc": _ts(now), "shadow": bool(scfg.shadow),
        "analysis": a.to_dict(), "setups": [s.to_dict() for s in setups], "text": text[:4000],
    }
    atomic_write_json(state_dir / ZONES_FILE, doc)
    log.info(f"[{TAG}] analisi {aid} ({a.source}): {len(a.zones)} zone, {len(a.ideas)} idee, "
             f"{len(setups)} setup {'SHADOW' if scfg.shadow else 'LIVE'} bias_long={a.bias_long_pct} "
             f"key={a.key_level} -> " + "; ".join(
                 f"{s.direction} {s.entry_lo:g}-{s.entry_hi:g} SL {s.sl:g} TP {'/'.join(f'{t:g}' for t in s.tps)}"
                 for s in setups))
    if not scfg.shadow:
        log.warning(f"[{TAG}] modalità LIVE non ancora supportata: i setup restano in shadow")
    return doc
