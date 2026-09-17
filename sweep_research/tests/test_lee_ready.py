import pandas as pd

from sweep_research.src.ticks import aggregate_m1, lee_ready


def test_quote_lee_ready_and_tick_rule():
    ticks = pd.DataFrame({
        "ts": pd.to_datetime(["2022-01-01 00:00:01", "2022-01-01 00:00:02",
                              "2022-01-01 00:00:03", "2022-01-01 00:01:01"]),
        "bid": [1.0, 1.1, 1.1, 1.0], "ask": [1.2, 1.3, 1.3, 1.2],
        "bid_vol": [1, 2, 3, 4], "ask_vol": [1, 2, 3, 4],
    })
    out = lee_ready(ticks)
    assert out["sign"].tolist() == [1, 1, 1, -1]
    panel = aggregate_m1(ticks)
    assert panel.iloc[0].buy_vol == 12
    assert panel.iloc[1].sell_vol == 8
