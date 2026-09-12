from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MT5Settings:
    terminal_path: str | None
    login: int | None
    password: str | None
    server: str | None
    initialize_timeout_ms: int = 60000


@dataclass(frozen=True)
class StorageSettings:
    root: Path
    ticks_dir: Path
    rates_dir: Path
    bars_dir: Path
    reports_dir: Path


@dataclass(frozen=True)
class MarketSettings:
    expected_digits: int
    default_point: float
    target_points: int
    stop_points: int
    max_spread_points: int


@dataclass(frozen=True)
class ExportSettings:
    tick_chunk_days: int
    rates_timeframe: str


@dataclass(frozen=True)
class FeatureSettings:
    rsi_period: int
    ema_fast: int
    ema_mid: int
    ema_slow: int
    atr_period: int
    adx_period: int
    momentum_period: int
    vwap_period: int


@dataclass(frozen=True)
class BacktestSettings:
    horizon_bars: int
    slippage_points: int
    commission_per_lot_round_turn: float


@dataclass(frozen=True)
class ResearchConfig:
    symbol: str
    broker: str
    mt5: MT5Settings
    storage: StorageSettings
    market: MarketSettings
    export: ExportSettings
    features: FeatureSettings
    backtest: BacktestSettings
    raw: dict[str, Any]


def _env_value(name: str, current: Any) -> Any:
    value = os.getenv(name)
    if value is None or value == "":
        return current
    return value


def _env_int(name: str, current: Any) -> int | None:
    value = os.getenv(name)
    if value is None or value == "":
        return current
    return int(value)


def load_config(path: str | Path) -> ResearchConfig:
    config_path = Path(path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    base_dir = config_path.parent.parent

    symbol = _env_value("TRADINGO_SYMBOL", data["symbol"])
    mt5_data = data["mt5"]
    storage_data = data["storage"]
    market_data = data["market"]
    export_data = data["export"]
    feature_data = data["features"]
    backtest_data = data["backtest"]

    mt5 = MT5Settings(
        terminal_path=_env_value("TRADINGO_MT5_PATH", mt5_data.get("terminal_path")),
        login=_env_int("TRADINGO_MT5_LOGIN", mt5_data.get("login")),
        password=_env_value("TRADINGO_MT5_PASSWORD", mt5_data.get("password")),
        server=_env_value("TRADINGO_MT5_SERVER", mt5_data.get("server")),
        initialize_timeout_ms=int(mt5_data.get("initialize_timeout_ms", 60000)),
    )

    storage = StorageSettings(
        root=_resolve_path(base_dir, storage_data["root"]),
        ticks_dir=_resolve_path(base_dir, storage_data["ticks_dir"]),
        rates_dir=_resolve_path(base_dir, storage_data["rates_dir"]),
        bars_dir=_resolve_path(base_dir, storage_data["bars_dir"]),
        reports_dir=_resolve_path(base_dir, storage_data["reports_dir"]),
    )

    return ResearchConfig(
        symbol=str(symbol),
        broker=str(data.get("broker", "")),
        mt5=mt5,
        storage=storage,
        market=MarketSettings(
            expected_digits=int(market_data["expected_digits"]),
            default_point=float(market_data["default_point"]),
            target_points=int(market_data["target_points"]),
            stop_points=int(market_data["stop_points"]),
            max_spread_points=int(market_data["max_spread_points"]),
        ),
        export=ExportSettings(
            tick_chunk_days=int(export_data["tick_chunk_days"]),
            rates_timeframe=str(export_data["rates_timeframe"]),
        ),
        features=FeatureSettings(
            rsi_period=int(feature_data["rsi_period"]),
            ema_fast=int(feature_data["ema_fast"]),
            ema_mid=int(feature_data["ema_mid"]),
            ema_slow=int(feature_data["ema_slow"]),
            atr_period=int(feature_data["atr_period"]),
            adx_period=int(feature_data["adx_period"]),
            momentum_period=int(feature_data["momentum_period"]),
            vwap_period=int(feature_data["vwap_period"]),
        ),
        backtest=BacktestSettings(
            horizon_bars=int(backtest_data["horizon_bars"]),
            slippage_points=int(backtest_data["slippage_points"]),
            commission_per_lot_round_turn=float(backtest_data["commission_per_lot_round_turn"]),
        ),
        raw=data,
    )


def ensure_storage_dirs(config: ResearchConfig) -> None:
    for directory in (
        config.storage.root,
        config.storage.ticks_dir,
        config.storage.rates_dir,
        config.storage.bars_dir,
        config.storage.reports_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()
