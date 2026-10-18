"""Test del gate di rischio prop (Velotrade 1-Step Pro 10k).

I casi limite usano numeri reali del conto Vantage: il segnale IvanTrades
``90e2279b4b`` del 31 luglio (4 ticket a SL nello stesso istante, -474 USD di
flottante a 0.10 lotti) e la giornata del 3 agosto.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dxtrade_risk import (
    FLOOR_BREACH,
    FLOOR_NEAR,
    HARD_STOP,
    MARGIN_CAP,
    NO_SL,
    OK,
    RISK_TOO_BIG,
    SOFT_STOP,
    AccountSnapshot,
    PropRules,
    RiskGate,
    margin_for_order,
    max_units_for_margin,
    max_units_for_risk,
    risk_for_order,
)

RULES = PropRules()  # Velotrade 1-Step Pro 10k


def snap(equity, balance=None, ts=None, margin_used=0.0):
    return AccountSnapshot(
        ts=ts or datetime(2026, 8, 4, 10, 0, tzinfo=timezone.utc),
        equity=equity,
        balance=balance if balance is not None else equity,
        margin_used=margin_used,
    )


# ── regolamento ───────────────────────────────────────────────────────────


def test_velotrade_pro_10k_limits():
    assert RULES.daily_limit == 300.0
    assert RULES.floor == 9700.0
    assert RULES.target == 11000.0


def test_floor_is_static_and_does_not_follow_profits():
    gate = RiskGate()
    gate.evaluate(snap(10000.0))
    gate.evaluate(snap(10800.0, ts=datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)))
    assert gate.rules.floor == 9700.0
    assert gate.floor_room(snap(10800.0)) == pytest.approx(1100.0)


# ── confine giornaliero alle 00:30 UTC ────────────────────────────────────


def test_trading_day_rolls_at_0030_utc():
    gate = RiskGate()
    before = datetime(2026, 8, 4, 0, 15, tzinfo=timezone.utc)
    after = datetime(2026, 8, 4, 0, 45, tzinfo=timezone.utc)
    assert gate.trading_day(before).isoformat() == "2026-08-03"
    assert gate.trading_day(after).isoformat() == "2026-08-04"


def test_daily_reference_resets_after_0030():
    gate = RiskGate()
    gate.evaluate(snap(10000.0, ts=datetime(2026, 8, 3, 20, 0, tzinfo=timezone.utc)))
    gate.evaluate(snap(9800.0, balance=9800.0, ts=datetime(2026, 8, 3, 23, 0, tzinfo=timezone.utc)))
    assert gate.day_reference == 10000.0
    # nuovo giorno: il riferimento diventa la chiusura precedente
    gate.evaluate(snap(9800.0, balance=9800.0, ts=datetime(2026, 8, 4, 1, 0, tzinfo=timezone.utc)))
    assert gate.day_reference == 9800.0
    assert gate.daily_room(snap(9800.0, balance=9800.0)) == pytest.approx(300.0)


def test_halt_is_cleared_by_the_new_trading_day():
    gate = RiskGate()
    day1 = datetime(2026, 8, 3, 15, 0, tzinfo=timezone.utc)
    gate.evaluate(snap(10000.0, ts=day1))
    assert gate.evaluate(snap(9775.0, balance=10000.0, ts=day1)).code == HARD_STOP
    assert gate.halted_until_reset is True

    day2 = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    decision = gate.evaluate(snap(9775.0, balance=9775.0, ts=day2))
    assert decision.code == OK
    assert gate.halted_until_reset is False


def test_after_a_heavy_day_the_static_floor_blocks_the_next_one():
    """Il daily si resetta, il floor no: dopo -240 restano 60 USD in tutto."""
    gate = RiskGate()
    day2 = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    decision = gate.evaluate(snap(9760.0, balance=9760.0, ts=day2))
    assert decision.daily_room == pytest.approx(300.0)  # il daily ricomincia pieno
    assert decision.floor_room == pytest.approx(60.0)   # ma il floor e' vicinissimo
    assert decision.code == FLOOR_NEAR
    assert decision.allow is False


# ── soglie ────────────────────────────────────────────────────────────────


def test_ok_while_well_inside_limits():
    gate = RiskGate()
    decision = gate.evaluate(snap(9950.0, balance=10000.0))
    assert decision.allow is True
    assert decision.code == OK
    assert decision.daily_room == pytest.approx(250.0)
    assert decision.floor_room == pytest.approx(250.0)


def test_soft_stop_at_half_of_the_daily_limit():
    gate = RiskGate()
    decision = gate.evaluate(snap(9850.0, balance=10000.0))
    assert decision.allow is False
    assert decision.code == SOFT_STOP
    assert decision.flatten is False  # si smette di aprire, non si chiude


def test_hard_stop_and_flatten_at_three_quarters():
    gate = RiskGate()
    decision = gate.evaluate(snap(9770.0, balance=10000.0))
    assert decision.code == HARD_STOP
    assert decision.flatten is True


def test_daily_limit_reached_is_hard_stop():
    gate = RiskGate()
    decision = gate.evaluate(snap(9700.0, balance=10000.0))
    assert decision.code in (HARD_STOP, FLOOR_BREACH)
    assert decision.flatten is True


def test_floor_breach_is_terminal():
    gate = RiskGate()
    decision = gate.evaluate(snap(9699.0, balance=10500.0))
    assert decision.code == FLOOR_BREACH
    assert decision.flatten is True


def test_floor_near_triggers_preventive_flatten_even_with_daily_room():
    """Giorno aperto a 9750: il daily consente 300, ma dal floor mancano 40."""
    gate = RiskGate()
    decision = gate.evaluate(snap(9740.0, balance=9750.0))
    assert decision.code == FLOOR_NEAR
    assert decision.flatten is True


def test_soft_stop_persists_until_reset_even_if_equity_recovers():
    gate = RiskGate()
    day = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    gate.evaluate(snap(10000.0, ts=day))
    gate.evaluate(snap(9770.0, balance=10000.0, ts=day))  # hard stop
    decision = gate.evaluate(snap(9990.0, balance=10000.0, ts=day))
    assert decision.allow is False
    assert decision.code == SOFT_STOP


# ── controllo pre-ordine ──────────────────────────────────────────────────


def test_order_without_stop_is_refused():
    gate = RiskGate()
    assert gate.check_order(snap(10000.0), risk_usd=0.0).code == NO_SL


def test_ivan_signal_at_001_lots_is_accepted():
    """4 ticket da 1 oz, SL a 12 USD: 48 USD di rischio, 16% del limite."""
    gate = RiskGate()
    risk = 4 * risk_for_order(entry=4059.0, stop=4071.0, units=1)
    assert risk == pytest.approx(48.0)
    assert gate.check_order(snap(10000.0), risk_usd=risk).allow is True


def test_ivan_signal_at_010_lots_is_refused():
    """Gli stessi 4 ticket a 10 oz rischiano 480 USD: oltre il floor da 300."""
    gate = RiskGate()
    risk = 4 * risk_for_order(entry=4059.0, stop=4071.0, units=10)
    assert risk == pytest.approx(480.0)
    decision = gate.check_order(snap(10000.0), risk_usd=risk)
    assert decision.allow is False
    assert decision.code == RISK_TOO_BIG


def test_order_risk_must_fit_the_remaining_room_not_just_the_limit():
    gate = RiskGate()
    account = snap(9800.0, balance=10000.0)  # gia' -200 oggi
    assert gate.check_order(account, risk_usd=20.0).allow is False  # soft stop attivo
    fresh = RiskGate()
    tight = snap(9890.0, balance=10000.0)  # -110, sotto il soft stop
    assert fresh.check_order(tight, risk_usd=40.0).allow is True
    fresh2 = RiskGate()
    assert fresh2.check_order(tight, risk_usd=200.0).code == RISK_TOO_BIG


def test_margin_cap_blocks_oversized_gold_exposure():
    """Leva 3x: 10 oz d'oro a 4030 chiedono 13.4k di margine su un conto da 10k."""
    gate = RiskGate()
    margin = margin_for_order(price=4030.0, units=10, leverage=3.0)
    assert margin == pytest.approx(13433.33, abs=0.01)
    decision = gate.check_order(snap(10000.0), risk_usd=50.0, margin_usd=margin)
    assert decision.code == MARGIN_CAP


def test_margin_cap_allows_one_ounce_per_ticket():
    gate = RiskGate()
    margin = margin_for_order(price=4030.0, units=1, leverage=3.0)
    assert margin == pytest.approx(1343.33, abs=0.01)
    assert gate.check_order(snap(10000.0), risk_usd=12.0, margin_usd=margin).allow is True


def test_margin_cap_counts_positions_already_open():
    """Tetto al 60% di 10k = 6000 USD di margine, cioe' 4 once d'oro in tutto."""
    gate = RiskGate()
    one_oz = margin_for_order(price=4030.0, units=1, leverage=3.0)
    account = snap(10000.0, margin_used=3 * one_oz)  # 3 oz gia' aperte
    assert gate.check_order(account, risk_usd=12.0, margin_usd=one_oz).allow is True
    crowded = snap(10000.0, margin_used=4 * one_oz)  # la quinta non entra
    assert gate.check_order(crowded, risk_usd=12.0, margin_usd=one_oz).code == MARGIN_CAP


# ── sizing ────────────────────────────────────────────────────────────────


def test_risk_for_order_is_distance_times_units():
    assert risk_for_order(4059.0, 4071.0, 1) == pytest.approx(12.0)
    assert risk_for_order(4059.0, 4071.0, 10) == pytest.approx(120.0)
    assert risk_for_order(4059.0, 4059.0, 10) == 0.0
    assert risk_for_order(4059.0, 4071.0, 0) == 0.0


def test_margin_for_order_rejects_zero_leverage():
    with pytest.raises(ValueError):
        margin_for_order(4030.0, 1, 0)


def test_max_units_for_risk_rounds_down_to_step():
    # 150 USD di spazio, stop a 12 USD -> 12.5 oz -> 12 con step 1
    assert max_units_for_risk(4059.0, 4071.0, 150.0, unit_step=1) == 12
    assert max_units_for_risk(4059.0, 4071.0, 150.0, unit_step=5) == 10
    assert max_units_for_risk(4059.0, 4071.0, 0.0) == 0.0
    assert max_units_for_risk(4059.0, 4059.0, 150.0) == 0.0


def test_max_units_for_margin_matches_the_3x_ceiling():
    """60% di 10k a margine con leva 3x = 18k di notional = 4 oz a 4030."""
    assert max_units_for_margin(4030.0, 10000.0, leverage=3.0, utilization=0.60) == 4
    assert max_units_for_margin(4030.0, 10000.0, leverage=3.0, utilization=0.80) == 5
    assert max_units_for_margin(
        4030.0, 10000.0, leverage=3.0, utilization=0.60, margin_used=6000.0
    ) == 0


def test_full_day_replay_survives_at_one_ounce():
    """Il 31 luglio (giorno peggiore) a 1 oz per ticket sopravvive, per poco.

    A lotti 0.10 l'escursione intraday fu -1416 USD; scalata a 1 oz per ticket
    (fattore 0.10) diventa -141.6, appena sotto il soft stop di 150 e con 158
    USD ancora liberi verso il floor.
    """
    gate = RiskGate()
    day = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    gate.evaluate(snap(10000.0, ts=day))
    worst = gate.evaluate(
        snap(10000.0 - 141.6, balance=10000.0,
             ts=datetime(2026, 7, 31, 14, 8, tzinfo=timezone.utc))
    )
    assert worst.code == OK
    assert worst.flatten is False
    assert worst.floor_room == pytest.approx(158.4)

    # bastano altri 9 USD di escursione per fermare le nuove aperture
    fresh = RiskGate()
    fresh.evaluate(snap(10000.0, ts=day))
    assert fresh.evaluate(snap(9850.0, balance=10000.0, ts=day)).code == SOFT_STOP


def test_full_day_replay_breaches_at_two_ounces():
    """Lo stesso giorno a 2 oz per ticket (-283) forza flat e stop."""
    gate = RiskGate()
    day = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
    gate.evaluate(snap(10000.0, ts=day))
    worst = gate.evaluate(
        snap(10000.0 - 283.2, balance=10000.0,
             ts=datetime(2026, 7, 31, 14, 8, tzinfo=timezone.utc))
    )
    assert worst.code == HARD_STOP
    assert worst.flatten is True
