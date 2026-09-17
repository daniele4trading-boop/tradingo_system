import pandas as pd
import pytest

from sweep_research.src.config import Config
from sweep_research.src.events import compute_penetration_flags, detect_events
from sweep_research.src.levels import build_levels


def _bars():
    highs = [1, 2, 3, 5, 3, 2, 4, 2, 1, 2, 1, 2, 1]
    lows = [0, 0, 1, 2, 1, 0, 1, 0, -1, 0, -1, 0, -1]
    return pd.DataFrame({"ts": pd.date_range("2022-01-01", periods=len(highs), freq="5min"),
                         "open": highs, "high": highs, "low": lows, "close": highs,
                         "volume": 1.0, "n_ticks": 1, "spread_med": 0.1})


def test_confirm_and_sweep():
    bars = _bars()
    cfg = Config(swing_n=[1], equal_level_base_n=1, equal_level_min_points=2)
    levels = build_levels(bars, cfg)
    assert any(x.form_idx == 3 and x.confirm_idx == 4 for x in levels)
    events = detect_events(bars.assign(close=[1, 2, 3, 4, 3, 2, 3.5, 2, 1, 2, 1, 2, 1]), cfg, "M5")
    assert set(events["dir"]) <= {"sweep_high", "sweep_low"}


def test_level_breakout_removes_level():
    bars = _bars()
    bars.loc[5, "close"] = 6
    cfg = Config(swing_n=[1], equal_level_base_n=1)
    events = detect_events(bars, cfg, "M5")
    assert len(events) == 0 or (events.bar_ts_utc >= bars.ts.iloc[5]).all()


def test_subspread_threshold_uses_half_spread():
    assert compute_penetration_flags(0.1, 0.4, 1.0) == (0.5, True)
    ratio, flag = compute_penetration_flags(0.3, 0.4, 1.0)
    assert ratio == pytest.approx(1.5)
    assert flag is False
