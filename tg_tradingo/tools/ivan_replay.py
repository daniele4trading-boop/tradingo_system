"""Replay dei messaggi IVAN dal journal del bridge attraverso l'interprete LLM.

Legge ``journal/bridge_events/events_YYYYMMDD.jsonl`` (eventi NEW del canale
IVAN), ricostruisce il setup corrente dai payload OPEN emessi, raggruppa i
messaggi di gestione come farebbe il bridge (debounce: un nuovo messaggio
entro ``--debounce`` secondi rinvia la valutazione; contesto = ultimi 5 min)
e confronta l'azione dell'interprete con quella del parser legacy.

Uso:
    python tools/ivan_replay.py --events DIR --from 2026-10-07 --to 2026-10-20 \
        --out replay.csv [--dry]

``--dry`` non chiama l'API: elenca soltanto i gruppi che verrebbero valutati.
Richiede ANTHROPIC_API_KEY nell'ambiente (mai su file).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ivan_interpreter import (
    InterpreterConfig,
    IvanInterpreter,
    IvanMessageBuffer,
    describe_signal,
    interpretation_to_signal,
    is_urgent,
)
from tradingo_bridge import ivan_is_setup_text, strip_md

CH = {"id": "CH_IVAN", "magic_base": 17000}


def _ts(row: dict) -> float:
    return datetime.strptime(row["ts_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    ).timestamp()


def load_events(events_dir: Path, d_from: str, d_to: str, channel: str) -> list[dict]:
    rows = []
    for f in sorted(events_dir.glob("events_*.jsonl")):
        day = f.stem.split("_")[1]
        if not (d_from.replace("-", "") <= day <= d_to.replace("-", "")):
            continue
        for line in f.open(encoding="utf-8"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("channel_id") == channel and r.get("event_type") == "NEW":
                rows.append(r)
    rows.sort(key=_ts)
    return rows


def replay(rows: list[dict], interp: IvanInterpreter | None, debounce: float) -> list[dict]:
    buf = IvanMessageBuffer()
    last_trade: dict | None = None
    out: list[dict] = []
    pending_legacy: list[str] = []

    def flush(now: float):
        nonlocal pending_legacy
        context, pending = buf.take_pending(now)
        if not pending:
            return
        res = interp.interpret(context, last_trade, buf.executed(now)) if interp else None
        sig = interpretation_to_signal(res, CH, last_trade, context[-1]) if res else None
        if sig:
            buf.mark_executed(now, describe_signal(sig))
        out.append({
            "ts_utc": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "n_msgs": len(pending),
            "messages": " || ".join(m.replace("\n", " ")[:120] for m in context[-len(pending):]),
            "legacy": " + ".join(a for a in pending_legacy if a) or "-",
            "llm_action": res.action if res else "",
            "llm_scope": (res.scope or res.keep or "") if res else "",
            "llm_price": res.price if res and res.price is not None else "",
            "llm_conf": f"{res.confidence:.2f}" if res else "",
            "llm_signal": (sig or {}).get("action", "-") if res else "",
            "llm_reason": res.reason if res else "",
        })
        pending_legacy = []

    prev_ts: float | None = None
    for r in rows:
        ts = _ts(r)
        text = r.get("raw_text") or ""
        if prev_ts is not None and buf.has_pending() and ts - prev_ts > debounce:
            flush(prev_ts + debounce)
        action = (r.get("payload") or {}).get("action") if r.get("outcome") == "EMITTED" else None
        if ivan_is_setup_text(strip_md(text).upper()):
            if action in ("OPEN", "UPDATE_OPEN") and r.get("payload"):
                p = r["payload"]
                buf.reset()
                last_trade = {
                    "symbol": p.get("symbol"), "direction": p.get("direction"),
                    "entry": p.get("entry"), "entry_range": p.get("entry_range"),
                    "sl": p.get("sl"), "tp_levels": p.get("tp_levels"),
                    "setup_entry": p.get("setup_entry") or p.get("entry"),
                    "allow_stack": p.get("allow_stack"),
                }
            prev_ts = ts
            continue
        buf.push(ts, int(r.get("message_id") or 0), text)
        pending_legacy.append(action or "")
        if is_urgent(text):
            flush(ts)
        prev_ts = ts
    if buf.has_pending() and prev_ts is not None:
        flush(prev_ts + debounce)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True, type=Path)
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--channel", default="CH_IVAN")
    ap.add_argument("--debounce", type=float, default=25.0)
    ap.add_argument("--model", default=None)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    rows = load_events(a.events, a.d_from, a.d_to, a.channel)
    cfg = InterpreterConfig(enabled=True)
    if a.model:
        cfg.model = a.model
    interp = None if a.dry else IvanInterpreter(cfg)
    result = replay(rows, interp, a.debounce)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(result[0].keys()) if result else ["ts_utc"])
        w.writeheader()
        w.writerows(result)
    acted = sum(1 for r in result if r["llm_signal"] not in ("", "-"))
    print(f"{len(rows)} messaggi NEW, {len(result)} valutazioni, {acted} con azione -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
