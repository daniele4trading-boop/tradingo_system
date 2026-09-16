import pandas as pd
import pytest

from sweep_research.src.config import Config
from sweep_research.src.outcomes import add_outcomes


def test_forward_return_and_mfe_mae_exact():
    m1 = pd.DataFrame({
        "ts": pd.date_range("2022-01-01 00:00", periods=8, freq="min"),
        "open": [100, 100, 100, 100, 100, 100, 100, 100],
        "high": [100, 101, 104, 103, 106, 102, 102, 102],
        "low": [100, 99, 98, 99, 97, 99, 99, 99],
        "close": [100, 100, 102, 103, 104, 105, 106, 107],
        "volume": 1.0,
        "spread_med": 0.2,
    })
    events = pd.DataFrame({
        "event_ts_utc": [pd.Timestamp("2022-01-01 00:01")],
        "bar_close": [100.0],
        "sign": [1],
        "trade_sign": [1],
        "atr_tf": [2.0],
    })
    cfg = Config(outcome_horizons_min=[1, 3], mfe_mae_horizon_min=3)
    out = add_outcomes(events, m1, cfg).iloc[0]
    assert out["fwd_ret_1_bp"] == pytest.approx(200.0)
    assert out["fwd_ret_3_bp"] == pytest.approx(400.0)
    assert out["fwd_ret_3_dir_bp"] == pytest.approx(400.0)
    assert out["fwd_ret_1_c1_bp"] == pytest.approx(300.0)
    assert out["fwd_ret_3_c1_dir_bp"] == pytest.approx(500.0)
    assert out["entry_c1"] == pytest.approx(100.0)
    assert out["mfe_3_pts"] == pytest.approx(6.0)
    assert out["mae_3_pts"] == pytest.approx(3.0)
    assert out["mfe_3_atr"] == pytest.approx(3.0)
    assert out["mae_3_atr"] == pytest.approx(1.5)


def test_constant_drift_is_removed_from_c1_ex_return(tmp_path):
    close = [100.0] * 20
    m1 = pd.DataFrame({
        "ts": pd.date_range("2022-01-01", periods=20, freq="min"),
        "open": close, "high": [x + 0.1 for x in close],
        "low": [x - 0.1 for x in close], "close": close,
        "spread_med": 0.2,
    })
    events = pd.DataFrame({
        "event_ts_utc": [pd.Timestamp("2022-01-01 00:01")],
        "bar_close": [close[1]], "sign": [1], "trade_sign": [1], "atr_tf": [1.0],
    })
    cfg = Config(
        output_dir=str(tmp_path), outcome_horizons_min=[1], mfe_mae_horizon_min=3
    )
    out = add_outcomes(events, m1, cfg).iloc[0]
    assert out["fwd_ret_1_c1_ex_bp"] == pytest.approx(0.0, abs=1e-8)
