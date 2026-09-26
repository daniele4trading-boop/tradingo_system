"""Avvio: ``python -m dx_executor --config dx_executor_config.json [--once]``."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from . import __version__
from .config import load_config
from .dxclient import DXTradeClient, DXTradeError
from .executor import Executor
from .journal import Journal


def _setup_logging(log_dir: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        handlers.append(
            TimedRotatingFileHandler(Path(log_dir) / "dx_executor.log", when="midnight", backupCount=14, encoding="utf-8")
        )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=handlers
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dx_executor")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true", help="un solo ciclo (diagnostica)")
    ap.add_argument("--check", action="store_true", help="solo login + quote + metriche, poi esce")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    _setup_logging(cfg.paths.log_dir)
    log = logging.getLogger("dx_executor.main")
    try:
        user, pwd = cfg.broker.credentials()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 2
    client = DXTradeClient(cfg.broker.base_url, user, pwd, cfg.broker.account, cfg.broker.domain)
    client.login()
    symbol = next(iter(cfg.broker.symbol_map.values()))
    log.info("dx_executor %s mode=%s account=%s symbol=%s", __version__, cfg.execution.mode, cfg.broker.account, symbol)

    if args.check:
        q = client.quote(symbol)
        m = client.metrics()
        inst = client.instrument(symbol)
        log.info("quote %s bid=%s ask=%s spread=%.2f", symbol, q.bid, q.ask, q.spread)
        log.info("metrics equity=%.2f balance=%.2f openPL=%.2f positions=%d", m.equity, m.balance, m.open_pl, m.open_positions)
        log.info("instrument priceInc=%s qtyInc=%s lotSize=%s", inst.price_increment, inst.quantity_increment, inst.lot_size)
        return 0

    executor = Executor(cfg, client, Journal(cfg.state_dir))
    log.info("watch %s (paper=%s)", cfg.signals_dir / cfg.paths.signal_file, not executor.live)
    while True:
        try:
            executor.tick()
        except DXTradeError as exc:
            log.error("errore broker: %s", exc)
        except Exception:
            log.exception("errore non gestito nel ciclo")
        if args.once:
            return 0
        time.sleep(cfg.execution.poll_sec)


if __name__ == "__main__":
    raise SystemExit(main())
