"""
TG TradinGo — Channel Signal Sampler
Estrae gli ultimi 50 messaggi dai 4 canali operativi.
Output: C:\TG_TradinGo\logs\signal_samples.txt
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from telethon import TelegramClient

import sys as _sys
_sys.path.insert(0, os.path.dirname(__file__))
from bridge_core import resolve_telegram_credentials  # noqa: E402

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "tradingo_config.json")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

TG      = resolve_telegram_credentials(CONFIG["telegram"])
LOG_DIR = Path(CONFIG["paths"]["logs"])
LOG_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = LOG_DIR / "signal_samples.txt"

MESSAGES_TO_FETCH = 50

# I 4 canali operativi con ID corretti
CHANNELS = [
    {"id": "CH1", "name": "ZANNI VIP SIGNALS",       "telegram_id": -1003026686847},
    {"id": "CH2", "name": "SALA GOLD VIP",            "telegram_id": -1003302540529},
    {"id": "CH3", "name": "SALA VIP",                 "telegram_id": -1002890661441},
    {"id": "CH4", "name": "SALA STARK",               "telegram_id": -1002073368935},
]

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
        log(f"TG TradinGo — Signal Sampler (ultimi {MESSAGES_TO_FETCH} messaggi)")
        log(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 70)

        for ch in CHANNELS:
            log()
            log("=" * 70)
            log(f"  {ch['id']} — {ch['name']}")
            log(f"  Telegram ID: {ch['telegram_id']}")
            log("=" * 70)

            try:
                entity = await client.get_entity(ch["telegram_id"])
                msgs   = await client.get_messages(entity, limit=MESSAGES_TO_FETCH)

                if not msgs:
                    log("  (nessun messaggio trovato)")
                    continue

                count = 0
                for m in reversed(msgs):
                    if not m.text or not m.text.strip():
                        continue
                    count += 1
                    ts = m.date.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    log(f"[{count:02d}] {ts}")
                    for row in m.text.splitlines():
                        log(f"     {row}")
                    log()

                log(f"  Totale messaggi con testo: {count}")

            except Exception as e:
                log(f"  ERRORE: {e}")

        log()
        log("=" * 70)
        log("DUMP COMPLETATO")
        log("=" * 70)

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nOutput salvato in: {OUT_FILE}")

if __name__ == "__main__":
    asyncio.run(dump())
