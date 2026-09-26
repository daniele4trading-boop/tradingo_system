"""Configurazione dell'executor (file JSON, credenziali solo da environment)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BrokerConfig:
    base_url: str = "https://dx.velotrade.com/dxsca-web"
    domain: str = "default"
    account: str = ""
    username_env: str = "DXTRADE_USERNAME"
    password_env: str = "DXTRADE_PASSWORD"
    symbol_map: dict[str, str] = field(default_factory=lambda: {"XAUUSD": "XAU"})

    def credentials(self) -> tuple[str, str]:
        user = os.environ.get(self.username_env, "")
        pwd = os.environ.get(self.password_env, "")
        if not user or not pwd:
            raise RuntimeError(
                f"credenziali DXtrade mancanti: imposta {self.username_env} e {self.password_env}"
            )
        return user, pwd


@dataclass(frozen=True)
class ExecutionConfig:
    # "paper": simula fill e SL/TP sui prezzi reali, nessun ordine al broker.
    # "live": invia ordini. Il passaggio a live richiede anche ``live_orders_ack``.
    mode: str = "paper"
    live_orders_ack: str = ""
    quantity_per_tp: float = 0.01
    quantity_increment: float = 0.01
    max_legs_per_setup: int = 4
    # Tolleranza fuori zona in valuta (l'EA usa 200 punti MT5 = 2.0 $ sull'oro).
    off_market_tolerance: float = 2.0
    # Livelli oltre questa % dal prezzo sono errori di battitura (come l'EA).
    max_level_deviation_pct: float = 2.0
    # Uno SL che allarga il rischio è seguito al massimo fino a rischio×fattore.
    max_sl_widen_factor: float = 2.0
    # BE all'entry del segnale solo se entro questa distanza dal fill (500 pt MT5).
    be_signal_entry_max_gap: float = 5.0
    # Finestra che raggruppa le posizioni in un "blocco" (CLOSE_SELECTIVE).
    batch_window_sec: int = 120
    # Ordini SL/TP separati sul broker; se rifiutati si passa al soft stop.
    hard_protection: bool = True
    poll_sec: float = 2.0


@dataclass(frozen=True)
class RiskConfig:
    initial_balance: float = 0.0
    daily_loss_pct: float = 0.0  # 0 = off
    max_dd_pct: float = 0.0  # 0 = off
    block_new_at_allowance_used: float = 0.6
    close_all_at_allowance_used: float = 0.9
    max_open_legs: int = 8
    heartbeat_max_age_sec: int = 180


@dataclass(frozen=True)
class PathsConfig:
    signals_dir: str = ""
    signal_file: str = "signal_ch_ivan.json"
    heartbeat_file: str = "tradingo_heartbeat.json"
    killswitch_file: str = "dx_executor.killswitch"
    state_dir: str = ""
    log_dir: str = ""


@dataclass(frozen=True)
class ExecutorConfig:
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)

    @property
    def is_live(self) -> bool:
        return self.execution.mode == "live" and self.execution.live_orders_ack == "I_AUTHORIZE_LIVE_ORDERS"

    @property
    def signals_dir(self) -> Path:
        return Path(self.paths.signals_dir)

    @property
    def state_dir(self) -> Path:
        return Path(self.paths.state_dir or (self.signals_dir / "dx_state"))


def load_config(path: str | Path) -> ExecutorConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return config_from_dict(raw)


def config_from_dict(raw: dict) -> ExecutorConfig:
    cfg = ExecutorConfig(
        broker=BrokerConfig(**raw.get("broker", {})),
        execution=ExecutionConfig(**raw.get("execution", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        paths=PathsConfig(**raw.get("paths", {})),
    )
    if cfg.execution.mode not in ("paper", "live"):
        raise ValueError(f"execution.mode non valido: {cfg.execution.mode}")
    if not cfg.paths.signals_dir:
        raise ValueError("paths.signals_dir obbligatorio")
    if cfg.execution.quantity_per_tp < cfg.execution.quantity_increment:
        raise ValueError("quantity_per_tp sotto l'incremento minimo")
    return cfg
