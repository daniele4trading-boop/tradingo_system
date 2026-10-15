import json

import numpy as np
import pandas as pd

from sweep_research_h4.src.config import Config
from sweep_research_h4.src.event_study import bh, fe_mean
from sweep_research_h4.src.stages.s1 import run


def test_fe_mean_equal_weight_vs_n_weighted():
    y = np.r_[np.full(10, 1.0), np.full(30, 3.0)] + np.tile([-0.1, 0.1], 20)
    g = np.r_[["A"] * 10, ["B"] * 30]
    c = np.arange(40)
    rng = np.random.default_rng(0)
    assert abs(fe_mean(y, g, c, rng)["mean"] - 2.5) < 1e-12
    eqw = fe_mean(y, g, c, rng, equal_weight=True)
    assert abs(eqw["mean"] - 2.0) < 1e-12
    assert eqw["boot_ci95"][0] < 2.0 < eqw["boot_ci95"][1]


def test_fe_mean_cluster_se_grows_with_within_cluster_correlation():
    rng = np.random.default_rng(1)
    base = rng.normal(size=50)
    y = np.repeat(base, 4)
    g = np.zeros(200)
    independent = fe_mean(y, g, np.arange(200), rng, bootstrap=False)
    clustered = fe_mean(y, g, np.repeat(np.arange(50), 4), rng, bootstrap=False)
    assert clustered["se"] > 1.9 * independent["se"]


def test_bh_matches_manual_cutoff():
    assert bh([0.001, 0.02, 0.03, 0.5, None], 0.10) == [True, True, True, False, False]
    assert bh([0.2, 0.3], 0.10) == [False, False]


def _synthetic_events(n_per_symbol=300):
    rng = np.random.default_rng(7)
    rows = []
    types = ["equal_high", "swing_high_20", "swing_low_10", "swing_low_5"]
    for s_i, symbol in enumerate(["XAUUSD", "EURUSD", "SPX500"]):
        ts = pd.Timestamp("2018-01-01 01:00") + pd.to_timedelta(np.arange(n_per_symbol) * 28, unit="h")
        for i, stamp in enumerate(ts):
            edge = 25.0 if symbol == "XAUUSD" else 0.0
            row = {"symbol": symbol, "event_ts_utc": stamp, "level_type": types[(i + s_i) % 4],
                   "dir": "sweep_high" if i % 2 else "sweep_low", "cost_rt_bp": 1.0,
                   "mfe_12_atr": abs(rng.normal(1.5)), "mae_12_atr": abs(rng.normal(1.5))}
            for h in (1, 2, 4, 12):
                value = rng.normal(edge, 20.0)
                row.update({f"fwd_ret_{h}_bp": value, f"fwd_ret_{h}_dir_bp": value,
                            f"fwd_ret_{h}_ex_bp": value})
            rows.append(row)
    return pd.DataFrame(rows)


def test_s1_end_to_end_reports_symbol_and_level_splits(tmp_path):
    _synthetic_events().to_parquet(tmp_path / "events_h4.parquet", index=False)
    cfg = Config(symbols=["XAUUSD", "EURUSD", "SPX500"], output_dir=str(tmp_path))
    summary = run(cfg)
    rows = summary["rows"]
    for h in cfg.horizons_bars:
        splits = {(r["split"], r["key"]) for r in rows if r["h"] == h}
        assert {("pooled", "fe_nweighted"), ("pooled", "fe_equal_weight")} <= splits
        assert {("symbol", s) for s in cfg.symbols} <= splits
        assert {("level_family", f) for f in ["equal", "swing20", "swing10", "swing5"]} <= splits
    xau = [r for r in rows if r["split"] == "symbol" and r["key"] == "XAUUSD"]
    eur = [r for r in rows if r["split"] == "symbol" and r["key"] == "EURUSD"]
    assert all(r["bh_q10"] and r["net_mean"] > 20 for r in xau)
    assert not any(r["bh_q10"] and r["net_mean"] > 0 for r in eur)
    assert abs(xau[0]["gross_mean"] - xau[0]["net_mean"] - 1.0) < 1e-9
    s1 = tmp_path / "s1"
    assert (s1 / "s1_report.html").stat().st_size > 10_000
    assert json.loads((s1 / "s1_summary.json").read_text())["verdict"]["n_bh_positive"] >= 4
