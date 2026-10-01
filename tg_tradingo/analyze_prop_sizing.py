"""Quanto grande si può operare su un conto prop, secondo i dati reali.

Rigioca lo storico di equity del conto Vantage (i file
``MQL5\\Files\\journal\\equity\\equity_*.csv`` scritti dall'EA) applicando il
regolamento di un conto challenge, e cerca il massimo fattore di scala dei
lotti che sopravvive a tutto il periodo.

Il P&L, realizzato e flottante, è lineare nel lotto: se oggi si opera a 0.10
lotti per TP, un conto che opera a ``k × 0.10`` avrà esattamente ``k`` volte
le stesse escursioni. Da qui il fattore di scala.

Uso:

    python analyze_prop_sizing.py --equity-dir /percorso/journal/equity
    python analyze_prop_sizing.py --equity-dir ... --initial 10000 \\
        --daily-pct 3 --dd-pct 3 --lot-per-tp 0.10

Il default è il Velotrade 1-Step Pro da 10k: daily 3%, floor statico 3%.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
from dataclasses import dataclass


@dataclass
class DayCurve:
    date: str
    worst: float      # peggior scostamento intraday dall'apertura
    close: float      # P&L di fine giornata
    worst_at: str


def load_equity_day(path: str) -> DayCurve | None:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("equity")]
    if not rows:
        return None
    equity = [float(r["equity"]) for r in rows]
    stamps = [r.get("ts_utc", "") for r in rows]
    base = equity[0]
    path_pnl = [value - base for value in equity]
    worst = min(path_pnl)
    name = os.path.basename(path)
    digits = "".join(ch for ch in name if ch.isdigit())
    return DayCurve(
        date=digits or name,
        worst=worst,
        close=path_pnl[-1],
        worst_at=stamps[path_pnl.index(worst)],
    )


def replay(days: list[DayCurve], k: float, initial: float, daily_limit: float, floor: float):
    """Rigioca il periodo a scala ``k``. Ritorna (equity minima, breach, pnl)."""
    balance = initial
    lowest = initial
    breach = None
    for day in days:
        start = balance
        low = start + k * day.worst
        lowest = min(lowest, low)
        if breach is None:
            if low < floor:
                breach = (day.date, "floor statico", low, floor)
            elif low < start - daily_limit:
                breach = (day.date, "limite giornaliero", low, start - daily_limit)
        balance = start + k * day.close
    return lowest, breach, balance - initial


def max_scale(days, initial, daily_limit, floor) -> float:
    lo, hi = 0.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        _, breach, _ = replay(days, mid, initial, daily_limit, floor)
        if breach:
            hi = mid
        else:
            lo = mid
    return lo


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--equity-dir", required=True, help="cartella con equity_*.csv")
    parser.add_argument("--initial", type=float, default=10_000.0)
    parser.add_argument("--daily-pct", type=float, default=3.0)
    parser.add_argument("--dd-pct", type=float, default=3.0)
    parser.add_argument("--target-pct", type=float, default=10.0)
    parser.add_argument("--lot-per-tp", type=float, default=0.10)
    parser.add_argument("--lot-single", type=float, default=0.20)
    parser.add_argument("--min-lot", type=float, default=0.01)
    parser.add_argument(
        "--safety",
        type=float,
        default=0.5,
        help="frazione del massimo teorico da usare davvero (default 0.5)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    paths = sorted(glob.glob(os.path.join(args.equity_dir, "equity_*.csv")))
    days = [d for d in (load_equity_day(p) for p in paths) if d]
    if not days:
        print(f"Nessun equity_*.csv in {args.equity_dir}")
        return 1

    daily_limit = args.initial * args.daily_pct / 100.0
    floor = args.initial * (1 - args.dd_pct / 100.0)
    target = args.initial * args.target_pct / 100.0

    print("=" * 92)
    print(f"STORICO — {len(days)} giornate, lotti attuali {args.lot_per_tp}/TP (k = 1)")
    print("=" * 92)
    print(f"{'giorno':<12}{'P&L giorno':>14}{'peggior intraday':>20}{'ora':>24}")
    print("-" * 92)
    for day in days:
        print(f"{day.date:<12}{day.close:>14.2f}{day.worst:>20.2f}{day.worst_at:>24}")
    total = sum(d.close for d in days)
    worst_day = min(days, key=lambda d: d.worst)
    print("-" * 92)
    print(f"{'TOTALE':<12}{total:>14.2f}")
    print(f"\nGiornata peggiore: {worst_day.date} ({worst_day.worst:.2f} sotto l'apertura)")

    print()
    print("=" * 92)
    print(
        f"REGOLE — saldo {args.initial:,.0f}, daily {args.daily_pct:g}% "
        f"({daily_limit:,.0f}), floor statico {floor:,.0f}, target +{target:,.0f}"
    )
    print("=" * 92)

    k_max = max_scale(days, args.initial, daily_limit, floor)
    k_safe = k_max * args.safety
    print(f"k massimo teorico  : {k_max:.3f}  -> {k_max * args.lot_per_tp:.4f} lot/TP")
    print(f"k con margine {args.safety:.0%}   : {k_safe:.3f}  -> {k_safe * args.lot_per_tp:.4f} lot/TP")

    k_min = args.min_lot / args.lot_per_tp
    print(f"k minimo operabile : {k_min:.3f}  -> {args.min_lot:.4f} lot/TP (lotto minimo)")
    if k_safe < k_min:
        print(
            "\n  ATTENZIONE: il k con margine di sicurezza è sotto il lotto minimo.\n"
            "  Il regolamento non lascia spazio a questa strategia nemmeno alla size minima."
        )

    print()
    print(f"{'scenario':<34}{'k':>8}{'lot/TP':>10}{'equity min':>13}{'P&L periodo':>14}{'esito':>26}")
    print("-" * 92)
    scenarios = [
        ("massimo teorico", k_max),
        (f"con margine {args.safety:.0%}", k_safe),
        ("lotto minimo", k_min),
        ("doppio del lotto minimo", 2 * k_min),
    ]
    for label, k in scenarios:
        lowest, breach, pnl = replay(days, k, args.initial, daily_limit, floor)
        outcome = "OK"
        if breach:
            outcome = f"BREACH {breach[1]} il {breach[0]}"
        print(
            f"{label:<34}{k:>8.3f}{k * args.lot_per_tp:>10.4f}"
            f"{lowest:>13.2f}{pnl:>14.2f}{outcome:>26}"
        )

    print()
    per_day = total / len(days)
    if per_day > 0:
        for label, k in scenarios[:3]:
            daily_pnl = k * per_day
            if daily_pnl > 0:
                print(
                    f"  {label:<28} +{daily_pnl:>7.2f} USD/giorno medi "
                    f"-> ~{target / daily_pnl:.0f} giornate al target"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
