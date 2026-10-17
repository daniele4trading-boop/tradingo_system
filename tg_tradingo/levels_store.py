"""Livelli tecnici (supporti, resistenze, liquidita') letti dalle analisi dei canali.

Scrive `tradingo_levels.csv` nella cartella segnali di ogni istanza MT5: l'indicatore
`TG_TradinGoLevels.mq5` lo rilegge e disegna le linee. Il file non tocca mai l'EA operativo.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

KINDS = ("support", "resistance", "liquidity")
HEADER = ["symbol", "source", "kind", "price", "price_to", "valid_until_utc", "label"]


@dataclass(frozen=True)
class Level:
    symbol: str
    source: str
    kind: str
    price: float
    price_to: float | None = None
    valid_until_utc: datetime | None = None
    label: str = ""

    def key(self) -> tuple:
        return (self.symbol, self.source, self.kind, round(self.price, 5))


def normalize_level(raw: dict, source: str, default_ttl_h: float = 36.0,
                    now: datetime | None = None) -> Level | None:
    """Valida un livello estratto (es. da LLM); None se incompleto o incoerente."""
    now = now or datetime.now(UTC)
    kind = str(raw.get("kind", "")).strip().lower()
    symbol = str(raw.get("symbol", "")).strip().upper().replace("/", "")
    if kind not in KINDS or not symbol:
        return None
    try:
        price = float(raw["price"])
        price_to = float(raw["price_to"]) if raw.get("price_to") not in (None, "") else None
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or (price_to is not None and price_to <= 0):
        return None
    if price_to is not None and price_to < price:
        price, price_to = price_to, price
    label = " ".join(str(raw.get("label", "")).replace(",", " ").split())[:60]
    return Level(symbol, source.upper(), kind, price, price_to,
                 now + timedelta(hours=default_ttl_h), label)


def merge_levels(current: list[Level], new: list[Level], source: str,
                 now: datetime | None = None) -> list[Level]:
    """Una nuova analisi di `source` per un simbolo sostituisce i livelli precedenti
    dello stesso source+simbolo; scaduti eliminati."""
    now = now or datetime.now(UTC)
    src = source.upper()
    replaced = {lv.symbol for lv in new}
    kept = [lv for lv in current
            if not (lv.source == src and lv.symbol in replaced)
            and (lv.valid_until_utc is None or lv.valid_until_utc > now)]
    out: dict[tuple, Level] = {lv.key(): lv for lv in kept}
    for lv in new:
        out[lv.key()] = lv
    return sorted(out.values(), key=lambda lv: (lv.symbol, lv.source, -lv.price))


def to_csv(levels: list[Level]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    for lv in levels:
        w.writerow([
            lv.symbol, lv.source, lv.kind, f"{lv.price:.5f}".rstrip("0").rstrip("."),
            "" if lv.price_to is None else f"{lv.price_to:.5f}".rstrip("0").rstrip("."),
            "" if lv.valid_until_utc is None else lv.valid_until_utc.strftime("%Y.%m.%d %H:%M"),
            lv.label,
        ])
    return buf.getvalue()


def from_csv(text: str) -> list[Level]:
    out: list[Level] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            until = (datetime.strptime(row["valid_until_utc"], "%Y.%m.%d %H:%M")
                     .replace(tzinfo=UTC) if row.get("valid_until_utc") else None)
            out.append(Level(row["symbol"], row["source"], row["kind"], float(row["price"]),
                             float(row["price_to"]) if row.get("price_to") else None,
                             until, row.get("label", "")))
        except (KeyError, ValueError):
            continue
    return out


def write_levels_file(path: Path, levels: list[Level]) -> None:
    """Scrittura atomica: l'indicatore non legge mai un file a meta'."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".levels_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(to_csv(levels))
    os.replace(tmp, path)
