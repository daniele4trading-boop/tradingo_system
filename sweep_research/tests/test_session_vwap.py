import pandas as pd

from sweep_research.src.config import Config
from sweep_research.src.features_context import session_vwap


def test_session_vwap_dst_and_reset():
    ts = pd.to_datetime(["2022-01-03 13:29", "2022-01-03 13:30", "2022-07-01 12:29", "2022-07-01 12:30"])
    m1 = pd.DataFrame({"ts": ts, "high": [2, 4, 2, 4], "low": [0, 2, 0, 2], "close": [1, 3, 1, 3],
                       "volume": [1, 1, 1, 1]})
    result = session_vwap(m1, Config())
    assert result.iloc[1] == 3
    assert result.iloc[3] == 3
