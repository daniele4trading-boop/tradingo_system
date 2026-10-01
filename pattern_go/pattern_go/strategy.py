"""Logica pura della strategia Pattern GO: swing, bias di sessione, pattern, filtri.

Nessuna dipendenza dal broker: tutto opera su barre chiuse passate dall'esterno.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .types import Bar, Side, SignalFeatures, Swing, SwingType


class SwingTracker:
    """Frattali a 3 barre (Larry Williams) con alternanza H->L->H->L forzata.

    Uno swing sulla barra k e' confermato solo dopo la chiusura della barra k+1.
    Se arriva uno swing dello stesso tipo del precedente viene ignorato,
    non sostituito.
    """

    def __init__(self) -> None:
        self.swings: list[Swing] = []

    @staticmethod
    def classify(prev: Bar, mid: Bar, nxt: Bar) -> SwingType | None:
        if mid.high > prev.high and mid.high > nxt.high:
            return SwingType.HIGH
        if mid.low < prev.low and mid.low < nxt.low:
            return SwingType.LOW
        return None

    def confirm(self, bars: list[Bar], k: int) -> Swing | None:
        """Prova a confermare uno swing sulla barra `k` usando `k-1` e `k+1`."""
        if k <= 0 or k + 1 >= len(bars):
            return None
        kind = self.classify(bars[k - 1], bars[k], bars[k + 1])
        if kind is None:
            return None
        if self.swings and self.swings[-1].type == kind:
            return None
        price = bars[k].high if kind is SwingType.HIGH else bars[k].low
        swing = Swing(index=k, type=kind, price=price, time=bars[k].time)
        self.swings.append(swing)
        return swing

    def after(self, index: int) -> list[Swing]:
        return [s for s in self.swings if s.index > index]

    def last_of_type(self, kind: SwingType, before_index: int) -> Swing | None:
        for swing in reversed(self.swings):
            if swing.index < before_index and swing.type is kind:
                return swing
        return None

    def first_of_type_after(self, kind: SwingType, index: int) -> Swing | None:
        for swing in self.swings:
            if swing.index > index and swing.type is kind:
                return swing
        return None


@dataclass(frozen=True)
class SessionSpec:
    name: str
    open_time: time
    tz: str

    def opens_on(self, bar_time: datetime) -> bool:
        """True se `bar_time` (UTC) e' esattamente l'apertura di sessione locale."""
        local = bar_time.astimezone(ZoneInfo(self.tz))
        return (local.hour, local.minute) == (self.open_time.hour, self.open_time.minute)


@dataclass
class SessionState:
    spec: SessionSpec
    open_index: int
    day_bars: int
    bias_window: int
    sw_high: float | None = None
    sw_low: float | None = None
    ref_index: int | None = None
    bias: Side | None = None
    bias_confirm_index: int | None = None
    bias_failed: bool = False
    closed: bool = False

    @property
    def last_index(self) -> int:
        return self.open_index + self.day_bars

    def expired(self, index: int) -> bool:
        return index >= self.last_index

    def resolve_reference_swings(self, tracker: SwingTracker) -> bool:
        """Prende i primi due swing confermati successivi all'apertura di sessione."""
        if self.ref_index is not None:
            return True
        candidates = tracker.after(self.open_index)[:2]
        if len(candidates) < 2:
            return False
        highs = [s for s in candidates if s.type is SwingType.HIGH]
        lows = [s for s in candidates if s.type is SwingType.LOW]
        if not highs or not lows:
            return False
        self.sw_high = highs[0].price
        self.sw_low = lows[0].price
        self.ref_index = max(s.index for s in candidates)
        return True

    def update_bias(self, bars: list[Bar], index: int) -> Side | None:
        """Cerca la conferma del bias sulla barra `index`, entro `bias_window`."""
        if self.bias is not None or self.bias_failed or self.ref_index is None:
            return self.bias
        if index <= self.ref_index:
            return None
        if index > self.ref_index + self.bias_window:
            self.bias_failed = True
            return None
        close = bars[index].close
        if self.sw_high is not None and close > self.sw_high:
            self.bias = Side.LONG
        elif self.sw_low is not None and close < self.sw_low:
            self.bias = Side.SHORT
        else:
            if index == self.ref_index + self.bias_window:
                self.bias_failed = True
            return None
        self.bias_confirm_index = index
        return self.bias


def match_pattern(prev: Bar, cur: Bar, bias: Side) -> bool:
    """Pattern GO: due candele chiuse, chiusura oltre l'intero range della prima.

    Piu' stringente di un engulfing classico: guarda il range, non il corpo.
    """
    if bias is Side.SHORT:
        return (
            prev.close >= prev.open
            and cur.close < cur.open
            and cur.close < prev.low
        )
    return (
        prev.close < prev.open
        and cur.close >= cur.open
        and cur.close > prev.high
    )


def trigger_and_stop(cur: Bar, bias: Side) -> tuple[float, float]:
    """Prezzo dello stop order e stop loss per la barra segnale `cur`."""
    if bias is Side.SHORT:
        return cur.low, cur.high
    return cur.high, cur.low


def compute_features(prev: Bar, cur: Bar, risk: float, spread: float) -> SignalFeatures:
    range1 = prev.range
    range2 = cur.range
    body2_frac = cur.body / range2 if range2 > 0 else 0.0
    range2_over_range1 = range2 / range1 if range1 > 0 else float("inf")
    risk_spread_mult = risk / spread if spread > 0 else float("inf")
    return SignalFeatures(
        range1=range1,
        body1=prev.body,
        range2=range2,
        body2=cur.body,
        body2_frac=body2_frac,
        range2_over_range1=range2_over_range1,
        risk_spread_mult=risk_spread_mult,
        stop_distance=risk,
    )


@dataclass(frozen=True)
class EntryFilters:
    """Soglie di ingresso. `None` disattiva il singolo vincolo."""

    body2_frac_min: float | None = None
    risk_spread_mult_min: float | None = None
    range2_over_range1_min: float | None = None
    range2_over_range1_max: float | None = None
    min_stop_distance: float | None = None

    def rejections(self, f: SignalFeatures) -> list[str]:
        out: list[str] = []
        if self.min_stop_distance is not None and f.stop_distance < self.min_stop_distance:
            out.append("min_stop_distance")
        if self.body2_frac_min is not None and f.body2_frac < self.body2_frac_min:
            out.append("body2_frac")
        if (
            self.risk_spread_mult_min is not None
            and f.risk_spread_mult < self.risk_spread_mult_min
        ):
            out.append("risk_spread_mult")
        if (
            self.range2_over_range1_min is not None
            and f.range2_over_range1 < self.range2_over_range1_min
        ):
            out.append("range2_over_range1_min")
        if (
            self.range2_over_range1_max is not None
            and f.range2_over_range1 > self.range2_over_range1_max
        ):
            out.append("range2_over_range1_max")
        return out

    def passes(self, f: SignalFeatures) -> bool:
        return not self.rejections(f)


def take_profit(
    entry: float,
    risk: float,
    bias: Side,
    swing: Swing | None,
    fallback_r: float = 1.5,
    min_r: float = 0.5,
) -> tuple[float, str]:
    """TP dallo swing opposto al bias, con fallback a `fallback_r` x rischio.

    Lo swing e' valido solo se sta nella direzione favorevole rispetto
    all'ingresso e dista almeno `min_r` x rischio.
    """
    if swing is not None:
        distance = entry - swing.price if bias is Side.SHORT else swing.price - entry
        if distance >= min_r * risk:
            return swing.price, "swing"
    if bias is Side.SHORT:
        return entry - fallback_r * risk, "fallback"
    return entry + fallback_r * risk, "fallback"
