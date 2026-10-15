import numpy as np
import pandas as pd

from sweep_research_h4.src.bars import aggregate_h4
from sweep_research_h4.src.config import Config
from sweep_research_h4.src.data import list_m1_files, read_m1
from sweep_research_h4.src.events import dedup_events, detect_events
from sweep_research_h4.src.features import macro_flags, volatility_regime
from sweep_research_h4.src.leakage import _compare, negative_test
from sweep_research_h4.src.outcomes import compute_outcomes, drift_table
from sweep_research_h4.src.primitives import atr, swing_points


def _m1(rows):
    columns = ["ts", "open", "high", "low", "close", "n_ticks", "volume", "spread_med"]
    return pd.DataFrame(rows, columns=columns)


def test_aggregate_h4_anchor_boundaries_and_ohlc():
    ts = pd.date_range("2024-01-01 20:59", periods=7, freq="min")
    rows = [(stamp, i, i + 1, i - 1, i + 0.5, 2, 10.0, 0.2) for i, stamp in enumerate(ts)]
    out = aggregate_h4(_m1(rows), 21)
    assert out.ts.tolist() == [pd.Timestamp("2024-01-01 17:00"), pd.Timestamp("2024-01-01 21:00")]
    assert out.loc[1, ["open", "high", "low", "close"]].tolist() == [1.1, 7.1, 0.1, 6.6]
    assert out.loc[1, "n_m1"] == 6


def test_atr_matches_manual_wilder_values():
    bars = pd.DataFrame({"high": [11.0, 13.0, 14.0], "low": [9.0, 10.0, 12.0], "close": [10.0, 12.0, 13.0]})
    expected = pd.Series([2.0, 3.0, 2.0]).ewm(alpha=0.5, adjust=False, min_periods=2).mean()
    pd.testing.assert_series_equal(atr(bars, 2), expected, check_names=False)


def test_volatility_regime_uses_current_atr_against_prior_window():
    values = pd.Series([1.0] * 100 + [10.0, 2.0])
    assert volatility_regime(values, 101, 250) == 100 / 101
    assert np.isnan(volatility_regime(values, 99, 250))


def test_negative_leakage_compare_detects_future_feature():
    full = pd.DataFrame(
        {
            "event_id": ["a", "b"],
            "causal": [1.0, 2.0],
            "_noncausal": [2.0, 3.0],
        }
    )
    truncated = full.assign(_noncausal=[1.0, 2.0])
    result = _compare(full, truncated, ["causal", "_noncausal"])
    assert result == {"causal": True, "_noncausal": False}
    assert negative_test()


def test_swing_is_confirmed_only_after_n_bars():
    bars = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=7, freq="4h"),
            "high": [1, 2, 5, 2, 1, 1, 1],
            "low": [0, 0, 1, 0, 0, 0, 0],
        }
    )
    points = swing_points(bars, 2, 2)
    row = points[(points.kind == "H") & (points.idx == 2)].iloc[0]
    assert row.confirmed_idx == 4


def test_synthetic_event_has_atr_penetration():
    n = 40
    high = np.ones(n) * 10.0
    low = np.ones(n) * 9.0
    close = np.ones(n) * 9.5
    high[10] = 12.0
    low[10] = 11.0
    close[10] = 11.5
    high[20] = 13.0
    low[20] = 9.0
    close[20] = 11.5
    bars = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=n, freq="4h"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1.0,
            "n_ticks": 1,
            "spread_med": 0.1,
        }
    )
    cfg = Config(symbols=["XAUUSD"], swing_n=[5], max_level_age_bars=100)
    events = detect_events(bars, cfg, "XAUUSD")
    assert not events.empty
    assert events["penetration_atr"].notna().any()
    assert (events["event_ts_utc"] == events["bar_ts_utc"] + pd.Timedelta(hours=4)).all()


def test_dedup_priority_prefers_equal_then_largest_swing():
    base = {
        "symbol": "XAUUSD",
        "event_ts_utc": pd.Timestamp("2024-01-01 21:00"),
        "dir": "sweep_high",
        "level_price": 10.0,
        "extreme_price": 11.0,
        "penetration_atr": 1.0,
        "below_min_penetration": False,
        "swing_n": 5,
    }
    events = pd.DataFrame(
        [
            {**base, "level_type": "swing_high_5"},
            {**base, "level_type": "swing_high_20", "swing_n": 20},
            {**base, "level_type": "equal_high"},
        ]
    )
    assert dedup_events(events).iloc[0].level_type == "equal_high"


def test_source_override_is_used_in_m1_path(tmp_path):
    root = (
        tmp_path / "bars" / "symbol=EURUSD" / "source=dukascopy_candles" / "tf=M1" / "anchor=0" / "year=2024"
    )
    root.mkdir(parents=True)
    path = root / "2024-01.parquet"
    pd.DataFrame({"ts": [pd.Timestamp("2024-01-01")]}).to_parquet(path, index=False)
    cfg = Config(data_root=str(tmp_path), sources={"EURUSD": "dukascopy_candles"})
    assert list_m1_files(cfg, "EURUSD") == [path]


def test_flat_zero_volume_m1_rows_are_removed_but_flat_volume_rows_remain(tmp_path):
    root = (
        tmp_path
        / "bars"
        / "symbol=EURUSD"
        / "source=dukascopy_candles"
        / "tf=M1"
        / "anchor=0"
        / "year=2024"
    )
    root.mkdir(parents=True)
    path = root / "2024-01.parquet"
    pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=3, freq="min"),
            "open": [1.0, 1.0, 1.0],
            "high": [1.0, 1.0, 1.1],
            "low": [1.0, 1.0, 1.0],
            "close": [1.0, 1.0, 1.05],
            "n_ticks": [0, 4, 4],
            "volume": [0.0, 2.0, 0.0],
            "spread_med": [0.0, 0.1, 0.1],
        }
    ).to_parquet(path, index=False)
    cfg = Config(data_root=str(tmp_path), sources={"EURUSD": "dukascopy_candles"})
    out = read_m1(cfg, "EURUSD")
    assert len(out) == 2
    assert out["volume"].tolist() == [2.0, 0.0]


def test_outcomes_direction_drift_and_truncation():
    bars = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=5, freq="4h"),
            "open": [10, 11, 12, 13, 14],
            "high": [11, 12, 13, 14, 15],
            "low": [9, 10, 11, 12, 13],
            "close": [11, 12, 13, 14, 15],
            "spread_med": [0.1] * 5,
            "atr14": [1.0] * 5,
            "year": [2024] * 5,
        }
    )
    bars["symbol"] = "XAUUSD"
    events = pd.DataFrame(
        [{"symbol": "XAUUSD", "bar_idx": 0, "trade_sign": -1, "event_id": "a", "bar_ts_utc": bars.ts[0]}]
    )
    drift = drift_table(bars, [1])
    out, dropped = compute_outcomes(events, bars, [1], 2, drift)
    assert dropped == 0
    assert out.loc[0, "entry_next_open"] == 11
    assert out.loc[0, "fwd_ret_1_dir_bp"] < 0
    assert out.loc[0, "mfe_12_pts"] == 1
    assert not out.loc[0, "label_truncated"]


def test_macro_half_open_interval(tmp_path):
    path = tmp_path / "macro.csv"
    path.write_text("ts_utc,event,source\n2024-01-01T04:00:00Z,NFP,test\n")
    ts = pd.Series(pd.to_datetime(["2024-01-01 00:00", "2024-01-01 04:00"]))
    result = macro_flags(ts, path)
    assert result.tolist() == [False, True]


def test_macro_nfp_bar_is_flagged(tmp_path):
    path = tmp_path / "macro.csv"
    path.write_text("ts_utc,event,source\n2024-01-05T13:30:00Z,NFP,test\n")
    ts = pd.Series(pd.to_datetime(["2024-01-05 13:00"]))
    assert macro_flags(ts, path).tolist() == [True]


def test_missing_macro_calendar_raises(tmp_path):
    missing = tmp_path / "missing.csv"
    try:
        macro_flags(pd.Series(pd.to_datetime(["2024-01-01"])), missing)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing macro calendar must raise FileNotFoundError")
