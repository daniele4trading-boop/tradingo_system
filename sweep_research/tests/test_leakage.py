import pandas as pd

from sweep_research.src.leakage import truncate_bundle


def test_truncate_bundle_causal_cut():
    frame = pd.DataFrame({"ts": pd.to_datetime(["2022-01-01", "2022-01-02"]), "close": [1, 2]})
    out = truncate_bundle({"m1": frame}, pd.Timestamp("2022-01-01 12:00"))
    assert len(out["m1"]) == 1
