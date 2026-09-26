"""Stato persistente dell'executor: setup e gambe (al posto del magic number MT5).

``state.json`` viene riscritto atomicamente a ogni variazione; ``events.jsonl``
è il log append-only leggibile dalla dashboard.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Leg:
    tp_index: int
    quantity: float
    side: str
    fill: float
    sl: float
    tp: float
    opened_at: str
    client_id: str
    position_code: str = ""
    sl_order: str = ""
    tp_order: str = ""
    soft: bool = True
    pending_be: float = 0.0
    status: str = "OPEN"
    close_price: float = 0.0
    close_reason: str = ""
    closed_at: str = ""
    pnl: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.status == "OPEN"


@dataclass
class Setup:
    signal_id: str
    symbol: str
    direction: str
    entry: float
    opened_at: str
    legs: list[Leg] = field(default_factory=list)

    @property
    def open_legs(self) -> list[Leg]:
        return [leg for leg in self.legs if leg.is_open]

    @property
    def opened_ts(self) -> float:
        return datetime.fromisoformat(self.opened_at.replace("Z", "+00:00")).timestamp()


@dataclass
class State:
    processed: dict[str, str] = field(default_factory=dict)
    setups: list[Setup] = field(default_factory=list)
    day: str = ""
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    paper_balance: float = 0.0
    seq: int = 0

    def next_client_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}-{utc_now().strftime('%Y%m%d%H%M%S')}-{self.seq}"

    def open_legs(self, symbol: str | None = None) -> list[tuple[Setup, Leg]]:
        out: list[tuple[Setup, Leg]] = []
        for setup in self.setups:
            if symbol and setup.symbol != symbol:
                continue
            out.extend((setup, leg) for leg in setup.open_legs)
        return out

    def find_setup(self, signal_id: str) -> Setup | None:
        for setup in self.setups:
            if setup.signal_id == signal_id:
                return setup
        return None

    def prune(self, keep_processed: int = 500, keep_closed: int = 200) -> None:
        if len(self.processed) > keep_processed:
            for key in sorted(self.processed, key=self.processed.get)[: len(self.processed) - keep_processed]:
                del self.processed[key]
        closed = [s for s in self.setups if not s.open_legs]
        if len(closed) > keep_closed:
            drop = {id(s) for s in closed[: len(closed) - keep_closed]}
            self.setups = [s for s in self.setups if id(s) not in drop]


class Journal:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = state_dir / "state.json"
        self.events_path = state_dir / "events.jsonl"
        self.state = self._load()

    def _load(self) -> State:
        if not self.state_path.exists():
            return State()
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        setups = [
            Setup(**{**s, "legs": [Leg(**leg) for leg in s.get("legs", [])]})
            for s in raw.get("setups", [])
        ]
        return State(
            processed=raw.get("processed", {}),
            setups=setups,
            day=raw.get("day", ""),
            day_start_equity=float(raw.get("day_start_equity", 0.0)),
            peak_equity=float(raw.get("peak_equity", 0.0)),
            paper_balance=float(raw.get("paper_balance", 0.0)),
            seq=int(raw.get("seq", 0)),
        )

    def save(self) -> None:
        self.state.prune()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.state), indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def event(self, kind: str, **fields: Any) -> None:
        rec = {"ts": iso(utc_now()), "event": kind, **fields}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
