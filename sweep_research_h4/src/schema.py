from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_schema(path: str | Path, frame: pd.DataFrame, macro_note: str) -> None:
    lines = [
        "# H4 S0 schema",
        "",
        "Availability: `t_close` uses information no later than event-bar close; "
        "`ex_post` is outcome data; `static` is metadata.",
        "",
        f"Macro sources: {macro_note}",
        "",
        "Range semantics: weekly and monthly grouping keys use `(ts + 3h)` so the Sunday "
        "21:00 UTC bar belongs to the following ISO week/month; `day_of_month`, `weekday`, "
        "and `week_of_month` use the unshifted `ts`.",
        "External `dxy_chg_k` and `ust_chg_k` use k bars of the symbol grid after "
        "backward as-of alignment. `corr20_*` uses returns including bar t.",
        "",
        "| Column | Availability | NaN % | Definition |",
        "|---|---|---:|---|",
    ]
    outcomes = [
        c
        for c in frame.columns
        if c.startswith(("fwd_", "mfe_", "mae_"))
        or c
        in {
            "entry_bar_close",
            "entry_next_open",
            "exec_cost_bp",
            "next_bar_gap_h",
            "n_m1_bar",
            "spread_bp_entry",
            "cost_rt_bp",
            "label_start_ts",
            "label_end_ts",
            "label_truncated",
        }
    ]
    for col in frame.columns:
        availability = (
            "ex_post"
            if col in outcomes
            else "static"
            if col
            in {
                "symbol",
                "event_id",
                "bar_idx",
                "bar_ts_utc",
                "event_ts_utc",
                "dir",
                "level_type",
                "level_price",
                "extreme_price",
                "swing_n",
                "trade_sign",
                "level_form_idx",
                "level_confirm_idx",
            }
            else "t_close"
        )
        nan_pct = frame[col].isna().mean() * 100
        lines.append(f"| `{col}` | `{availability}` | {nan_pct:.3f} | Causal H4 S0 field. |")
    Path(path).write_text("\n".join(lines) + "\n")
