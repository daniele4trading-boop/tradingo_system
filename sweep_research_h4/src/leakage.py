from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config
from .events import dedup_events, detect_events
from .features import add_features


def _compare(left: pd.DataFrame, right: pd.DataFrame, columns: list[str]) -> dict[str, bool]:
    by_id_left = left.set_index("event_id")
    by_id_right = right.set_index("event_id")
    common = by_id_left.index.intersection(by_id_right.index)
    result = {}
    for col in columns:
        if col not in by_id_left or col not in by_id_right:
            raise AssertionError(f"leakage comparison column missing: {col}")
        a, b = by_id_left.loc[common, col], by_id_right.loc[common, col]
        equal = np.isclose(a.fillna(np.nan), b.fillna(np.nan), rtol=0, atol=1e-9, equal_nan=True)
        result[col] = bool(np.all(equal))
    return result


def run_leakage(
    cfg: Config,
    symbol: str,
    bars: pd.DataFrame,
    full_events: pd.DataFrame,
    full_features: pd.DataFrame,
    external: dict[str, pd.DataFrame] | None = None,
    all_bars: dict[str, pd.DataFrame] | None = None,
) -> dict:
    rng = np.random.default_rng(cfg.seed + sum(map(ord, symbol)))
    candidates = np.arange(max(100, len(bars) // 4), max(101, len(bars) - 20))
    cuts = sorted(
        rng.choice(candidates, size=min(cfg.leakage_cuts_per_symbol, len(candidates)), replace=False)
    )
    feature_columns = [
        c
        for c in full_features.columns
        if c not in full_events.columns and c not in {"entry_bar_close", "entry_next_open"}
    ]
    checks = []
    for cut in cuts:
        truncated = bars.iloc[:cut].copy()
        events = dedup_events(detect_events(truncated, cfg, symbol))
        events = events[~events["below_min_penetration"]].copy()
        cutoff_ts = truncated.ts.max()
        truncated_external = {
            name: frame[frame["ts"] <= cutoff_ts].copy() for name, frame in (external or {}).items()
        }
        truncated_all_bars = {
            name: frame[frame["ts"] <= cutoff_ts].copy() for name, frame in (all_bars or {}).items()
        }
        features = add_features(
            events,
            truncated,
            cfg,
            symbol,
            truncated_external,
            truncated_all_bars,
        )
        expected = full_features[full_features["bar_idx"] < cut]
        result = _compare(expected, features, feature_columns)
        common = expected["event_id"].isin(features["event_id"]).sum()
        checks.append(
            {
                "cut": int(cut),
                "n_compared": int(common),
                "columns": result,
                "passed": all(result.values()),
            }
        )
    return {
        "symbol": symbol,
        "columns_verified": feature_columns,
        "n_compared": [item["n_compared"] for item in checks],
        "cuts": checks,
        "passed": all(item["passed"] for item in checks),
    }


def negative_test() -> bool:
    full = pd.DataFrame(
        {
            "event_id": ["a", "b"],
            "causal": [1.0, 2.0],
            "_noncausal": [2.0, 3.0],
        }
    )
    truncated = full.assign(_noncausal=[1.0, 2.0])
    result = _compare(full, truncated, ["causal", "_noncausal"])
    return result["causal"] and not result["_noncausal"]


def write_leakage(path: str, results: list[dict]) -> None:
    with open(path, "w") as handle:
        json.dump(
            {
                "symbols": results,
                "negative_test_passed": negative_test(),
                "passed": all(r["passed"] for r in results),
            },
            handle,
            indent=2,
        )
