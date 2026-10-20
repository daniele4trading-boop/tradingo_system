from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .primitives import atr, swing_points


@dataclass(frozen=True)
class Level:
    kind: str
    swing_n: int
    price: float
    form_idx: int
    confirm_idx: int
    points: int = 1


def build_levels(bars: pd.DataFrame, cfg: Config) -> list[Level]:
    atr_values = atr(bars, cfg.atr_period)
    levels: list[Level] = []
    point_sets: dict[int, pd.DataFrame] = {}
    for n in cfg.swing_n:
        points = swing_points(bars, n, n)
        point_sets[n] = points
        for row in points.itertuples(index=False):
            levels.append(
                Level(
                    "swing_high" if row.kind == "H" else "swing_low",
                    n,
                    row.price,
                    row.idx,
                    row.confirmed_idx,
                )
            )
    base_n = min(cfg.swing_n)
    base = point_sets[base_n]
    for kind, group in base.groupby("kind", sort=False):
        clusters: list[list[object]] = []
        for row in group.sort_values("idx").itertuples(index=False):
            tol = cfg.equal_level_tol_atr * atr_values.iloc[row.idx]
            previous = clusters[-1] if clusters else None
            if previous and np.isfinite(tol) and abs(row.price - previous[-1].price) <= tol:
                previous.append(row)
            else:
                clusters.append([row])
            current = clusters[-1]
            if len(current) >= cfg.equal_level_min_points:
                levels.append(
                    Level(
                        "equal_high" if kind == "H" else "equal_low",
                        base_n,
                        float(np.mean([item.price for item in current])),
                        current[-1].idx,
                        current[-1].confirmed_idx,
                        len(current),
                    )
                )
    return sorted(levels, key=lambda level: (level.confirm_idx, level.form_idx, level.kind))
