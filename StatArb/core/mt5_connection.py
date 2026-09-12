from __future__ import annotations

import importlib
import time
from types import ModuleType
from typing import Any

from core.config import AccountProfile
from core.logging_setup import setup_logging


class Mt5ConnectionError(RuntimeError):
    """Raised when a MetaTrader 5 terminal cannot be initialized."""


def import_mt5() -> ModuleType:
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise Mt5ConnectionError(
            "Modulo Python 'MetaTrader5' non installato. Esegui preflight.py nella .venv dedicata."
        ) from exc


class Mt5Session:
    """Context manager for one MT5 profile in one Python process.

    Caveat operativo: chiudere manualmente i terminali MT5 prima di lanciare i processi
    Python. Il pacchetto MetaTrader5 puo' fallire con "authorization failed" se un
    terminale e' gia' aperto con stato/sessione incompatibile.

    Per vincolo del pacchetto MetaTrader5, un singolo processo Python deve connettersi a
    un solo terminale MT5 alla volta. Usare processi separati per data_source, leg_A e leg_B.
    """

    def __init__(
        self,
        profile: AccountProfile,
        *,
        retries: int = 3,
        retry_delay_seconds: float = 3.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.profile = profile
        self.retries = retries
        self.retry_delay_seconds = retry_delay_seconds
        self.timeout_seconds = timeout_seconds
        self.mt5: ModuleType | None = None
        self.logger = setup_logging("statarb.mt5_connection")

    def __enter__(self) -> ModuleType:
        self.mt5 = import_mt5()
        deadline = time.monotonic() + self.timeout_seconds
        last_error: Any = None

        for attempt in range(1, self.retries + 1):
            if time.monotonic() > deadline:
                break

            self.logger.info(
                "Connessione MT5 profilo=%s server=%s path=%s attempt=%s/%s",
                self.profile.name,
                self.profile.server,
                self.profile.path,
                attempt,
                self.retries,
            )
            initialized = self.mt5.initialize(
                path=self.profile.path,
                login=self.profile.login,
                password=self.profile.password,
                server=self.profile.server,
            )
            if initialized:
                account_info = self.mt5.account_info()
                if account_info is None:
                    last_error = self.mt5.last_error()
                    self.mt5.shutdown()
                else:
                    self.logger.info(
                        "Connesso MT5 profilo=%s login=%s broker=%s",
                        self.profile.name,
                        account_info.login,
                        account_info.company,
                    )
                    return self.mt5
            else:
                last_error = self.mt5.last_error()

            self.logger.warning(
                "Connessione MT5 fallita profilo=%s attempt=%s errore=%s",
                self.profile.name,
                attempt,
                last_error,
            )
            if attempt < self.retries:
                time.sleep(self.retry_delay_seconds)

        raise Mt5ConnectionError(
            f"Impossibile connettere MT5 profilo={self.profile.name} "
            f"dopo {self.retries} tentativi. last_error={last_error}"
        )

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.mt5 is not None:
            self.logger.info("Shutdown MT5 profilo=%s", self.profile.name)
            self.mt5.shutdown()
            self.mt5 = None
