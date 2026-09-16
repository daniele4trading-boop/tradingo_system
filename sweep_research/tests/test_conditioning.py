import json
import subprocess
import sys

import numpy as np
import pandas as pd
import yaml

from sweep_research.src.conditioning import _candidates, bh_adjust
from sweep_research.src.config import Config


def test_bh_adjust_hand_calculation():
    values = np.array([0.01, 0.04, 0.03, 0.20, 0.50])
    adjusted = bh_adjust(values)
    expected = np.array([0.05, 0.0666666667, 0.0666666667, 0.25, 0.50])
    np.testing.assert_allclose(adjusted, expected, rtol=1e-8)


def test_conditioning_filter_counts_small_segments():
    rows = []
    for index in range(420):
        day = pd.Timestamp("2022-01-01") + pd.Timedelta(days=index % 40)
        rows.append({
            "ny_date": day,
            "fwd_ret_30_dir_bp": 1.0,
            "fwd_ret_120_dir_bp": 2.0,
        })
    events = pd.DataFrame(rows)
    bins = {
        "tf": pd.Series(["M5"] * 400 + ["M15"] * 20),
    }
    rows, dropped = _candidates(events, bins, Config(), np.random.default_rng(1))
    assert len(rows) == 2
    assert {row["horizon_min"] for row in rows} == {30, 120}
    assert dropped >= 2


def test_s2_smoke_outputs_and_test_count(tmp_path):
    rows = []
    for index in range(60):
        day = pd.Timestamp("2022-01-01") + pd.Timedelta(days=index // 20)
        row = {
            "event_id": index,
            "event_ts_utc": day + pd.Timedelta(minutes=index % 20),
            "event_ts_ny": (day + pd.Timedelta(minutes=index % 20)).isoformat(),
            "ny_date": day,
            "tf": "M5" if index % 2 else "M15",
            "dir": "sweep_high" if index % 2 else "sweep_low",
            "level_type": "equal_high",
        }
        for horizon in [30, 120]:
            row[f"fwd_ret_{horizon}_dir_bp"] = float(index % 3)
        rows.append(row)
    output = tmp_path / "output"
    output.mkdir()
    pd.DataFrame(rows).to_parquet(output / "events.parquet")
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"output_dir": str(output)}))
    result = subprocess.run(
        [sys.executable, "-m", "sweep_research", "run", "--stage", "s2",
         "--config", str(config)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (output / "s2" / "report_s2.html").exists()
    assert (output / "s2" / "s2_summary.json").exists()
    assert (output / "s2" / "tests_s2.csv").exists()
    summary = json.loads((output / "s2" / "s2_summary.json").read_text())
    rows = pd.read_csv(output / "s2" / "tests_s2.csv")
    assert summary["n_tests_s2"] == len(rows)
