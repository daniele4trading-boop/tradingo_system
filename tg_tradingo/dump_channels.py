r"""
TG TradinGo — Channel Dumper
Stampa gli ultimi N messaggi da tutti i canali configurati + canali sconosciuti attivi.
Eseguire una volta sulla VPS: python dump_channels.py
Output salvato in: C:\TG_TradinGo\logs\channel_dump.txt
"""

import asyncio
import json
import os
import sys as _sys
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat

_sys.path.insert(0, os.path.dirname(__file__))
from bridge_core import resolve_telegram_credentials

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "tradingo_config.json")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TG        = resolve_telegram_credentials(CONFIG["telegram"])
LOG_DIR   = Path(CONFIG["paths"]["logs"])
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE  = LOG_DIR / f"channel_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

MESSAGES_TO_FETCH = 10  # ultimi N messaggi per canale

# ID canali già configurati
KNOWN_IDS = {ch["telegram_id"]: ch["id"] for ch in CONFIG["channels"]}


async def dump():
    client = TelegramClient(
        TG["session_file"],
        TG["api_id"],
        TG["api_hash"]
    )

    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    async with client:
        log("=" * 70)
        log("TG TradinGo — Channel Dumper")
        log(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 70)

        # Recupera tutte le dialog (canali/gruppi a cui sei iscritto)
        dialogs = await client.get_dialogs()

        channels = []
        for d in dialogs:
            entity = d.entity
            if isinstance(entity, (Channel, Chat)):
                # Telethon restituisce ID positivi, MT formato è -100XXXXXXXXXX
                full_id = -(1000000000000 + entity.id) if isinstance(entity, Channel) else entity.id
                channels.append((full_id, entity.id, getattr(entity, "title", str(entity.id)), entity))

        log(f"\nCanali/gruppi trovati: {len(channels)}\n")

        for (full_id, raw_id, title, entity) in channels:
            label = KNOWN_IDS.get(full_id, "[ SCONOSCIUTO ]")
            log("-" * 70)
            log(f"CANALE : {title}")
            log(f"ID full : {full_id}")
            log(f"ID raw  : {raw_id}")
            log(f"Config  : {label}")
            log()

            try:
                msgs = await client.get_messages(entity, limit=MESSAGES_TO_FETCH)
                if not msgs:
                    log("  (nessun messaggio)")
                else:
                    for m in reversed(msgs):
                        if not m.text:
                            continue
                        ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                        log(f"  [{ts}]")
                        # Stampa testo raw riga per riga, indentato
                        for row in m.text.splitlines():
                            log(f"    {row}")
                        log()
            except Exception as e:  # noqa: BLE001
                log(f"  ERRORE lettura messaggi: {e}")

        log("=" * 70)
        log("DUMP COMPLETATO")
        log("=" * 70)

    # Salva su file
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nOutput salvato in: {OUT_FILE}")


if __name__ == "__main__":
    asyncio.run(dump())
