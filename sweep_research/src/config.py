from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class PeriodConfig:
    start: str
    end: str


@dataclass
class ExternalConfig:
    dxy: str = "DOLLARIDXUSD"
    ust_proxy: str = "USTBONDTRUSD"
    silver: str = "XAGUSD"
    lookback_min: int = 60


@dataclass
class LeakageConfig:
    n_cuts: int = 5
    sample_events: int = 200


@dataclass
class NewsConfig:
    source: str | None = None


@dataclass
class Config:
    symbol: str = "XAUUSD"
    data_root: str = "/home/ubuntu/quantlab-data"
    output_dir: str = "sweep_research/output"
    reports_dir: str = "sweep_research"
    seed: int = 20240801
    period: PeriodConfig = field(default_factory=lambda: PeriodConfig("2022-08-01", "2026-07-24"))
    timeframes: list[str] = field(default_factory=lambda: ["M5", "M15"])
    swing_n: list[int] = field(default_factory=lambda: [5, 10, 20])
    tick_size: float = 0.01
    equal_level_tol_ticks: int = 20
    equal_level_min_points: int = 2
    equal_level_base_n: int = 5
    max_level_age_bars: int = 500
    level_touch_tol_ticks: int = 20
    liquidity_density_ticks: int = 50
    atr_period: int = 14
    er_period: int = 20
    burst_fast_atr: int = 5
    burst_slow_atr: int = 50
    burst_threshold: float = 1.5
    hourly_median_days: int = 20
    vpin_window_bars: int = 50
    profile_bin_ticks: int = 10
    profile_value_area: float = 0.70
    session_tz: str = "America/New_York"
    london_tz: str = "Europe/London"
    ny_open: str = "08:30"
    london_open: str = "08:00"
    trading_day_anchor: str = "18:00"
    sessions: dict[str, list[str]] = field(default_factory=lambda: {
        "asia": ["18:00", "03:00"], "london": ["03:00", "08:30"], "ny": ["08:30", "17:00"]
    })
    killzones: dict[str, list[str]] = field(default_factory=lambda: {
        "kz_asia": ["20:00", "00:00"], "kz_london": ["02:00", "05:00"],
        "kz_ny_am": ["08:30", "11:00"], "kz_ny_pm": ["13:30", "16:00"]
    })
    external: ExternalConfig = field(default_factory=ExternalConfig)
    outcome_horizons_min: list[int] = field(default_factory=lambda: [5, 15, 30, 60, 120])
    mfe_mae_horizon_min: int = 120
    leakage_check: LeakageConfig = field(default_factory=LeakageConfig)
    news: NewsConfig = field(default_factory=NewsConfig)

    @property
    def start(self):
        return self.period.start

    @property
    def end(self):
        return self.period.end


def _merge(default: Any, value: Any) -> Any:
    if isinstance(default, dict) and isinstance(value, dict):
        return {k: _merge(default.get(k), v) for k, v in value.items()}
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        alt = Path(__file__).parents[1] / path
        path = alt if alt.exists() else path
    raw = yaml.safe_load(path.read_text()) or {}
    base = Config()
    defaults = {k: getattr(base, k) for k in base.__dataclass_fields__}
    values = {k: _merge(defaults.get(k), v) for k, v in raw.items()}
    if isinstance(values.get("period"), dict):
        values["period"] = PeriodConfig(**values["period"])
    if isinstance(values.get("external"), dict):
        values["external"] = ExternalConfig(**values["external"])
    if isinstance(values.get("leakage_check"), dict):
        values["leakage_check"] = LeakageConfig(**values["leakage_check"])
    if isinstance(values.get("news"), dict):
        values["news"] = NewsConfig(**values["news"])
    cfg = Config(**values)
    if not cfg.timeframes or not cfg.swing_n or cfg.period.start >= cfg.period.end:
        raise ValueError("configurazione periodo/timeframe invalida")
    return cfg
