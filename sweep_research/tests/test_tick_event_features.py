import pandas as pd

from sweep_research.src.config import Config
from sweep_research.src.ticks import tick_event_features


def test_raw_tick_time_beyond_and_extreme():
    def make_ticks(mid):
        ts = pd.to_datetime([
            "2022-01-01 00:00:00", "2022-01-01 00:00:10",
            "2022-01-01 00:00:20", "2022-01-01 00:00:40",
        ])
        return pd.DataFrame({
            "ts": ts, "bid": [x - 0.1 for x in mid], "ask": [x + 0.1 for x in mid],
            "bid_vol": [1.0] * 4, "ask_vol": [1.0] * 4,
        })

    def run(sign, level, ticks):
        events = pd.DataFrame({
            "bar_ts_utc": [pd.Timestamp("2022-01-01 00:00:00")],
            "event_ts_utc": [pd.Timestamp("2022-01-01 00:01:00")],
            "level_price": [level], "sign": [sign],
        })
        return tick_event_features(
            Config(), events, lambda day: ticks, pd.Series([1.0], index=events.index)
        ).iloc[0]

    high = run(-1, 100.5, make_ticks([100.0, 101.0, 102.0, 101.0]))
    assert high["time_beyond_level_sec"] == 30.0
    assert high["t_extreme"] == pd.Timestamp("2022-01-01 00:00:20")
    assert high["displacement_60s"] == 0.025
    assert high["displacement_60s_truncated"]

    low = run(1, 101.5, make_ticks([101.0, 100.0, 99.0, 100.0]))
    assert low["t_extreme"] == pd.Timestamp("2022-01-01 00:00:20")
    assert low["displacement_60s"] == 0.025
