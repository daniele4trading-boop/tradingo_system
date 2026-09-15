import numpy as np
import pandas as pd

from sweep_research.src.config import Config
from sweep_research.src.features_context import _overnight_features


def test_overnight_range_and_asia_gap_exact():
    ts = pd.to_datetime([
        "2022-01-02 21:59", "2022-01-02 23:00", "2022-01-03 00:00",
        "2022-01-03 07:59", "2022-01-03 08:00", "2022-01-03 21:59",
        "2022-01-03 23:00", "2022-01-04 00:00",
    ])
    m1 = pd.DataFrame({
        "ts": ts,
        "open": [95, 100, 101, 103, 102, 108, 110, 111],
        "high": [95, 101, 104, 103, 102, 108, 111, 112],
        "low": [95, 99, 98, 97, 99, 108, 109, 110],
        "close": [95, 100, 102, 102, 101, 108, 110, 111],
        "volume": 1.0,
    })
    events = pd.DataFrame({
        "bar_ts_utc": pd.to_datetime([
            "2022-01-03 07:30", "2022-01-03 15:00", "2022-01-04 01:00",
        ]),
        "event_ts_utc": pd.to_datetime([
            "2022-01-03 07:30", "2022-01-03 15:00", "2022-01-04 01:00",
        ]),
    })
    atr_map = pd.Series(2.0, index=events["bar_ts_utc"])
    overnight, gap = _overnight_features(m1, events, Config(), atr_map)
    assert np.isnan(overnight[0])
    assert overnight[1] == 3.5
    assert np.isnan(overnight[2])
    assert gap[1] == 2.5
    assert gap[2] == 1.0
