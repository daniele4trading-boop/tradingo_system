"""
Migra tradingo_config.json alla struttura canali luglio 2026.

Preserva: telegram (api_id, api_hash, session), paths, mt5_instances.
Sostituisce: channels con CH_GOLD, CH_FOREX, CH_ORO, CH_STARK, CH_IVAN.

Uso:
  python C:\\StatArb\\scripts\\migrate_tradingo_config.py
  python C:\\StatArb\\scripts\\migrate_tradingo_config.py C:\\TG_TradinGo\\tradingo_config.json
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_CONFIG = Path(r"C:\TG_TradinGo\tradingo_config.json")

CHANNELS = [
    {
        "id": "CH_GOLD",
        "enabled": True,
        "telegram_id": -1003302540529,
        "name": "Sala GOLD VIP",
        "parser": "sala_gold",
        "signal_file": "signal_ch_gold.json",
        "magic_base": 12000,
        "execution": {
            "mode": "fixed_lot",
            "fixed_lot_single": 0.20,
            "fixed_lot_per_tp": 0.10,
            "tp_levels_expected": 2,
        },
    },
    {
        "id": "CH_FOREX",
        "enabled": True,
        "telegram_id": -1002890661441,
        "name": "Sala FOREX VIP",
        "parser": "sala_vip",
        "signal_file": "signal_ch_forex.json",
        "magic_base": 13000,
        "execution": {
            "mode": "fixed_lot",
            "fixed_lot_single": 0.20,
            "fixed_lot_per_tp": 0.10,
            "tp_levels_expected": 1,
        },
    },
    {
        "id": "CH_ORO",
        "enabled": True,
        "telegram_id": -1003950995427,
        "name": "Sala ORO VIP",
        "parser": "sala_oro",
        "signal_file": "signal_ch_oro.json",
        "magic_base": 14100,
        "execution": {
            "mode": "fixed_lot",
            "fixed_lot_single": 0.20,
            "fixed_lot_per_tp": 0.10,
            "tp_levels_expected": 2,
        },
    },
    {
        "id": "CH_STARK",
        "enabled": True,
        "telegram_id": -1002073368935,
        "name": "Sala Stark",
        "parser": "sala_stark",
        "signal_file": "signal_ch_stark.json",
        "magic_base": 14000,
        "execution": {
            "mode": "fixed_lot",
            "fixed_lot_single": 0.20,
            "fixed_lot_per_tp": 0.10,
            "tp_levels_expected": 1,
        },
    },
    {
        "id": "CH_IVAN",
        "enabled": False,
        "telegram_id": -1004475607491,
        "name": "IvanTrades - VIP 2.0",
        "parser": "placeholder",
        "signal_file": "signal_ch_ivan.json",
        "magic_base": 17000,
        "execution": {
            "mode": "fixed_lot",
            "fixed_lot_single": 0.20,
            "fixed_lot_per_tp": 0.10,
            "tp_levels_expected": 1,
        },
    },
]


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not config_path.exists():
        raise SystemExit(f"Config non trovato: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # preserva telegram_id Ivan se già impostato manualmente
    for ch in cfg.get("channels", []):
        if ch.get("id") == "CH_IVAN" and ch.get("telegram_id"):
            for target in CHANNELS:
                if target["id"] == "CH_IVAN":
                    target["telegram_id"] = int(ch["telegram_id"])
                    target["enabled"] = bool(ch.get("enabled", False))

    backup = config_path.with_suffix(
        f".json.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    shutil.copy2(config_path, backup)

    paths = cfg.setdefault("paths", {})
    paths.setdefault("state", r"C:\TG_TradinGo\state")

    cfg["channels"] = CHANNELS

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    print(f"Backup: {backup}")
    print(f"Migrato: {config_path}")
    print("Canali attivi:")
    for ch in CHANNELS:
        if ch["enabled"]:
            print(f"  {ch['id']} | {ch['name']} | parser={ch['parser']} | id={ch['telegram_id']}")
    print("Canali disabilitati:")
    for ch in CHANNELS:
        if not ch["enabled"]:
            print(f"  {ch['id']} | {ch['name']} | id={ch['telegram_id']}")


if __name__ == "__main__":
    main()
