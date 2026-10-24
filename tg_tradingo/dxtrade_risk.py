"""Gate di rischio per conti prop (Velotrade 1-Step Pro e simili).

Su un conto challenge il vincolo non è la strategia, è il regolamento: se
l'equity tocca il floor l'account è chiuso, punto. Questo modulo è
l'equivalente del kill-switch dell'EA MQL5 (`InpMaxFloatingLossUSD`), ma
calibrato sulle regole prop invece che su una soglia fissa.

Regole modellate (default = Velotrade 1-Step Pro 10k):

* **Daily loss limit**: l'equity non può scendere oltre il 3% sotto la
  chiusura del giorno precedente. Reset alle 00:30 UTC.
* **Max drawdown statico**: floor fissato al 97% del saldo iniziale, mai
  spostato, nemmeno dopo giorni in profitto.
* **Margine**: la leva in challenge è bassa (3x sull'oro), quindi il tetto
  all'esposizione contemporanea arriva prima del tetto di rischio.

Il gate non conosce DXtrade: lavora su una fotografia dell'account. Va
chiamato **prima** di ogni apertura e periodicamente durante la giornata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

# Esiti possibili, dal più permissivo al più grave.
OK = "OK"
SOFT_STOP = "DAILY_SOFT_STOP"
HARD_STOP = "DAILY_HARD_STOP"
FLOOR_NEAR = "FLOOR_NEAR"
FLOOR_BREACH = "FLOOR_BREACH"
MARGIN_CAP = "MARGIN_CAP"
RISK_TOO_BIG = "RISK_TOO_BIG"
NO_SL = "NO_STOP_LOSS"


@dataclass(frozen=True)
class PropRules:
    """Regolamento del conto. I default sono il Velotrade 1-Step Pro 10k."""

    initial_balance: float = 10_000.0
    daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.03
    profit_target_pct: float = 0.10
    daily_reset_utc: time = time(0, 30)

    # Margini operativi: si smette di aprire molto prima del limite vero.
    soft_stop_ratio: float = 0.50
    flatten_ratio: float = 0.75
    floor_buffer_ratio: float = 0.20
    max_margin_utilization: float = 0.60
    leverage: float = 3.0

    @property
    def daily_limit(self) -> float:
        return self.initial_balance * self.daily_loss_pct

    @property
    def floor(self) -> float:
        return self.initial_balance * (1.0 - self.max_drawdown_pct)

    @property
    def target(self) -> float:
        return self.initial_balance * (1.0 + self.profit_target_pct)


@dataclass
class AccountSnapshot:
    """Fotografia dell'account al momento della decisione."""

    ts: datetime
    equity: float
    balance: float
    margin_used: float = 0.0
    open_units: float = 0.0


@dataclass
class Decision:
    allow: bool
    code: str
    reason: str
    flatten: bool = False
    daily_room: float = 0.0
    floor_room: float = 0.0

    def __bool__(self) -> bool:
        return self.allow


@dataclass
class RiskGate:
    """Stato del rischio giornaliero. Va tenuto vivo tra un segnale e l'altro."""

    rules: PropRules = field(default_factory=PropRules)
    day_reference: float | None = None
    day_key: date | None = None
    day_low_equity: float | None = None
    halted_until_reset: bool = False

    # ── giornata ──────────────────────────────────────────────────────────

    def trading_day(self, ts: datetime) -> date:
        """Giorno di trading secondo il reset delle 00:30 UTC."""
        moment = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        reset = self.rules.daily_reset_utc
        shifted = moment - timedelta(hours=reset.hour, minutes=reset.minute, seconds=reset.second)
        return shifted.date()

    def observe(self, snapshot: AccountSnapshot) -> None:
        """Aggiorna il riferimento giornaliero e il minimo di equity del giorno."""
        key = self.trading_day(snapshot.ts)
        if key != self.day_key:
            self.day_key = key
            # Il limite giornaliero si misura sulla chiusura del giorno prima.
            self.day_reference = snapshot.balance
            self.day_low_equity = snapshot.equity
            self.halted_until_reset = False
        if self.day_reference is None:
            self.day_reference = snapshot.balance
        if self.day_low_equity is None or snapshot.equity < self.day_low_equity:
            self.day_low_equity = snapshot.equity

    # ── spazio disponibile ────────────────────────────────────────────────

    def daily_room(self, snapshot: AccountSnapshot) -> float:
        """USD ancora perdibili oggi prima del limite giornaliero."""
        reference = self.day_reference if self.day_reference is not None else snapshot.balance
        return snapshot.equity - (reference - self.rules.daily_limit)

    def floor_room(self, snapshot: AccountSnapshot) -> float:
        """USD ancora perdibili prima del floor statico."""
        return snapshot.equity - self.rules.floor

    def usable_room(self, snapshot: AccountSnapshot) -> float:
        return min(self.daily_room(snapshot), self.floor_room(snapshot))

    # ── valutazione continua ──────────────────────────────────────────────

    def evaluate(self, snapshot: AccountSnapshot) -> Decision:
        """Stato del conto adesso: si può ancora operare? Bisogna chiudere?"""
        self.observe(snapshot)
        daily = self.daily_room(snapshot)
        floor = self.floor_room(snapshot)
        common = {"daily_room": daily, "floor_room": floor}

        if floor <= 0:
            return Decision(False, FLOOR_BREACH,
                            f"floor statico violato: equity {snapshot.equity:.2f} <= {self.rules.floor:.2f}",
                            flatten=True, **common)
        if daily <= 0:
            self.halted_until_reset = True
            return Decision(False, HARD_STOP,
                            f"limite giornaliero raggiunto: equity {snapshot.equity:.2f}, "
                            f"riferimento {self.day_reference:.2f}",
                            flatten=True, **common)

        limit = self.rules.daily_limit
        used = limit - daily
        # Il primo giorno daily limit e floor coincidono (entrambi al 3% del
        # saldo iniziale): la causa giornaliera è più informativa, quindi va
        # valutata prima di quella sul floor.
        if used >= limit * self.rules.flatten_ratio:
            self.halted_until_reset = True
            return Decision(False, HARD_STOP,
                            f"consumato il {100 * used / limit:.0f}% del limite giornaliero: flat e stop",
                            flatten=True, **common)
        if floor <= limit * self.rules.floor_buffer_ratio:
            return Decision(False, FLOOR_NEAR,
                            f"a {floor:.2f} USD dal floor: chiusura preventiva",
                            flatten=True, **common)
        if self.halted_until_reset:
            return Decision(False, SOFT_STOP,
                            "trading sospeso fino al reset delle 00:30 UTC", **common)
        if used >= limit * self.rules.soft_stop_ratio:
            return Decision(False, SOFT_STOP,
                            f"consumato il {100 * used / limit:.0f}% del limite giornaliero: "
                            "niente nuove aperture",
                            **common)
        return Decision(True, OK, "entro i limiti", **common)

    # ── controllo pre-ordine ──────────────────────────────────────────────

    def check_order(
        self,
        snapshot: AccountSnapshot,
        risk_usd: float,
        margin_usd: float = 0.0,
    ) -> Decision:
        """Autorizza una singola apertura.

        ``risk_usd`` è la perdita attesa se lo stop viene colpito, calcolata
        con :func:`risk_for_order`. Un ordine senza stop non è autorizzabile:
        su un conto con floor al 3% è la prima causa di bruciatura.
        """
        state = self.evaluate(snapshot)
        if not state.allow:
            return state
        if risk_usd is None or risk_usd <= 0:
            return Decision(False, NO_SL,
                            "ordine senza stop loss: rischio non quantificabile",
                            daily_room=state.daily_room, floor_room=state.floor_room)

        room = self.usable_room(snapshot) - self.rules.daily_limit * (1.0 - self.rules.flatten_ratio)
        if risk_usd > room:
            return Decision(False, RISK_TOO_BIG,
                            f"rischio {risk_usd:.2f} USD oltre lo spazio utilizzabile {room:.2f} USD",
                            daily_room=state.daily_room, floor_room=state.floor_room)

        if margin_usd:
            cap = snapshot.equity * self.rules.max_margin_utilization
            if snapshot.margin_used + margin_usd > cap:
                return Decision(False, MARGIN_CAP,
                                f"margine {snapshot.margin_used + margin_usd:.2f} oltre il tetto "
                                f"{cap:.2f} (leva {self.rules.leverage:g}x)",
                                daily_room=state.daily_room, floor_room=state.floor_room)
        return Decision(True, OK, "ordine autorizzato",
                        daily_room=state.daily_room, floor_room=state.floor_room)


# ── calcoli di sizing ─────────────────────────────────────────────────────


def risk_for_order(
    entry: float, stop: float, units: float, value_per_unit: float = 1.0
) -> float:
    """Perdita in USD se lo stop viene colpito (senza slippage)."""
    if not entry or not stop or units <= 0:
        return 0.0
    return abs(float(entry) - float(stop)) * float(units) * float(value_per_unit)


def margin_for_order(
    price: float, units: float, leverage: float, value_per_unit: float = 1.0
) -> float:
    """Margine richiesto: notional / leva."""
    if leverage <= 0:
        raise ValueError("leverage deve essere positivo")
    return abs(float(price) * float(units) * float(value_per_unit)) / float(leverage)


def max_units_for_risk(
    entry: float,
    stop: float,
    room_usd: float,
    value_per_unit: float = 1.0,
    unit_step: float = 1.0,
) -> float:
    """Unità massime che stanno nello spazio di rischio, arrotondate allo step."""
    distance = abs(float(entry) - float(stop))
    if distance <= 0 or room_usd <= 0:
        return 0.0
    raw = room_usd / (distance * value_per_unit)
    if unit_step and unit_step > 0:
        raw = int(raw / unit_step) * unit_step
    return max(0.0, round(raw, 10))


def max_units_for_margin(
    price: float,
    equity: float,
    leverage: float,
    utilization: float = 0.60,
    value_per_unit: float = 1.0,
    unit_step: float = 1.0,
    margin_used: float = 0.0,
) -> float:
    """Unità massime che stanno nel margine disponibile."""
    budget = equity * utilization - margin_used
    if budget <= 0 or price <= 0:
        return 0.0
    raw = budget * leverage / (price * value_per_unit)
    if unit_step and unit_step > 0:
        raw = int(raw / unit_step) * unit_step
    return max(0.0, round(raw, 10))
