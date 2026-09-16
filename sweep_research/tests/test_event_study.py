import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

from sweep_research.src.config import Config
from sweep_research.src.event_study import (
    cluster_diff_test,
    dedup_events,
    describe_returns,
    run_s1,
)


def test_dedup_events_uses_level_priority():
    events = pd.DataFrame({
        "event_id": [4, 2, 3, 1],
        "event_ts_utc": [pd.Timestamp("2022-01-01 10:00")] * 4,
        "dir": ["sweep_high"] * 4,
        "tf": ["M5", "M5", "M5", "M15"],
        "level_type": ["swing_high_5", "swing_high_10", "equal_high", "swing_high_20"],
    })
    selected = dedup_events(events, ["event_ts_utc", "dir"])
    assert len(selected) == 1
    assert selected.iloc[0]["level_type"] == "equal_high"


def test_describe_returns_cluster_day_and_bootstrap():
    rng = np.random.default_rng(123)
    values = np.array([1.0, 2.0, 3.0])
    separate = describe_returns(values, np.array(["a", "b", "c"]), rng, 5)
    assert separate["mean"] == 2.0
    assert separate["median"] == 2.0
    assert separate["hit_rate"] == 1.0
    assert np.isclose(separate["t_cluster_day"], separate["t_naive"])
    repeated = describe_returns(
        np.repeat([1.0, 3.0], 4), np.repeat(["a", "b"], 4), np.random.default_rng(123), 5
    )
    assert np.isclose(repeated["t_cluster_day"], repeated["t_naive"] / 2)
    boot = describe_returns(values, np.array(["a", "b", "c"]), np.random.default_rng(123), 5)
    assert boot["boot_ci95"][0] <= boot["mean"] <= boot["boot_ci95"][1]
    assert boot == describe_returns(values, np.array(["a", "b", "c"]), np.random.default_rng(123), 5)
    costs = describe_returns(
        values, np.array(["a", "b", "c"]), np.random.default_rng(123), 5,
        cost_bp=np.array([0.5, 1.0, 1.5]),
    )
    assert costs["mean_cost_bp"] == 1.0
    assert costs["mean_net_bp"] == 1.0


def test_cluster_diff_independent_days_matches_welch_scale():
    result = cluster_diff_test(
        np.array([1.0, 2.0]), np.array(["a", "b"]),
        np.array([0.0, 1.0]), np.array(["c", "d"]),
    )
    assert result["diff"] == 1.0
    assert result["se_cluster_day"] == pytest.approx(0.5)


def _synthetic_events(n=60):
    rows = []
    for index in range(n):
        day = pd.Timestamp("2022-01-03") + pd.Timedelta(days=index // 20)
        ts = day + pd.Timedelta(minutes=index % 20)
        row = {
            "event_id": index,
            "event_ts_utc": ts,
            "ny_date": day,
            "tf": "M5" if index % 2 else "M15",
            "dir": "sweep_high" if index % 2 else "sweep_low",
            "subspread_sweep": False,
            "level_type": "equal_high" if index % 3 == 0 else "swing_high_5",
            "mfe_120_atr": 1.0 + index / n,
            "mae_120_atr": 0.5 + index / (2 * n),
            "mfe_120_pts": 10.0 + index,
            "mae_120_pts": 5.0 + index / 2,
        }
        for horizon in [5, 15, 30, 60, 120]:
            row[f"fwd_ret_{horizon}_dir_bp"] = float((index % 7) - 2)
            row[f"fwd_ret_{horizon}_c1_dir_bp"] = float((index % 7) - 2)
            row[f"fwd_ret_{horizon}_c1_ex_dir_bp"] = float((index % 7) - 2)
            row[f"fwd_ret_{horizon}_c1_ex_bp"] = float((index % 7) - 2)
            row[f"fwd_ret_{horizon}_c1_bp"] = float((index % 7) - 2)
            row[f"fwd_ret_{horizon}_bp"] = float((index % 7) - 2)
        row["cost_rt_bp"] = 0.1
        rows.append(row)
    return pd.DataFrame(rows)


def test_s1_smoke_outputs_and_test_count(tmp_path):
    events = _synthetic_events()
    output = tmp_path / "output"
    output.mkdir()
    events.to_parquet(output / "events.parquet")
    cfg = Config(output_dir=str(output), reports_dir=str(tmp_path))
    summary = run_s1(cfg)
    assert (output / "s1" / "report_s1.html").exists()
    assert (output / "s1" / "s1_summary.json").exists()
    payload = json.loads((output / "s1" / "s1_summary.json").read_text())
    assert payload["n_tests_s1"] == len(payload["tests"])
    assert payload["n_tests_s1"] == 40
    assert summary["n_events"]["raw"] == 60


def test_s1_missing_events_is_clean(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"output_dir": str(tmp_path / "missing")}))
    result = subprocess.run(
        [sys.executable, "-m", "sweep_research", "run", "--stage", "s1", "--config", str(config)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "events.parquet" in result.stderr
