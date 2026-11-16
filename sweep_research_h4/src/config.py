from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class PeriodConfig:
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class H4Config:
    anchor_utc_hour: int = 21
    source_tf: str = "M1"


@dataclass(frozen=True)
class ExternalConfig:
    dxy: dict[str, str] = field(default_factory=dict)
    real_yield_proxy: dict[str, str] = field(default_factory=dict)
    change_lags_bars: list[int] = field(default_factory=lambda: [5, 20])


@dataclass(frozen=True)
class Config:
    symbols: list[str] = field(default_factory=lambda: ["XAUUSD"])
    primary_symbol: str = "XAUUSD"
    data_root: str = "/home/ubuntu/quantlab-data"
    sources: dict[str, str] = field(default_factory=dict)
    symbol_period_start: dict[str, str] = field(default_factory=dict)
    drop_flat_zero_volume_m1: bool = True
    output_dir: str = "sweep_research_h4/output"
    period: PeriodConfig = field(default_factory=PeriodConfig)
    h4: H4Config = field(default_factory=H4Config)
    price_basis: str = "mid"
    atr_period: int = 14
    swing_n: list[int] = field(default_factory=lambda: [5, 10, 20])
    equal_level_tol_atr: float = 0.05
    equal_level_min_points: int = 2
    max_level_age_bars: int = 250
    min_penetration_atr: float = 0.10
    hypothesis: str = "continuation"
    horizons_bars: list[int] = field(default_factory=lambda: [1, 2, 4, 12])
    mfe_mae_horizon_bars: int = 12
    drift_detrend: dict[str, list[str]] = field(default_factory=lambda: {"by": ["symbol", "year"]})
    external: ExternalConfig = field(default_factory=ExternalConfig)
    corr_window_bars: int = 20
    atr_regime_window_bars: int = 250
    vol_median_window_bars: int = 250
    efficiency_window_bars: int = 10
    week_52_bars: int = 1560
    liquidity_density_atr: float = 0.5
    touch_tol_atr: float = 0.05
    macro_calendar: str = "config/macro_calendar.csv"
    s2_segments_exclude: list[str] = field(default_factory=lambda: ["XAGUSD"])
    s2_min_segment_events: int = 200
    leakage_cuts_per_symbol: int = 4
    seed: int = 20240801

    @property
    def start(self) -> str | None:
        return self.period.start

    @property
    def end(self) -> str | None:
        return self.period.end


def resolve_macro_calendar(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return Path(__file__).resolve().parents[1] / candidate


def load_config(path: str | Path) -> Config:
    raw: dict[str, Any] = yaml.safe_load(Path(path).read_text()) or {}
    period = PeriodConfig(**(raw.pop("period", {}) or {}))
    h4 = H4Config(**(raw.pop("h4", {}) or {}))
    external = ExternalConfig(**(raw.pop("external", {}) or {}))
    raw["external"] = external
    return Config(period=period, h4=h4, **raw)
