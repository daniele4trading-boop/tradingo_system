"""
TG TradinGo — campiona messaggi Telegram delle ultime N ore.

Usa i canali da tradingo_config.json e, opzionalmente, segnala altri
canali/gruppi con attività recente non ancora configurati.

Output: C:\\TG_TradinGo\\logs\\signal_last_24h.txt

Uso:
  python sample_last_24h.py
  python sample_last_24h.py --hours 48
  python sample_last_24h.py --parser-dry-run
  python sample_last_24h.py --include-unknown
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

CONFIG_FILE = os.environ.get(
    "TRADINGO_CONFIG",
    os.path.join(os.path.dirname(__file__), "tradingo_config.json"),
)


def configure_stdio() -> None:
    """Avoid UnicodeEncodeError on Windows console (emoji in Telegram messages)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def load_config() -> dict:
    path = CONFIG_FILE
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(__file__), "tradingo_config.example.json")
        if os.path.exists(example):
            path = example
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def channel_full_id(entity) -> int | None:
    if isinstance(entity, Channel):
        return -(1_000_000_000_000 + entity.id)
    if isinstance(entity, Chat):
        return -entity.id
    return None


def import_parsers():
    """Import parser functions from bridge (no Telegram client startup)."""
    sys.path.insert(0, os.path.dirname(__file__))
    from bridge_core import EphemeralBridgeState, resolve_telegram_credentials
    from tradingo_bridge import (
        parser_sala_gold,
        parser_sala_stark,
        parser_sala_vip,
        parser_zanni_vip,
    )

    dry_state = EphemeralBridgeState()
    return {
        "zanni_vip": parser_zanni_vip,
        "sala_gold": lambda text, ch: parser_sala_gold(text, ch, dry_state),
        "sala_vip": lambda text, ch: parser_sala_vip(text, ch, dry_state),
        "sala_stark": parser_sala_stark,
    }


def format_signal(sig: dict | None) -> str:
    if not sig:
        return "IGNORED"
    parts = [sig.get("action", "?")]
    if sig.get("direction"):
        parts.append(sig["direction"])
    if sig.get("symbol"):
        parts.append(sig["symbol"])
    if sig.get("tp_index"):
        parts.append(f"TP{sig['tp_index']}")
    return " | ".join(parts)


async def fetch_messages_since(client, entity, since: datetime):
    msgs = []
    async for m in client.iter_messages(entity, offset_date=since, reverse=True):
        if m.text and m.text.strip():
            msgs.append(m)
    return msgs


def write_output(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    # Canonical name for push script / cloud agent
    canonical = path.parent / "signal_last_24h.txt"
    if path.name != canonical.name:
        canonical.write_text("\n".join(lines), encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    config = load_config()
    tg = resolve_telegram_credentials(config["telegram"])
    log_dir = Path(config["paths"]["logs"])
    log_dir.mkdir(parents=True, exist_ok=True)
    out_file = log_dir / f"signal_last_{args.hours}h.txt"

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    configured = [ch for ch in config.get("channels", []) if ch.get("enabled", True)]
    known_ids = {int(ch["telegram_id"]): ch for ch in configured}
    parsers = import_parsers() if args.parser_dry_run else {}

    lines: list[str] = []

    def log(s: str = "") -> None:
        lines.append(s)
        safe_print(s)

    client = TelegramClient(tg["session_file"], tg["api_id"], tg["api_hash"])

    try:
        async with client:
            log("=" * 72)
            log(f"TG TradinGo — ultimi {args.hours} ore")
            log(f"Generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            log(f"Cutoff UTC: {since.strftime('%Y-%m-%d %H:%M:%S')}")
            log("=" * 72)

            log("\n## CANALI CONFIGURATI\n")

            for ch in configured:
                cid = int(ch["telegram_id"])
                log("-" * 72)
                log(f"{ch['id']} | {ch.get('name', '?')} | parser={ch.get('parser')}")
                log(f"telegram_id: {cid}")
                log(f"signal_file: {ch.get('signal_file')} | magic_base: {ch.get('magic_base')}")
                log("")

                parser_fn = parsers.get(ch.get("parser", ""))
                try:
                    entity = await client.get_entity(cid)
                    msgs = await fetch_messages_since(client, entity, since)
                except Exception as exc:
                    log(f"  ERRORE: {exc}\n")
                    continue

                if not msgs:
                    log("  (nessun messaggio con testo nelle ultime ore)\n")
                    continue

                for i, m in enumerate(msgs, 1):
                    ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    log(f"[{i:02d}] {ts} | msg_id={m.id}")
                    if args.parser_dry_run and parser_fn:
                        try:
                            sig = parser_fn(m.text, ch)
                            log(f"     -> {format_signal(sig)}")
                        except Exception as exc:
                            log(f"     -> PARSER_ERROR: {exc}")
                    for row in m.text.splitlines():
                        log(f"     {row}")
                    log()

                log(f"  Totale: {len(msgs)} messaggi\n")

            if args.include_unknown:
                log("\n## ALTRI CANALI/GRUPPI CON ATTIVITÀ RECENTE (non in config)\n")
                dialogs = await client.get_dialogs()

                for d in dialogs:
                    entity = d.entity
                    if not isinstance(entity, (Channel, Chat)):
                        continue
                    full_id = channel_full_id(entity)
                    if full_id is None or full_id in known_ids:
                        continue

                    title = getattr(entity, "title", str(entity.id))
                    try:
                        msgs = await fetch_messages_since(client, entity, since)
                    except Exception:
                        continue

                    if not msgs:
                        continue

                    log("-" * 72)
                    log(f"[SCONOSCIUTO] {title}")
                    log(f"telegram_id: {full_id}")
                    log(f"messaggi ultime {args.hours}h: {len(msgs)}")
                    log("Ultimi 3 messaggi:")
                    for m in msgs[-3:]:
                        ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                        preview = m.text.replace("\n", " | ")[:120]
                        log(f"  [{ts}] {preview}")
                    log("")

    except Exception:
        log("\n!!! ERRORE DURANTE DUMP !!!")
        log(traceback.format_exc())
        raise
    finally:
        write_output(out_file, lines)
        safe_print(f"\nOutput: {out_file}")
        safe_print(f"Output: {log_dir / 'signal_last_24h.txt'}")


def main() -> None:
    configure_stdio()
    p = argparse.ArgumentParser(description="Campiona messaggi Telegram recenti")
    p.add_argument("--hours", type=int, default=24, help="Finestra temporale (default 24)")
    p.add_argument(
        "--parser-dry-run",
        action="store_true",
        help="Simula parser su ogni messaggio (-> OPEN | BUY | XAUUSD)",
    )
    p.add_argument(
        "--include-unknown",
        action="store_true",
        help="Elenca anche canali/gruppi non in config con messaggi recenti",
    )
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
