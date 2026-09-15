import pandas as pd

from sweep_research.src.config import Config
from sweep_research.src.reports import write_reports


def test_inventory_table_contains_hand_built_entries(tmp_path):
    events = pd.DataFrame({
        "event_id": [1],
        "event_ts_utc": [pd.Timestamp("2022-01-01")],
        "tf": ["M5"],
        "level_type": ["swing_high_5"],
        "dir": ["sweep_high"],
    })
    cfg = Config(reports_dir=str(tmp_path))
    inventory = {
        "XAUUSD/M5": {
            "rows": 12,
            "min_ts": "2022-01-01",
            "max_ts": "2022-01-02",
            "files": 2,
        },
    }
    write_reports(events, cfg, {}, {}, inventory)
    findings = (tmp_path / "FINDINGS.md").read_text()
    assert "## Inventario dati" in findings
    assert "XAUUSD/M5" in findings
    assert "| 12 |" in findings
