"""Configurazione Pattern GO: tutto da file JSON, niente valori hardcodati."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from .risk import RiskConfig
from .strategy import EntryFilters, SessionSpec


@dataclass(frozen=True)
class StrategyConfig:
    name: str
    timeframe: str
    enabled: bool
    bias_window: int
    max_hold: int
    day_bars: int
    filters: EntryFilters
    tp_fallback_r: float = 1.5
    tp_min_r: float = 0.5
    tp_swing_mode: str = "last_before_entry"


@dataclass(frozen=True)
class BrokerConfig:
    base_url: str
    domain: str
    account: str
    username: str
    password: str
    symbol: str
    timeout: float = 20.0
    max_retries: int = 5


@dataclass(frozen=True)
class RuntimeConfig:
    poll_seconds: float = 10.0
    heartbeat_seconds: float = 900.0
    warmup_bars: int = 400
    kill_switch_file: str = "KILL_SWITCH"
    kill_switch_closes_positions: bool = True
    startup_retry_max_seconds: float = 300.0
    resume_open_trades: bool = True
    close_unknown_positions: bool = True
    log_dir: str = "logs"
    state_file: str = "state/pattern_go_state.json"
    report_dir: str = "reports"
    log_max_bytes: int = 20_000_000
    log_backup_count: int = 30


@dataclass(frozen=True)
class Config:
    broker: BrokerConfig
    risk: RiskConfig
    runtime: RuntimeConfig
    strategies: list[StrategyConfig]
    sessions: list[SessionSpec] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _resolve_secret(raw: dict[str, Any], key: str) -> str:
    """Legge un segreto da variabile d'ambiente (`*_env`) o dal file di config."""
    env_key = raw.get(f"{key}_env")
    if env_key:
        value = os.environ.get(env_key)
        if not value:
            raise ValueError(f"variabile d'ambiente {env_key} non impostata")
        return value
    value = raw.get(key)
    if not value:
        raise ValueError(f"broker.{key} mancante nella configurazione")
    return str(value)


class ConfigError(ValueError):
    """Configurazione non valida: il servizio non deve partire."""


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"sezione '{name}' mancante o non valida nella configurazione")
    return section


def validate(cfg: Config) -> Config:
    """Blocca all'avvio le configurazioni che produrrebbero ordini insensati.

    Senza questi controlli una `risk_fraction` negativa arriva fino in fondo: il
    rischio calcolato e' negativo, ma `round_below_min_to_min` lo trasforma comunque
    in un ordine al lotto minimo.
    """
    r = cfg.risk
    problems: list[str] = []
    if not 0 < r.risk_fraction <= 1:
        problems.append(f"risk.risk_fraction deve stare in (0, 1], trovato {r.risk_fraction}")
    for name, value in (
        ("max_dd_pct", r.max_dd_pct),
        ("daily_loss_pct", r.daily_loss_pct),
    ):
        if not 0 < value < 1:
            problems.append(f"risk.{name} deve stare in (0, 1), trovato {value}")
    for name, value in (
        ("block_new_at_allowance_used", r.block_new_at_allowance_used),
        ("close_all_at_allowance_used", r.close_all_at_allowance_used),
    ):
        if not 0 < value <= 1:
            problems.append(f"risk.{name} deve stare in (0, 1], trovato {value}")
    if r.block_new_at_allowance_used > r.close_all_at_allowance_used:
        problems.append(
            "risk.block_new_at_allowance_used deve precedere close_all_at_allowance_used"
        )
    if r.initial_balance <= 0:
        problems.append(
            f"account.initial_balance deve essere positivo, trovato {r.initial_balance}"
        )
    if r.cap_size < r.initial_balance:
        problems.append(
            f"account.cap_size ({r.cap_size}) non puo' essere sotto initial_balance "
            f"({r.initial_balance}): il serbatoio sarebbe nullo"
        )
    if not 0 < r.max_margin_utilisation <= 1:
        problems.append(
            "risk.max_margin_utilisation deve stare in (0, 1], "
            f"trovato {r.max_margin_utilisation}"
        )
    if r.margin_rate <= 0:
        problems.append(f"risk.margin_rate deve essere positivo, trovato {r.margin_rate}")
    if r.min_quantity <= 0 or r.quantity_increment <= 0:
        problems.append("risk.min_quantity e risk.quantity_increment devono essere positivi")
    if r.max_open_positions < 1 or r.max_open_positions_per_strategy < 1:
        problems.append("i limiti di posizioni aperte devono essere almeno 1")
    if not cfg.strategies:
        problems.append("nessuna strategia configurata")
    if not cfg.sessions:
        problems.append("nessuna sessione configurata")
    for s in cfg.strategies:
        if min(s.bias_window, s.max_hold, s.day_bars) < 1:
            problems.append(f"strategie.{s.name}: bias_window/max_hold/day_bars devono essere >= 1")
        if s.tp_fallback_r <= 0:
            problems.append(f"strategie.{s.name}: tp_fallback_r deve essere positivo")
    if problems:
        raise ConfigError("configurazione non valida:\n- " + "\n- ".join(problems))
    return cfg


def load_config(path: str | Path) -> Config:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    b = _section(raw, "broker")
    broker = BrokerConfig(
        base_url=b["base_url"],
        domain=b.get("domain", "default"),
        account=b["account"],
        username=_resolve_secret(b, "username"),
        password=_resolve_secret(b, "password"),
        symbol=b["symbol"],
        timeout=float(b.get("timeout", 20.0)),
        max_retries=int(b.get("max_retries", 5)),
    )

    r = _section(raw, "risk")
    account = _section(raw, "account")
    risk = RiskConfig(
        initial_balance=float(account["initial_balance"]),
        cap_size=float(account["cap_size"]),
        max_dd_pct=float(r["max_dd_pct"]),
        daily_loss_pct=float(r["daily_loss_pct"]),
        daily_reset_utc=_parse_time(r.get("daily_reset_utc", "00:30")),
        risk_fraction=float(r["risk_fraction"]),
        block_new_at_allowance_used=float(r.get("block_new_at_allowance_used", 0.60)),
        close_all_at_allowance_used=float(r.get("close_all_at_allowance_used", 0.80)),
        max_open_positions=int(r.get("max_open_positions", 2)),
        max_open_positions_per_strategy=int(r.get("max_open_positions_per_strategy", 1)),
        min_quantity=float(r.get("min_quantity", 0.01)),
        quantity_increment=float(r.get("quantity_increment", 0.01)),
        round_below_min_to_min=bool(r.get("round_below_min_to_min", True)),
        margin_rate=float(r.get("margin_rate", 0.16666)),
        max_margin_utilisation=float(r.get("max_margin_utilisation", 0.5)),
    )

    runtime = RuntimeConfig(**raw.get("runtime", {}))

    strategies = []
    for name, s in _section(raw, "strategies").items():
        f = s.get("filters", {})
        strategies.append(
            StrategyConfig(
                name=name,
                timeframe=s.get("timeframe", name),
                enabled=bool(s.get("enabled", True)),
                bias_window=int(s["bias_window"]),
                max_hold=int(s["max_hold"]),
                day_bars=int(s["day_bars"]),
                filters=EntryFilters(
                    body2_frac_min=f.get("body2_frac_min"),
                    risk_spread_mult_min=f.get("risk_spread_mult_min"),
                    range2_over_range1_min=f.get("range2_over_range1_min"),
                    range2_over_range1_max=f.get("range2_over_range1_max"),
                    min_stop_distance=f.get("min_stop_distance"),
                ),
                tp_fallback_r=float(s.get("tp_fallback_r", 1.5)),
                tp_min_r=float(s.get("tp_min_r", 0.5)),
                tp_swing_mode=s.get("tp_swing_mode", "last_before_entry"),
            )
        )

    sessions = [
        SessionSpec(name=s["name"], open_time=_parse_time(s["time"]), tz=s["tz"])
        for s in raw["sessions"]
    ]

    return validate(
        Config(
            broker=broker,
            risk=risk,
            runtime=runtime,
            strategies=strategies,
            sessions=sessions,
            raw=raw,
        )
    )
