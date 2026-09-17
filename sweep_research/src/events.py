from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd

from .config import Config
from .levels import Level, build_levels


def compute_penetration_flags(
    penetration_pts: float, spread_med: float, threshold: float
) -> tuple[float, bool]:
    if not np.isfinite(spread_med) or spread_med == 0:
        ratio = float("inf")
    else:
        ratio = penetration_pts / (spread_med / 2)
    return ratio, ratio < threshold


def detect_events(bars: pd.DataFrame, cfg: Config, tf: str) -> pd.DataFrame:
    levels = build_levels(bars, cfg)
    by_confirm: dict[int, list[Level]] = defaultdict(list)
    for level in levels:
        by_confirm[level.confirm_idx].append(level)
    active: list[Level] = []
    rows = []
    step = pd.Timedelta(minutes={"M1": 1, "M5": 5, "M15": 15, "H1": 60}[tf])
    for idx, bar in enumerate(bars.itertuples(index=False)):
        additions = by_confirm.get(idx - 1, [])
        for added in additions:
            if added.kind.startswith("equal_"):
                active = [
                    old for old in active
                    if not (
                        old.kind == added.kind
                        and abs(old.price - added.price) <= cfg.equal_level_tol_ticks * cfg.tick_size
                        and old.points < added.points
                    )
                ]
            active.append(added)
        kept = []
        candidates: dict[tuple[str, float], list[Level]] = defaultdict(list)
        for level in active:
            age = idx - level.confirm_idx
            if age > cfg.max_level_age_bars:
                continue
            if level.kind.endswith("high"):
                if bar.close > level.price:
                    continue
                if bar.high > level.price:
                    candidates[("sweep_high", level.price)].append(level)
            else:
                if bar.close < level.price:
                    continue
                if bar.low < level.price:
                    candidates[("sweep_low", level.price)].append(level)
            kept.append(level)
        active = kept
        for (direction, _price), found in candidates.items():
            non_equal = [x for x in found if x.kind.startswith("swing_")]
            equal = [x for x in found if x.kind.startswith("equal_")]
            groups = []
            if non_equal:
                groups.append(non_equal)
            groups.extend([[x] for x in equal])
            for group in groups:
                level = max(group, key=lambda x: x.n)
                sign = -1 if direction == "sweep_high" else 1
                extreme = bar.high if sign < 0 else bar.low
                spread_med = getattr(bar, "spread_med", float("nan"))
                penetration_half_spreads, subspread_sweep = compute_penetration_flags(
                    abs(extreme - level.price),
                    spread_med,
                    cfg.min_penetration_half_spreads,
                )
                if cfg.hypothesis == "continuation":
                    trade_sign = 1 if direction == "sweep_high" else -1
                elif cfg.hypothesis == "reversal":
                    trade_sign = -1 if direction == "sweep_high" else 1
                else:
                    raise ValueError(f"hypothesis non valida: {cfg.hypothesis}")
                rows.append({
                    "symbol": cfg.symbol, "tf": tf, "bar_ts_utc": bar.ts, "event_ts_utc": bar.ts + step,
                    "event_ts_ny": (bar.ts + step).tz_localize("UTC").tz_convert(cfg.session_tz).isoformat(),
                    "ny_date": ((bar.ts + step).tz_localize("UTC").tz_convert(cfg.session_tz) -
                               pd.Timedelta(hours=18)).date(),
                    "dir": direction, "sign": sign,
                    "trade_sign": trade_sign,
                    "level_type": (
                        f"swing_{level.kind.split('_')[-1]}_{level.n}"
                        if level.kind.startswith("swing_") else level.kind
                    ),
                    "level_price": level.price, "extreme_price": extreme,
                    "penetration_pts": abs(extreme - level.price),
                    "penetration_half_spreads": penetration_half_spreads,
                    "subspread_sweep": subspread_sweep,
                    "bar_open": bar.open, "bar_high": bar.high, "bar_low": bar.low, "bar_close": bar.close,
                    "bar_volume": getattr(bar, "volume", float("nan")),
                    "bar_n_ticks": getattr(bar, "n_ticks", float("nan")),
                    "level_form_ts": level.ts_form, "level_age_bars": idx - level.confirm_idx,
                    "level_touch_count": level.touch_count,
                    "level_points": level.points, "also_n": "|".join(map(str, sorted(x.n for x in group))),
                })
        for level in active:
            if level.kind.endswith("high"):
                near = abs(bar.high - level.price) <= cfg.level_touch_tol_ticks * cfg.tick_size
                broken = bar.close > level.price
            else:
                near = abs(bar.low - level.price) <= cfg.level_touch_tol_ticks * cfg.tick_size
                broken = bar.close < level.price
            if near and not broken:
                level.touch_count += 1
    out = pd.DataFrame(rows)
    if out.empty:
        return out.assign(event_id=pd.Series(dtype="int64"))
    out = out.sort_values(["event_ts_utc", "dir", "level_price"]).reset_index(drop=True)
    out.insert(0, "event_id", range(len(out)))
    return out
