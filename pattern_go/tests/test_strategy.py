from datetime import UTC, datetime, time, timedelta

from pattern_go.strategy import (
    EntryFilters,
    SessionSpec,
    SessionState,
    SwingTracker,
    compute_features,
    match_pattern,
    take_profit,
    trigger_and_stop,
)
from pattern_go.types import Bar, Side, Swing, SwingType

T0 = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)


def bar(i, o, h, low, c):
    return Bar(time=T0 + timedelta(minutes=5 * i), open=o, high=h, low=low, close=c)


def test_swing_high_and_low_need_three_bars():
    bars = [bar(0, 10, 12, 9, 11), bar(1, 11, 15, 10, 14), bar(2, 14, 13, 8, 9)]
    tracker = SwingTracker()
    assert tracker.confirm(bars, 0) is None  # prima barra esclusa
    swing = tracker.confirm(bars, 1)
    assert swing is not None and swing.type is SwingType.HIGH and swing.price == 15


def test_alternation_ignores_same_type_swing():
    tracker = SwingTracker()
    bars = [
        bar(0, 10, 12, 9, 11),
        bar(1, 11, 15, 10, 14),  # swing high
        bar(2, 14, 13, 11, 12),
        bar(3, 12, 16, 11, 15),  # secondo swing high: va ignorato
        bar(4, 15, 14, 12, 13),
    ]
    assert tracker.confirm(bars, 1) is not None
    assert tracker.confirm(bars, 3) is None
    assert [s.type for s in tracker.swings] == [SwingType.HIGH]


def test_swing_not_confirmed_before_next_bar_closes():
    bars = [bar(0, 10, 12, 9, 11), bar(1, 11, 15, 10, 14)]
    tracker = SwingTracker()
    assert tracker.confirm(bars, 1) is None


def test_pattern_short_requires_close_below_whole_range():
    prev = bar(0, 10, 12, 9, 11)  # rialzista, low 9
    engulf_body_only = bar(1, 11, 11.5, 9.5, 9.5)  # chiude sotto l'open, non sotto il low
    valid = bar(1, 11, 11.5, 8.0, 8.5)
    assert not match_pattern(prev, engulf_body_only, Side.SHORT)
    assert match_pattern(prev, valid, Side.SHORT)


def test_pattern_long_requires_close_above_whole_range():
    prev = bar(0, 12, 13, 10, 11)  # ribassista, high 13
    weak = bar(1, 11, 12.8, 10.5, 12.5)
    valid = bar(1, 11, 14, 10.5, 13.5)
    assert not match_pattern(prev, weak, Side.LONG)
    assert match_pattern(prev, valid, Side.LONG)


def test_pattern_doji_counts_as_bullish_first_candle_for_short():
    prev = bar(0, 10, 12, 9, 10)  # doji: close == open
    cur = bar(1, 10, 10.2, 8, 8.5)
    assert match_pattern(prev, cur, Side.SHORT)


def test_trigger_and_stop():
    cur = bar(1, 11, 12, 8, 9)
    assert trigger_and_stop(cur, Side.SHORT) == (8, 12)
    assert trigger_and_stop(cur, Side.LONG) == (12, 8)


M5_FILTERS = EntryFilters(
    body2_frac_min=0.741,
    risk_spread_mult_min=4.941,
    range2_over_range1_min=1.142,
    range2_over_range1_max=1.872,
)


def test_m5_filters_reject_when_spread_too_wide_for_the_risk():
    prev = bar(0, 4000, 4004, 3996, 4003)  # range1 = 8
    cur = bar(1, 4003, 4004, 3992, 3993)  # range2 = 12, body2_frac = 0.833
    wide = compute_features(prev, cur, risk=3.67, spread=1.56)
    assert M5_FILTERS.rejections(wide) == ["risk_spread_mult"]


def test_m5_filters_pass_with_realistic_backtest_spread():
    prev = bar(0, 4000, 4004, 3996, 4003)
    cur = bar(1, 4003, 4004, 3992, 3993)
    ok = compute_features(prev, cur, risk=12.0, spread=0.50)
    assert M5_FILTERS.passes(ok)
    assert ok.body2_frac > 0.741 and 1.142 <= ok.range2_over_range1 <= 1.872


def test_m5_filters_reject_range_expansion_out_of_band():
    prev = bar(0, 4000, 4001, 3999, 4000)  # range1 = 2
    cur = bar(1, 4000, 4001, 3990, 3991)  # range2 = 11 -> ratio 5.5
    features = compute_features(prev, cur, risk=11.0, spread=0.5)
    assert M5_FILTERS.rejections(features) == ["range2_over_range1_max"]


def test_m15_filter_only_checks_risk_spread_mult():
    prev = bar(0, 4000, 4004, 3999, 4003)
    cur = bar(1, 4003, 4004, 3994, 3995)
    m15 = EntryFilters(risk_spread_mult_min=8.625)
    assert m15.rejections(compute_features(prev, cur, risk=7.13, spread=1.5)) == [
        "risk_spread_mult"
    ]
    assert m15.passes(compute_features(prev, cur, risk=7.13, spread=0.5))


def test_min_stop_distance_rejects_tight_stops():
    prev = bar(0, 4000, 4004, 3999, 4003)
    cur = bar(1, 4003, 4004, 3994, 3995)
    filters = EntryFilters(min_stop_distance=8.0)
    tight = compute_features(prev, cur, risk=5.12, spread=0.2)
    assert filters.rejections(tight) == ["min_stop_distance"]
    assert filters.passes(compute_features(prev, cur, risk=8.0, spread=0.2))


def test_min_stop_distance_is_off_by_default():
    prev = bar(0, 4000, 4004, 3999, 4003)
    cur = bar(1, 4003, 4004, 3994, 3995)
    assert EntryFilters().passes(compute_features(prev, cur, risk=0.5, spread=0.2))


def test_take_profit_uses_swing_when_far_enough():
    swing = Swing(index=5, type=SwingType.LOW, price=3990.0, time=T0)
    price, source = take_profit(entry=4000.0, risk=4.0, bias=Side.SHORT, swing=swing)
    assert (price, source) == (3990.0, "swing")


def test_take_profit_falls_back_when_swing_too_close():
    swing = Swing(index=5, type=SwingType.LOW, price=3999.0, time=T0)
    price, source = take_profit(entry=4000.0, risk=4.0, bias=Side.SHORT, swing=swing)
    assert source == "fallback" and price == 4000.0 - 1.5 * 4.0


def test_take_profit_falls_back_when_swing_on_wrong_side():
    swing = Swing(index=5, type=SwingType.LOW, price=4010.0, time=T0)
    _, source = take_profit(entry=4000.0, risk=4.0, bias=Side.SHORT, swing=swing)
    assert source == "fallback"


def test_session_spec_handles_dst_via_zoneinfo():
    london = SessionSpec("london", time(9, 0), "Europe/Rome")
    summer = datetime(2026, 8, 4, 7, 0, tzinfo=UTC)  # CEST = UTC+2
    winter = datetime(2026, 12, 4, 8, 0, tzinfo=UTC)  # CET = UTC+1
    assert london.opens_on(summer)
    assert london.opens_on(winter)
    assert not london.opens_on(summer + timedelta(minutes=5))


def test_session_bias_confirmed_long_and_window_expiry():
    bars = [bar(i, 4000, 4001, 3999, 4000) for i in range(40)]
    tracker = SwingTracker()
    state = SessionState(
        spec=SessionSpec("london", time(9, 0), "Europe/Rome"),
        open_index=0,
        day_bars=288,
        bias_window=5,
    )
    tracker.swings = [
        Swing(index=2, type=SwingType.HIGH, price=4010.0, time=bars[2].time),
        Swing(index=4, type=SwingType.LOW, price=3990.0, time=bars[4].time),
    ]
    assert state.resolve_reference_swings(tracker)
    assert (state.sw_high, state.sw_low, state.ref_index) == (4010.0, 3990.0, 4)

    breakout = bar(6, 4000, 4015, 3999, 4011)
    bars[6] = breakout
    assert state.update_bias(bars, 5) is None
    assert state.update_bias(bars, 6) is Side.LONG
    assert state.bias_confirm_index == 6


def test_session_bias_fails_outside_window():
    bars = [bar(i, 4000, 4001, 3999, 4000) for i in range(20)]
    tracker = SwingTracker()
    tracker.swings = [
        Swing(index=1, type=SwingType.HIGH, price=4010.0, time=bars[1].time),
        Swing(index=2, type=SwingType.LOW, price=3990.0, time=bars[2].time),
    ]
    state = SessionState(
        spec=SessionSpec("ny", time(9, 30), "America/New_York"),
        open_index=0,
        day_bars=288,
        bias_window=3,
    )
    state.resolve_reference_swings(tracker)
    for i in range(3, 7):
        state.update_bias(bars, i)
    assert state.bias is None and state.bias_failed
