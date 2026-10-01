"""Tipi base condivisi dal sistema Pattern GO."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def opposite(self) -> Side:
        return Side.SHORT if self is Side.LONG else Side.LONG


class SwingType(str, Enum):
    HIGH = "H"
    LOW = "L"


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    MAX_HOLD = "MAX_HOLD"
    SESSION_END = "SESSION_END"
    RISK_GUARD = "RISK_GUARD"
    KILL_SWITCH = "KILL_SWITCH"
    MANUAL = "MANUAL"


@dataclass(frozen=True)
class Bar:
    """Barra OHLC chiusa. `time` e' l'istante di apertura, sempre in UTC."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


@dataclass(frozen=True)
class Swing:
    index: int
    type: SwingType
    price: float
    time: datetime


@dataclass(frozen=True)
class SignalFeatures:
    """Feature calcolate al momento del segnale, solo con dati fino alla barra `i`."""

    range1: float
    body1: float
    range2: float
    body2: float
    body2_frac: float
    range2_over_range1: float
    risk_spread_mult: float
    stop_distance: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "range1": self.range1,
            "body1": self.body1,
            "range2": self.range2,
            "body2": self.body2,
            "body2_frac": self.body2_frac,
            "range2_over_range1": self.range2_over_range1,
            "risk_spread_mult": self.risk_spread_mult,
            "stop_distance": self.stop_distance,
        }


@dataclass
class PendingOrder:
    """Stop order valido solo per la barra successiva a quella del pattern."""

    strategy: str
    side: Side
    trigger_price: float
    stop_loss: float
    take_profit: float
    risk: float
    quantity: float
    client_order_id: str
    signal_bar_index: int
    signal_bar_time: datetime
    expires_after_bar_index: int
    spread_at_signal: float
    features: SignalFeatures
    broker_order_id: str | None = None


@dataclass
class OpenTrade:
    strategy: str
    side: Side
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    risk: float
    entry_bar_index: int
    entry_time: datetime
    client_order_id: str
    theoretical_entry_price: float
    position_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def slippage(self) -> float:
        """Scostamento sfavorevole tra prezzo teorico e fill, in prezzo."""
        if self.side is Side.LONG:
            return self.entry_price - self.theoretical_entry_price
        return self.theoretical_entry_price - self.entry_price
