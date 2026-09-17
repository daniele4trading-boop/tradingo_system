from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

from . import pipeline
from .schema import schema_for_columns


def _is_outcome_column(name: str) -> bool:
    try:
        return schema_for_columns([name])[0].is_outcome
    except ValueError:
        return name.startswith(("fwd_", "mfe_", "mae_", "entry_c1", "exec_slip", "cost_rt"))


def truncate_bundle(bundle: dict, cut_ts: pd.Timestamp) -> dict:
    out = {}
    for key, frame in bundle.items():
        if key == "bars" and isinstance(frame, dict):
            out[key] = {}
            for tf, bars in frame.items():
                bar_len = pd.Timedelta(minutes={"M1": 1, "M5": 5, "M15": 15, "H1": 60}[tf])
                out[key][tf] = bars[bars.ts + bar_len <= cut_ts].copy()
            continue
        if key == "external" and isinstance(frame, dict):
            out[key] = {}
            for symbol, ext in frame.items():
                if ext.empty:
                    out[key][symbol] = ext.copy()
                else:
                    out[key][symbol] = ext[
                        pd.to_datetime(ext["ts"]) + pd.Timedelta(minutes=1) <= cut_ts
                    ].copy()
            continue
        if key == "ticks_by_day":
            loader = frame
            def truncated_loader(day, loader=loader):
                result = loader(day)
                return result[result["ts"] <= cut_ts].copy()
            out[key] = truncated_loader
            continue
        if not isinstance(frame, pd.DataFrame):
            out[key] = frame
            continue
        x = frame.copy()
        if "event_ts_utc" in x:
            x = x[x.event_ts_utc <= cut_ts]
        elif "ts" in x:
            x = x[x.ts + pd.Timedelta(minutes=1) <= cut_ts].copy()
        out[key] = x
    return out


def check_leakage(bundle: dict, cfg, events: pd.DataFrame) -> dict:
    if events.empty:
        return {"n_cuts": 0, "n_events": 0, "columns_failed": []}
    cuts = events.event_ts_utc.sort_values().iloc[
        np.linspace(0, len(events) - 1, min(cfg.leakage_check.n_cuts, len(events))).astype(int)
    ].tolist()
    failures: list[str] = []
    checked = 0
    for cut in cuts:
        truncated = truncate_bundle(bundle, cut)
        recomputed = pipeline.compute_s0(truncated, cfg)
        reference = events[events.event_ts_utc <= cut]
        keys = ["tf", "bar_ts_utc", "dir", "level_price"]
        tie_breakers = [
            x for x in [
                "level_type", "level_form_ts", "also_n", "level_points",
                "level_age_bars", "extreme_price", "penetration_pts",
            ] if x in reference and x in recomputed
        ]
        sort_keys = keys + tie_breakers
        left = reference.sort_values(sort_keys).reset_index(drop=True)
        right = recomputed.sort_values(sort_keys).reset_index(drop=True)
        checked += len(left)
        if len(left) != len(right) or not left[keys].equals(right[keys]):
            failures.append(f"event_set@{cut}")
            continue
        excluded = {
            column for column in left.columns if _is_outcome_column(column)
        } | {"event_id", "event_ts_utc"}
        for col in left.columns:
            if col in excluded:
                continue
            if col not in right:
                failures.append(f"missing_in_recompute:{col}")
                continue
            if not is_numeric_dtype(left[col]):
                equal = left[col].fillna("<NA>").astype(str).equals(right[col].fillna("<NA>").astype(str))
            else:
                equal = np.allclose(left[col].to_numpy(), right[col].to_numpy(), equal_nan=True, atol=1e-9)
            if not equal:
                failures.append(col)
        causal_right = {
            column for column in right.columns if not _is_outcome_column(column)
        } - {"event_id", "event_ts_utc"}
        for col in sorted(causal_right - set(left.columns)):
            failures.append(col)
    try:
        registry = {spec.name: spec for spec in schema_for_columns(events.columns)}
    except ValueError:
        registry = {}
        failures.extend(
            f"unregistered_column:{col}" for col in events.columns
            if col not in {"event_id", "event_ts_utc"}
            and not col.startswith(("fwd_", "mfe_", "mae_"))
        )
    outcomes = [c for c in events.columns if _is_outcome_column(c)]
    for col in outcomes:
        if col not in registry or not registry[col].is_outcome:
            failures.append(f"outcome_not_marked:{col}")
    return {
        "n_cuts": len(cuts),
        "n_events": checked,
        "verified_columns": len([
            c for c in events.columns
            if c not in {"event_id", "event_ts_utc"}
            and not _is_outcome_column(c)
        ]),
        "columns_failed": sorted(set(failures)),
    }
