"""S1 H4: event study con effetto fisso per simbolo e cluster per settimana."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LEVEL_FAMILY = {
    "equal_high": "equal",
    "equal_low": "equal",
    "swing_high_20": "swing20",
    "swing_low_20": "swing20",
    "swing_high_10": "swing10",
    "swing_low_10": "swing10",
    "swing_high_5": "swing5",
    "swing_low_5": "swing5",
}
N_BOOT = 2000


def prepare(events: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    frame = events.copy()
    ts = pd.to_datetime(frame["event_ts_utc"]).dt.tz_localize(None)
    trading = ts + pd.Timedelta(hours=3)
    frame["trading_day"] = trading.dt.strftime("%Y-%m-%d")
    iso = trading.dt.isocalendar()
    frame["trading_week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    frame["event_year"] = ts.dt.year
    frame["level_family"] = frame["level_type"].map(LEVEL_FAMILY).fillna("other")
    cost = frame["cost_rt_bp"].astype(float)
    for h in horizons:
        frame[f"net_{h}_bp"] = frame[f"fwd_ret_{h}_ex_bp"].astype(float) - cost
    return frame


def _p_normal(t: float | None) -> float | None:
    return None if t is None else float(math.erfc(abs(t) / math.sqrt(2.0)))


def fe_mean(
    values: np.ndarray,
    groups: np.ndarray,
    clusters: np.ndarray,
    rng: np.random.Generator,
    equal_weight: bool = False,
    bootstrap: bool = True,
) -> dict:
    """Media con effetto fisso di gruppo e SE cluster-robust.

    `equal_weight=False`: media n-pesata, residui demeanati per gruppo.
    `equal_weight=True`: media semplice delle medie di gruppo (1/K ciascuna).
    """
    y = np.asarray(values, dtype=float)
    g = np.asarray(groups)
    c = np.asarray(clusters)
    ok = np.isfinite(y) & pd.notna(g) & pd.notna(c)
    y, g, c = y[ok], g[ok], c[ok]
    n = len(y)
    if n < 2:
        return {"n": int(n), "mean": float(y.mean()) if n else None, "t": None, "p": None,
                "boot_ci95": [None, None], "n_clusters": int(len(np.unique(c)))}
    g_levels, g_idx = np.unique(g, return_inverse=True)
    c_levels, c_idx = np.unique(c, return_inverse=True)
    k = len(g_levels)
    g_count = np.bincount(g_idx, minlength=k).astype(float)
    g_mean = np.bincount(g_idx, weights=y, minlength=k) / g_count
    w = 1.0 / (k * g_count[g_idx]) if equal_weight else np.full(n, 1.0 / n)
    est = float(np.sum(w * y))
    influence = w * (y - g_mean[g_idx])
    cluster_sum = np.bincount(c_idx, weights=influence, minlength=len(c_levels))
    se = float(np.sqrt(np.sum(cluster_sum**2)))
    t = est / se if se > 0 else None
    out = {
        "n": int(n),
        "n_clusters": int(len(c_levels)),
        "n_groups": int(k),
        "mean": est,
        "median": float(np.median(y)),
        "std": float(np.std(y)),
        "hit_rate": float(np.mean(y > 0)),
        "se": se,
        "t": t,
        "p": _p_normal(t),
        "boot_ci95": [None, None],
    }
    if bootstrap:
        n_c = len(c_levels)
        flat = c_idx * k + g_idx
        sums = np.bincount(flat, weights=y, minlength=n_c * k).reshape(n_c, k)
        counts = np.bincount(flat, minlength=n_c * k).reshape(n_c, k).astype(float)
        draws = rng.multinomial(n_c, np.full(n_c, 1.0 / n_c), size=N_BOOT).astype(float)
        bs = draws @ sums
        bc = draws @ counts
        if equal_weight:
            with np.errstate(invalid="ignore", divide="ignore"):
                boot = np.nanmean(bs / bc, axis=1)
        else:
            boot = bs.sum(axis=1) / bc.sum(axis=1)
        boot = boot[np.isfinite(boot)]
        if len(boot):
            lo, hi = np.quantile(boot, [0.025, 0.975])
            out["boot_ci95"] = [float(lo), float(hi)]
    return out


def diff_test(frame_a: pd.DataFrame, frame_b: pd.DataFrame, column: str, cluster: str) -> dict:
    """Differenza di medie A-B con influenza cluster-robust (cluster condivisi)."""
    a = frame_a[[column, cluster, "symbol"]].dropna()
    b = frame_b[[column, cluster, "symbol"]].dropna()
    if len(a) < 2 or len(b) < 2:
        return {"n_a": len(a), "n_b": len(b), "diff": None, "t": None, "p": None}

    def infl(part: pd.DataFrame, sign: float) -> pd.Series:
        resid = part[column] - part.groupby("symbol")[column].transform("mean")
        return (sign * resid / len(part)).groupby(part[cluster]).sum()

    diff = float(a[column].mean() - b[column].mean())
    total = infl(a, 1.0).add(infl(b, -1.0), fill_value=0.0)
    se = float(np.sqrt(np.sum(total.to_numpy() ** 2)))
    t = diff / se if se > 0 else None
    return {"n_a": len(a), "n_b": len(b), "mean_a": float(a[column].mean()),
            "mean_b": float(b[column].mean()), "diff": diff, "se": se, "t": t, "p": _p_normal(t)}


def bh(p_values: list[float | None], q: float) -> list[bool]:
    idx = [i for i, p in enumerate(p_values) if p is not None]
    ranked = sorted(idx, key=lambda i: p_values[i])
    m = len(ranked)
    cutoff = 0
    for rank, i in enumerate(ranked, start=1):
        if p_values[i] <= q * rank / m:
            cutoff = rank
    keep = set(ranked[:cutoff])
    return [i in keep for i in range(len(p_values))]


def mfe_mae(frame: pd.DataFrame, horizon: int) -> dict:
    mfe = frame[f"mfe_{horizon}_atr"].to_numpy(float)
    mae = frame[f"mae_{horizon}_atr"].to_numpy(float)
    ok = np.isfinite(mfe) & np.isfinite(mae)
    if not ok.any():
        return {"n": 0}
    mfe, mae = mfe[ok], mae[ok]
    out = {"n": int(ok.sum()), "share_mfe_gt_mae": float(np.mean(mfe > mae))}
    for q in (50, 75, 90):
        out[f"mfe_p{q}"] = float(np.quantile(mfe, q / 100))
        out[f"mae_p{q}"] = float(np.quantile(mae, q / 100))
    return out
