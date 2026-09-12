from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from .config import ResearchConfig


TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


@dataclass(frozen=True)
class MT5ConnectionInfo:
    login: int | None
    server: str | None
    name: str | None
    company: str | None
    balance: float | None
    equity: float | None


def connect_mt5(config: ResearchConfig):
    """Initialize MT5. Import is delayed so non-Windows CI can still import modules."""
    mt5 = importlib.import_module("MetaTrader5")
    kwargs: dict[str, Any] = {"timeout": config.mt5.initialize_timeout_ms}
    if config.mt5.terminal_path:
        kwargs["path"] = config.mt5.terminal_path
    if config.mt5.login:
        kwargs["login"] = int(config.mt5.login)
    if config.mt5.password:
        kwargs["password"] = config.mt5.password
    if config.mt5.server:
        kwargs["server"] = config.mt5.server

    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    account = mt5.account_info()
    if account is None:
        last_error = mt5.last_error()
        mt5.shutdown()
        raise RuntimeError(f"MT5 account_info failed after initialize: {last_error}")
    return mt5


def get_connection_info(mt5) -> MT5ConnectionInfo:
    account = mt5.account_info()
    if account is None:
        return MT5ConnectionInfo(None, None, None, None, None, None)
    return MT5ConnectionInfo(
        login=getattr(account, "login", None),
        server=getattr(account, "server", None),
        name=getattr(account, "name", None),
        company=getattr(account, "company", None),
        balance=getattr(account, "balance", None),
        equity=getattr(account, "equity", None),
    )


def ensure_symbol(mt5, symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol not found in MT5: {symbol}")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Unable to select symbol in Market Watch: {symbol}")
    return mt5.symbol_info(symbol)


def export_symbol_metadata(mt5, config: ResearchConfig) -> Path:
    info = ensure_symbol(mt5, config.symbol)
    tick = mt5.symbol_info_tick(config.symbol)
    payload = {
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "connection": asdict(get_connection_info(mt5)),
        "symbol": info._asdict() if hasattr(info, "_asdict") else str(info),
        "last_tick": tick._asdict() if tick is not None and hasattr(tick, "_asdict") else None,
    }
    output_dir = config.storage.root / "metadata"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{config.symbol}_mt5_metadata.json"
    output_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return output_path


def export_ticks(
    mt5,
    config: ResearchConfig,
    start: datetime,
    end: datetime,
) -> list[Path]:
    info = ensure_symbol(mt5, config.symbol)
    point = float(getattr(info, "point", config.market.default_point) or config.market.default_point)
    output_paths: list[Path] = []

    for chunk_start, chunk_end in iter_chunks(start, end, days=config.export.tick_chunk_days):
        ticks = mt5.copy_ticks_range(config.symbol, chunk_start, chunk_end, mt5.COPY_TICKS_ALL)
        if ticks is None:
            raise RuntimeError(
                f"copy_ticks_range returned None for {config.symbol} "
                f"{chunk_start.isoformat()} -> {chunk_end.isoformat()}: {mt5.last_error()}"
            )
        if len(ticks) == 0:
            continue

        df = pd.DataFrame(ticks)
        df = normalize_ticks(df, point)
        path = tick_partition_path(config, chunk_start)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        output_paths.append(path)

    return output_paths


def export_rates(
    mt5,
    config: ResearchConfig,
    start: datetime,
    end: datetime,
    timeframe: str | None = None,
) -> Path:
    ensure_symbol(mt5, config.symbol)
    timeframe = timeframe or config.export.rates_timeframe
    mt5_timeframe = resolve_timeframe(mt5, timeframe)
    rates = mt5.copy_rates_range(config.symbol, mt5_timeframe, start, end)
    if rates is None:
        raise RuntimeError(
            f"copy_rates_range returned None for {config.symbol} {timeframe}: {mt5.last_error()}"
        )
    df = pd.DataFrame(rates)
    if not df.empty:
        df["time_utc"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df[
            [
                "time_utc",
                "time",
                "open",
                "high",
                "low",
                "close",
                "tick_volume",
                "spread",
                "real_volume",
            ]
        ]

    output_dir = config.storage.rates_dir / config.symbol / timeframe.upper()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{start:%Y%m%d}_{end:%Y%m%d}.parquet"
    df.to_parquet(output_path, index=False)
    return output_path


def normalize_ticks(df: pd.DataFrame, point: float) -> pd.DataFrame:
    if df.empty:
        return df

    result = df.copy()
    if "time_msc" in result.columns:
        result["time_utc"] = pd.to_datetime(result["time_msc"], unit="ms", utc=True)
    else:
        result["time_utc"] = pd.to_datetime(result["time"], unit="s", utc=True)

    if "ask" in result.columns and "bid" in result.columns:
        result["mid"] = (result["bid"] + result["ask"]) / 2.0
        result["spread"] = result["ask"] - result["bid"]
        result["spread_points"] = result["spread"] / point

    preferred = [
        "time_utc",
        "time",
        "time_msc",
        "bid",
        "ask",
        "mid",
        "last",
        "volume",
        "volume_real",
        "flags",
        "spread",
        "spread_points",
    ]
    return result[[col for col in preferred if col in result.columns]]


def resolve_timeframe(mt5, timeframe: str) -> int:
    name = TIMEFRAMES.get(timeframe.upper())
    if not name:
        raise ValueError(f"Unsupported timeframe {timeframe}. Supported: {sorted(TIMEFRAMES)}")
    return getattr(mt5, name)


def iter_chunks(start: datetime, end: datetime, days: int) -> Iterable[tuple[datetime, datetime]]:
    if days < 1:
        raise ValueError("days must be >= 1")
    current = ensure_utc(start)
    final = ensure_utc(end)
    while current < final:
        chunk_end = min(current + timedelta(days=days), final)
        yield current, chunk_end
        current = chunk_end


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def tick_partition_path(config: ResearchConfig, chunk_start: datetime) -> Path:
    return (
        config.storage.ticks_dir
        / config.symbol
        / f"{chunk_start:%Y}"
        / f"{chunk_start:%m}"
        / f"{chunk_start:%Y-%m-%d}.parquet"
    )
