from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import CANDIDATE_PAIRS, get_profile, load_accounts, load_params
from core.mt5_connection import Mt5Session
from core.symbols import resolve_pair


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe MODULO 1 core MT5/simboli.")
    parser.add_argument("--role", default="data_source", choices=("data_source", "leg_A", "leg_B"))
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Valida solo accounts.json/params.json senza connessione MT5.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = load_accounts()
    load_params()
    profile = get_profile(args.role, accounts)
    print(f"Config OK | role={args.role} profile={profile.name} server={profile.server}")

    if args.config_only:
        print("Connessione MT5 saltata per --config-only")
        return 0

    with Mt5Session(profile) as mt5:
        print("idx,pair,broker_a,tick_value_a,volume_step_a,broker_b,tick_value_b,volume_step_b")
        for candidate in CANDIDATE_PAIRS:
            try:
                resolved = resolve_pair(candidate.leg_a, candidate.leg_b, profile, accounts, mt5)
            except Exception as exc:  # noqa: BLE001 - CLI probe must show every rejected pair.
                print(f"{candidate.index},{candidate.leg_a}/{candidate.leg_b},SKIP,{exc}")
                continue
            print(
                ",".join(
                    [
                        str(candidate.index),
                        f"{candidate.leg_a}/{candidate.leg_b}",
                        resolved.broker_a,
                        str(resolved.meta_a.trade_tick_value),
                        str(resolved.meta_a.volume_step),
                        resolved.broker_b,
                        str(resolved.meta_b.trade_tick_value),
                        str(resolved.meta_b.volume_step),
                    ]
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
