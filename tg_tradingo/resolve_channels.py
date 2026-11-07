"""
Risolvi telegram_id cercando canali per nome (substring, case-insensitive).

Output: stdout + logs/channel_resolve.txt

Uso:
  python resolve_channels.py
  python resolve_channels.py --query "Sala FOREX"
  python resolve_channels.py --query "Ivan" --query "momo"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

from bridge_core import resolve_telegram_credentials

CONFIG_FILE = os.environ.get(
    "TRADINGO_CONFIG",
    os.path.join(os.path.dirname(__file__), "tradingo_config.json"),
)

DEFAULT_QUERIES = [
    "Sala GOLD VIP",
    "Sala FOREX VIP",
    "Sala ORO VIP",
    "Sala Stark",
    "momo Veritas VIP",
    "INMOMOVERITAS",
    "Ivan",
    "Sala VIP",
    "Sala ORO",
    "IvanTrades - VIP",
    "IvanTrades",
]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def load_config() -> dict:
    path = CONFIG_FILE
    if not os.path.exists(path):
        example = os.path.join(os.path.dirname(__file__), "tradingo_config.example.json")
        path = example if os.path.exists(example) else path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def channel_full_id(entity) -> int | None:
    if isinstance(entity, Channel):
        return -(1_000_000_000_000 + entity.id)
    if isinstance(entity, Chat):
        return -entity.id
    return None


async def run(args: argparse.Namespace) -> None:
    config = load_config()
    tg = resolve_telegram_credentials(config["telegram"])
    log_dir = Path(config["paths"]["logs"])
    log_dir.mkdir(parents=True, exist_ok=True)
    out_file = log_dir / "channel_resolve.txt"

    lines: list[str] = []

    def log(s: str = "") -> None:
        lines.append(s)
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("ascii", errors="replace").decode("ascii"))

    client = TelegramClient(tg["session_file"], tg["api_id"], tg["api_hash"])

    async with client:
        log("=" * 72)
        log("TG TradinGo — Channel resolver")
        log(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 72)

        dialogs = await client.get_dialogs()
        entries = []
        for d in dialogs:
            entity = d.entity
            if not isinstance(entity, (Channel, Chat)):
                continue
            title = getattr(entity, "title", "") or ""
            full_id = channel_full_id(entity)
            if full_id is None:
                continue
            entries.append((title, full_id, entity))

        log(f"\nCanali/gruppi totali: {len(entries)}\n")

        for query in args.query:
            log("-" * 72)
            log(f'Query: "{query}"')
            q = query.lower()
            matches = [(t, fid) for t, fid, _ in entries if q in t.lower()]
            if not matches:
                log("  (nessun match)")
            else:
                for title, fid in matches:
                    log(f"  MATCH: {title}")
                    log(f"  telegram_id: {fid}")
            log("")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    log(f"Salvato: {out_file}")


def main() -> None:
    configure_stdio()
    p = argparse.ArgumentParser()
    p.add_argument("--query", action="append", default=[], help="Substring nome canale")
    args = p.parse_args()
    if not args.query:
        args.query = DEFAULT_QUERIES
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
