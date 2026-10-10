"""Estrae i livelli dalle analisi dei canali (Hybrid Setup Gold/Forex, Matteo Sertorio).

Regole costruite sui messaggi reali (vedi docs/LEVELS_INDICATOR.md). Solo disegno:
niente di quanto estratto qui arriva all'EA operativo.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from levels_store import (
    Level,
    from_csv,
    merge_levels,
    normalize_level,
    write_levels_file,
)

_NUM = r"(?:\d{1,3}(?:\.\d{3})+(?!\d)|\d+(?:[.,]\d+)?)"
_RANGE_RE = re.compile(rf"({_NUM})(?:\s*[-–]\s*({_NUM}))?")
_SEG_SPLIT = re.compile(r"\n|(?=🔴|🟢|🟡|🔵|🐂|🐻|🔑|📌|📈|🧭|⚠️|🌍|⏱️|⏰|⚡)")
_FX_RE = re.compile(r"\b((?:EUR|GBP|USD|JPY|CHF|CAD|AUD|NZD|MXN|XAU|XAG)/?(?:EUR|GBP|USD|JPY|CHF|CAD|AUD|NZD|MXN))\b")
SOURCE_BY_CHANNEL = {"CH_HYBRIDGOLD": "HYBRID_GOLD", "CH_HYBRIDFX": "HYBRID_FOREX", "CH_SERTORIO": "SERTORIO"}
MAX_PER_KIND = 8


def parse_price(tok: str, symbol: str) -> float | None:
    t = tok.strip()
    gold = symbol.startswith("XAU")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t) and (gold or t.count(".") > 1):
        t = t.replace(".", "")
    try:
        v = float(t.replace(",", "."))
    except ValueError:
        return None
    if gold and not 500 <= v <= 20000:
        return None
    if symbol.endswith("JPY") and not 50 <= v <= 400:
        return None
    if "MXN" in symbol and not 3 <= v <= 30:
        return None
    if not gold and not symbol.endswith("JPY") and "MXN" not in symbol and not 0.3 <= v <= 3:
        return None
    return v


def _ranges(seg: str, symbol: str) -> list[tuple[float, float | None]]:
    seg = re.sub(r"\([^)]*\)", " ", seg)
    seg = re.sub(r"\b(?:M|H|D)\d+\b|\d+\s*%|\d+\s*(?:PIPS|PUNTI|DOLLARI|\$)", " ", seg, flags=re.IGNORECASE)
    out = []
    for a, b in _RANGE_RE.findall(seg):
        pa = parse_price(a, symbol)
        pb = parse_price(b, symbol) if b else None
        if pa is None:
            continue
        if pb is not None and abs(pb - pa) / pa > 0.02:
            out += [(pa, None), (pb, None)]
        else:
            out.append((pa, pb))
    return out


def _lv(symbol, kind, rng, label=""):
    return {"symbol": symbol, "kind": kind, "price": rng[0], "price_to": rng[1], "label": label}


def extract_sertorio(text: str) -> list[dict]:
    up = text.upper()
    if not ("XAUUSD" in up or "ORO" in up) or not re.search(r"RESISTENZ|SUPPORT|LIVELL", up):
        return []
    sym, out, section = "XAUUSD", [], None
    body = re.split(r"DISCLAIMER|⚠️\s*(?:IL TRADING|ANALISI PURAMENTE)", text, flags=re.IGNORECASE)[0]
    for seg in _SEG_SPLIT.split(body):
        s = seg.strip()
        if not s:
            continue
        u = s.upper()
        if re.search(r"SCENARIO", u) or s.startswith(("🐂", "🐻")):
            section = None
            direction = "watch_buy" if re.search(r"LONG|RIALZ|BUY|🐂", u) else "watch_sell"
            m = re.search(rf"(?:ENTRATA|INGRESSO|IN AREA|RETEST(?:\s+DI)?)\D{{0,25}}?({_NUM}(?:\s*[-–]\s*{_NUM})?)", s, re.IGNORECASE)
            if m and re.search(r"\bSL\b|STOP\s*LOSS", u):
                rng = _ranges(m.group(1), sym)
                if rng:
                    pct = re.search(r"(\d{2})\s*%", s)
                    sl = re.search(rf"(?:SL|STOP\s*LOSS)\s*:?\s*({_NUM})", s, re.IGNORECASE)
                    tps = re.findall(rf"TP\d?\s*:?\s*({_NUM})", s, re.IGNORECASE)
                    lab = ("Long" if direction == "watch_buy" else "Short") + (f" {pct.group(1)}%" if pct else "")
                    if sl:
                        lab += f" SL {parse_price(sl.group(1), sym):g}"
                    if tps:
                        lab += " TP " + "/".join(f"{parse_price(t, sym):g}" for t in tps[:2] if parse_price(t, sym))
                    out.append(_lv(sym, direction, rng[0], lab))
            continue
        if re.search(r"LIVELLO\s+CHIAVE", u) and not re.search(r"LIVELLI\s+CHIAVE", u):
            rng = _ranges(s.split(":", 1)[-1], sym)
            if rng:
                out.append(_lv(sym, "key", rng[0], "livello chiave"))
            section = None
            continue
        if "RESISTENZ" in u or (s.startswith("🔴") and section != "support_hdr"):
            kind = "resistance"
        elif "SUPPORT" in u or s.startswith("🟢"):
            kind = "support"
        else:
            continue
        section = kind
        for rng in _ranges(re.sub(r"(?i)resistenz\w*|support\w*", " ", s), sym):
            out.append(_lv(sym, kind, rng))
    return _cap(out)


def _cap(levels: list[dict]) -> list[dict]:
    mids = {}
    for sym in {lv["symbol"] for lv in levels}:
        px = sorted(lv["price"] for lv in levels if lv["symbol"] == sym)
        mids[sym] = px[len(px) // 2]
    levels = [lv for lv in levels if abs(lv["price"] - mids[lv["symbol"]]) / mids[lv["symbol"]] <= 0.08]
    seen, out = {}, []
    for lv in levels:
        k = (lv["symbol"], lv["kind"], lv["price"])
        if k in seen or sum(1 for x in out if x["kind"] == lv["kind"] and x["symbol"] == lv["symbol"]) >= MAX_PER_KIND:
            continue
        seen[k] = 1
        out.append(lv)
    return out


_HY_DIR = r"(BUY|LONG|SELL|SHORT)"


def extract_hybrid(text: str, default_symbol: str | None) -> tuple[list[dict], bool]:
    """(livelli in osservazione, azzera_tutto). Ignora i segnali operativi (hanno SL)."""
    up = text.upper()
    if re.search(r"\bSL\b|STOP\s*LOSS\s*:", up) and re.search(rf"\b{_HY_DIR}\s+[A-Z]{{6}}", up):
        return [], False
    if re.search(r"\bRECAP\b", up):
        return [], False
    clear = bool(re.search(r"NON ABBIAMO (?:LIVELLI|ALCUN LIVELLO)", up))
    out = []
    head = _FX_RE.search(up)
    for line in text.split("\n"):
        u = line.upper()
        sym_m = _FX_RE.search(u) or (head if not re.search(r"GOLD|ORO|XAU", u) else None)
        sym = sym_m.group(1).replace("/", "") if sym_m else None
        if not sym and re.search(r"GOLD|\bORO\b|XAU", u):
            sym = "XAUUSD"
        sym = sym or default_symbol
        if not sym:
            continue
        n_before = len(out)
        for m in re.finditer(rf"\b{_HY_DIR}\b(?:\s+(?:A|IN AREA|ZONA))?\s+({_NUM}(?:\s*[-–]\s*{_NUM})?)", u):
            rng = _ranges(m.group(2), sym)
            if rng:
                kind = "watch_buy" if m.group(1) in ("BUY", "LONG") else "watch_sell"
                out.append(_lv(sym, kind, rng[0], "BUY in osservazione" if kind == "watch_buy" else "SELL in osservazione"))
        m = re.match(rf"^\W*([A-Z]{{6}})\s*:\s*({_NUM}(?:\s*[-–]\s*{_NUM})?)\s*$", u.strip())
        if m and len(out) == n_before:
            rng = _ranges(m.group(2), m.group(1))
            if rng:
                out.append(_lv(m.group(1), "watch", rng[0], "in osservazione"))
    return _cap(out), clear


def _end_of_session(now: datetime) -> datetime:
    eod = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return eod if eod > now + timedelta(hours=2) else eod + timedelta(days=1)


def update_levels(path: Path, channel_id: str, text: str, now: datetime | None = None,
                  default_symbol: str | None = None) -> int:
    """Aggiorna il CSV dei livelli per un messaggio del canale. Ritorna il numero di livelli nuovi."""
    source = SOURCE_BY_CHANNEL.get(channel_id)
    if not source:
        return 0
    now = now or datetime.now(UTC)
    current = from_csv(path.read_text(encoding="utf-8")) if path.exists() else []
    if source == "SERTORIO":
        raw, clear, ttl = extract_sertorio(text), False, 36.0
    else:
        raw, clear = extract_hybrid(text, default_symbol)
        ttl = (_end_of_session(now) - now).total_seconds() / 3600
        signal = re.search(r"\b(?:BUY|SELL)\s+([A-Z]{6})", text.upper())
        if signal and re.search(r"\bSL\b", text.upper()):
            sym = signal.group(1)
            current = [lv for lv in current if not (lv.source == source and lv.symbol == sym)]
            write_levels_file(path, current)
            return 0
    if clear and not raw:
        current = [lv for lv in current if lv.source != source]
        write_levels_file(path, current)
        return 0
    new = [lv for lv in (normalize_level(r, source, ttl, now) for r in raw) if lv]
    if not new:
        return 0
    write_levels_file(path, merge_levels(current, new, source, now))
    return len(new)


__all__ = ["Level", "extract_hybrid", "extract_sertorio", "parse_price", "update_levels"]
