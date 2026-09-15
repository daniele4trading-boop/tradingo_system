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
    })
    events = pd.DataFrame({
        "event_ts_utc": [pd.Timestamp("2022-01-01 00:01")],
        "bar_close": [100.0],
        "sign": [1],
        "atr_tf": [2.0],
    })
    cfg = Config(outcome_horizons_min=[1, 3], mfe_mae_horizon_min=3)
    out = add_outcomes(events, m1, cfg).iloc[0]
    assert out["fwd_ret_1_bp"] == pytest.approx(200.0)
    assert out["fwd_ret_3_bp"] == pytest.approx(400.0)
    assert out["fwd_ret_3_dir_bp"] == pytest.approx(400.0)
    assert out["mfe_3_pts"] == pytest.approx(6.0)
    assert out["mae_3_pts"] == pytest.approx(3.0)
    assert out["mfe_3_atr"] == pytest.approx(3.0)
    assert out["mae_3_atr"] == pytest.approx(1.5)
