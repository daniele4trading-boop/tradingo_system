"""Replay: analisi Sertorio già interpretate (JSON) + barre M1 -> esito shadow dei setup.

  python tools/sertorio_replay.py --analyses analyses_llm.json --m1 xau_m1.csv --out replay.csv

`analyses` = {"<ts ISO Z>": Analysis.to_dict(), ...}; `m1` = CSV time(epoch),open,high,low,close.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sertorio_zones import Analysis, SetupConfig, ShadowBook, build_setups


def load_m1(path: Path):
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            yield (datetime.fromtimestamp(int(float(row["time"])), tz=timezone.utc),
                   float(row["high"]), float(row["low"]), float(row["close"]))


def run(analyses: dict, m1_path: Path, cfg: SetupConfig, out_csv: Path | None) -> ShadowBook:
    book = ShadowBook(cfg, None, out_csv)
    items = sorted((datetime.strptime(k, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc), v)
                   for k, v in analyses.items())
    idx = 0
    for ts, high, low, close in load_m1(m1_path):
        while idx < len(items) and items[idx][0] <= ts:
            a_ts, data = items[idx]
            a = Analysis.from_dict(data)
            setups = build_setups(a, cfg, a_ts, a_ts.strftime("SZ%Y%m%d%H%M"))
            if a.zones or a.ideas:
                book.replace_setups(setups, a_ts)
            idx += 1
        book.on_bar(ts, high, low, close)
    return book


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyses", required=True)
    ap.add_argument("--m1", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-zones", action="store_true", help="solo le idee esplicite dell'analista")
    ap.add_argument("--no-ideas", action="store_true")
    args = ap.parse_args()
    cfg = SetupConfig(derive_from_zones=not args.no_zones, use_ideas=not args.no_ideas)
    out = Path(args.out) if args.out else None
    if out and out.exists():
        out.unlink()
    book = run(json.loads(Path(args.analyses).read_text(encoding="utf-8")), Path(args.m1), cfg, out)
    closed = book.closed
    by_setup: dict[str, list] = {}
    for c in closed:
        by_setup.setdefault(c["setup_id"], []).append(c)
    print(f"setup toccati: {len(by_setup)}  gambe chiuse: {len(closed)}  "
          f"pips totali: {sum(c['pips'] for c in closed):+.1f}  gambe aperte: {len(book.open)}")
    for src in ("idea", "zone"):
        rows = [c for c in closed if c["source"] == src]
        if rows:
            w = sum(1 for c in rows if c["pips"] > 0)
            print(f"  {src:5s}: gambe {len(rows):3d}  win {w:3d}  pips {sum(c['pips'] for c in rows):+8.1f}")
    for sid in sorted(by_setup):
        legs = by_setup[sid]
        print(f"{sid} {legs[0]['direction']} fill={legs[0]['fill']:.2f} "
              + " ".join(f"T{c['tp_index']}:{c['reason']}{c['pips']:+.0f}" for c in sorted(legs, key=lambda c: c['tp_index'])))


if __name__ == "__main__":
    main()
