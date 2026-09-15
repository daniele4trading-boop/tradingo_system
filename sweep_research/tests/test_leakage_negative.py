import pandas as pd

from sweep_research.src import pipeline
from sweep_research.src.config import Config, LeakageConfig
from sweep_research.src.leakage import check_leakage


def test_leaky_column_is_reported(monkeypatch):
    events = pd.DataFrame({
        "event_id": [0],
        "tf": ["M5"],
        "bar_ts_utc": [pd.Timestamp("2022-01-01 00:00")],
        "event_ts_utc": [pd.Timestamp("2022-01-01 00:05")],
        "dir": ["sweep_high"],
        "level_price": [100.0],
        "leaky": [1.0],
    })
    bundle = {
        "bars": {},
        "m1": pd.DataFrame({"ts": [pd.Timestamp("2022-01-01 00:00")]}),
        "tick_m1_panel": pd.DataFrame(),
        "external": {},
        "ticks_by_day": lambda day: pd.DataFrame(),
    }

    def leaky_pipeline(_bundle, _cfg):
        result = events.copy()
        result["leaky"] = 2.0
        return result

    monkeypatch.setattr(pipeline, "compute_s0", leaky_pipeline)
    result = check_leakage(bundle, Config(leakage_check=LeakageConfig(n_cuts=1)), events)
    assert "leaky" in result["columns_failed"]
