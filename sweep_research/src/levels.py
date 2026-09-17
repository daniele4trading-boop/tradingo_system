from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import Config
from .primitives import swing_points


@dataclass
class Level:
    kind: str
    n: int
    price: float
    form_idx: int
    confirm_idx: int
    points: int
    ts_form: pd.Timestamp
    touch_count: int = 0


def build_levels(bars: pd.DataFrame, cfg: Config) -> list[Level]:
    levels: list[Level] = []
    for n in cfg.swing_n:
        points = swing_points(bars, n, n)
        for row in points.itertuples(index=False):
            levels.append(Level("swing_high" if row.kind == "H" else "swing_low", n, row.price,
                                row.idx, row.confirmed_idx, 1, row.ts))
    base = swing_points(bars, cfg.equal_level_base_n, cfg.equal_level_base_n)
    tol = cfg.equal_level_tol_ticks * cfg.tick_size
    for kind, group in base.groupby("kind"):
        clusters: list[list[object]] = []
        for row in group.itertuples(index=False):
            match = next((c for c in clusters if abs(row.price - c[0].price) <= tol), None)
            if match is None:
                clusters.append([row])
            else:
                match.append(row)
        for cluster in clusters:
            if len(cluster) < cfg.equal_level_min_points:
                continue
            for i in range(1, len(cluster)):
                current = cluster[:i + 1]
                prices = [r.price for r in current]
                levels.append(Level("equal_high" if kind == "H" else "equal_low", cfg.equal_level_base_n,
                                    max(prices) if kind == "H" else min(prices),
                                    current[-1].idx, current[-1].confirmed_idx, len(current),
                                    current[-1].ts))
    return sorted(levels, key=lambda x: (x.confirm_idx, x.form_idx, x.kind))


def level_touch_count(level: Level, bars: pd.DataFrame, idx: int, tol: float) -> int:
    return level.touch_count
