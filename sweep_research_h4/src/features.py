from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config, resolve_macro_calendar
from .levels import build_levels


def volatility_regime(atr14: pd.Series, bar_idx: int, window_bars: int = 250) -> float:
    window = atr14.iloc[max(0, bar_idx - window_bars) : bar_idx].dropna()
    current = atr14.iloc[bar_idx]
    if len(window) < 100 or not np.isfinite(current):
        return np.nan
    return float((window < current).mean())


def macro_flags(ts: pd.Series, path: str | Path | None) -> pd.Series:
    if path is None:
        raise FileNotFoundError("macro calendar path is not configured")
    macro_path = Path(path)
    if not macro_path.exists():
        raise FileNotFoundError(f"macro calendar not found: {macro_path}")
    macro = pd.read_csv(macro_path, parse_dates=["ts_utc"])
    if macro.empty:
        return pd.Series(False, index=ts.index)
    values = macro["ts_utc"].dt.tz_localize(None).to_numpy()
    return ts.map(
        lambda x: bool(
            ((values >= x.to_datetime64()) & (values < (x + pd.Timedelta(hours=4)).to_datetime64())).any()
        )
    )


def add_features(
    events: pd.DataFrame,
    bars: pd.DataFrame,
    cfg: Config,
    symbol: str,
    external: dict[str, pd.DataFrame] | None = None,
    all_bars: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = events.copy()
    close = bars["close"]
    atr14 = bars["atr14"]
    rng = (bars["high"] - bars["low"]).replace(0, np.nan)
    out["wick_ratio"] = [
        (bars.iloc[e.bar_idx].high - max(bars.iloc[e.bar_idx].open, bars.iloc[e.bar_idx].close))
        / rng.iloc[e.bar_idx]
        if e.dir == "sweep_high"
        else (min(bars.iloc[e.bar_idx].open, bars.iloc[e.bar_idx].close) - bars.iloc[e.bar_idx].low)
        / rng.iloc[e.bar_idx]
        for e in out.itertuples(index=False)
    ]
    out["dist_from_level_atr"] = [
        abs(float(bars.iloc[e.bar_idx].close) - float(e.level_price)) / float(atr14.iloc[e.bar_idx])
        if np.isfinite(atr14.iloc[e.bar_idx])
        else np.nan
        for e in out.itertuples(index=False)
    ]
    levels = build_levels(bars, cfg)
    out["liquidity_density"] = [
        sum(
            abs(other.price - e.level_price) <= cfg.liquidity_density_atr * atr14.iloc[e.bar_idx]
            and other.confirm_idx < e.bar_idx
            and not (other.form_idx == e.level_form_idx and other.confirm_idx == e.level_confirm_idx)
            for other in levels
        )
        for e in out.itertuples(index=False)
    ]
    out["level_age_bars"] = out["bar_idx"] - out["level_form_idx"]
    out["level_touch_count"] = [
        sum(
            (
                abs(float(bars.iloc[j].high if e.dir == "sweep_high" else bars.iloc[j].low) - e.level_price)
                <= cfg.touch_tol_atr * float(atr14.iloc[j])
                and (
                    bars.iloc[j].close <= e.level_price
                    if e.dir == "sweep_high"
                    else bars.iloc[j].close >= e.level_price
                )
            )
            for j in range(e.level_confirm_idx + 1, e.bar_idx)
            if np.isfinite(atr14.iloc[j])
        )
        for e in out.itertuples(index=False)
    ]
    out["bar_range_atr"] = [
        (rng.iloc[e.bar_idx] / atr14.iloc[e.bar_idx]) for e in out.itertuples(index=False)
    ]
    out["body_ratio"] = [
        abs(bars.iloc[e.bar_idx].close - bars.iloc[e.bar_idx].open) / rng.iloc[e.bar_idx]
        for e in out.itertuples(index=False)
    ]
    previous = atr14.shift(1)
    out["atr_regime"] = [
        atr14.iloc[e.bar_idx]
        / previous.iloc[max(0, e.bar_idx - cfg.atr_regime_window_bars) : e.bar_idx].dropna().median()
        for e in out.itertuples(index=False)
    ]
    out["volatility_regime"] = [
        volatility_regime(atr14, e.bar_idx, cfg.atr_regime_window_bars)
        for e in out.itertuples(index=False)
    ]

    def _vol_vs_median(event: object) -> float:
        bar_idx = event.bar_idx
        window = bars.iloc[max(0, bar_idx - cfg.vol_median_window_bars) : bar_idx]
        if len(window) < 30:
            return np.nan
        same_slot = window[window.bar_slot == bars.iloc[bar_idx].bar_slot]
        return float(bars.iloc[bar_idx].volume / same_slot.volume.median())

    out["vol_vs_median"] = [_vol_vs_median(e) for e in out.itertuples(index=False)]
    out["efficiency_ratio"] = [
        abs(close.iloc[e.bar_idx] - close.iloc[e.bar_idx - cfg.efficiency_window_bars])
        / close.iloc[max(0, e.bar_idx - cfg.efficiency_window_bars + 1) : e.bar_idx + 1].diff().abs().sum()
        if e.bar_idx >= cfg.efficiency_window_bars
        else np.nan
        for e in out.itertuples(index=False)
    ]
    for k in (5, 20):
        out[f"ret_{k}_bp"] = [
            (close.iloc[e.bar_idx] / close.iloc[e.bar_idx - k] - 1) * 1e4 if e.bar_idx >= k else np.nan
            for e in out.itertuples(index=False)
        ]
    shifted_ts = bars.ts + pd.Timedelta(hours=3)
    shifted_iso = shifted_ts.dt.isocalendar()
    week_low = bars.groupby([shifted_iso.year, shifted_iso.week], sort=False).low.cummin()
    week_high = bars.groupby([shifted_iso.year, shifted_iso.week], sort=False).high.cummax()
    month_low = bars.groupby([shifted_ts.dt.year, shifted_ts.dt.month], sort=False).low.cummin()
    month_high = bars.groupby([shifted_ts.dt.year, shifted_ts.dt.month], sort=False).high.cummax()
    out["pos_in_week_range"] = [
        (close.iloc[e.bar_idx] - week_low.iloc[e.bar_idx])
        / (week_high.iloc[e.bar_idx] - week_low.iloc[e.bar_idx])
        if week_high.iloc[e.bar_idx] != week_low.iloc[e.bar_idx]
        else np.nan
        for e in out.itertuples(index=False)
    ]
    out["pos_in_month_range"] = [
        (close.iloc[e.bar_idx] - month_low.iloc[e.bar_idx])
        / (month_high.iloc[e.bar_idx] - month_low.iloc[e.bar_idx])
        if month_high.iloc[e.bar_idx] != month_low.iloc[e.bar_idx]
        else np.nan
        for e in out.itertuples(index=False)
    ]
    high52 = bars.high.rolling(cfg.week_52_bars, min_periods=780).max()
    low52 = bars.low.rolling(cfg.week_52_bars, min_periods=780).min()
    out["dist_52w_high_atr"] = [
        (high52.iloc[e.bar_idx] - close.iloc[e.bar_idx]) / atr14.iloc[e.bar_idx]
        for e in out.itertuples(index=False)
    ]
    out["dist_52w_low_atr"] = [
        (close.iloc[e.bar_idx] - low52.iloc[e.bar_idx]) / atr14.iloc[e.bar_idx]
        for e in out.itertuples(index=False)
    ]
    for name, values in {
        "day_of_month": bars.ts.dt.day,
        "week_of_month": (bars.ts.dt.day - 1) // 7 + 1,
        "weekday": bars.ts.dt.weekday,
        "bar_slot": bars.bar_slot,
        "year": bars.year,
        "month": bars.ts.dt.month,
    }.items():
        out[name] = [values.iloc[e.bar_idx] for e in out.itertuples(index=False)]
    for prefix, frame in (external or {}).items():
        aligned = pd.merge_asof(
            bars[["ts"]].sort_values("ts"),
            frame[["ts", "close"]].sort_values("ts"),
            on="ts",
            direction="backward",
            tolerance=pd.Timedelta(hours=8),
        )["close"]
        out[f"{prefix}_level"] = [aligned.iloc[e.bar_idx] for e in out.itertuples(index=False)]
        for k in cfg.external.change_lags_bars:
            out[f"{prefix}_chg_{k}_bp"] = [
                (aligned.iloc[e.bar_idx] / aligned.iloc[e.bar_idx - k] - 1) * 1e4
                if e.bar_idx >= k
                and np.isfinite(aligned.iloc[e.bar_idx])
                and np.isfinite(aligned.iloc[e.bar_idx - k])
                else np.nan
                for e in out.itertuples(index=False)
            ]
    for other, other_bars in (all_bars or {}).items():
        if other == symbol:
            continue
        joined = (
            bars[["ts", "close"]]
            .rename(columns={"close": "x"})
            .merge(other_bars[["ts", "close"]].rename(columns={"close": "y"}), on="ts", how="inner")
        )
        corr = joined.x.pct_change().rolling(cfg.corr_window_bars, min_periods=15).corr(joined.y.pct_change())
        by_ts = dict(zip(joined.ts, corr, strict=True))
        out[f"corr20_{other}"] = [
            by_ts.get(bars.iloc[e.bar_idx].ts, np.nan) for e in out.itertuples(index=False)
        ]
    out["macro_high_impact_in_bar"] = macro_flags(
        out["bar_ts_utc"], resolve_macro_calendar(cfg.macro_calendar)
    ).to_numpy()
    return out
