"""Monitor shadow dei setup Sertorio: legge state/sertorio_zones.json (scritto dal bridge),
prende i prezzi dal terminale MT5 (pacchetto MetaTrader5, sola lettura) e registra fill/TP/SL
virtuali in state/sertorio_shadow.json + journal/sertorio_shadow.csv. Nessun ordine reale.

  python tools/sertorio_shadow.py --config C:\\TG_TradinGo\\tradingo_config.json [--terminal <path terminal64.exe>] [--poll 2]

Processo separato dal bridge: non usa Telegram.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sertorio_zones import (
    SHADOW_CSV_FILE,
    SHADOW_STATE_FILE,
    ZONES_FILE,
    Setup,
    SetupConfig,
    ShadowBook,
    build_open_signal,
    live_signal_paths,
    write_live_signal,
)

log = logging.getLogger("tradingo.sertorio.shadow")


class Mt5Feed:
    """Tick dal terminale MT5. Se il terminale viene riavviato il modulo MetaTrader5 resta
    collegato a un processo morto e risponde None senza errori: qui lo rileviamo e ci ricolleghiamo."""

    def __init__(self, terminal: str | None, symbol: str, reconnect_after: int = 15):
        import MetaTrader5 as mt5  # type: ignore
        self.mt5 = mt5
        self.terminal = terminal
        self.symbol = symbol
        self.reconnect_after = reconnect_after
        self.misses = 0
        self.last_tick_time = 0
        self._connect(first=True)

    def _connect(self, first: bool = False) -> None:
        mt5 = self.mt5
        if not first:
            with contextlib.suppress(Exception):
                mt5.shutdown()
        ok = mt5.initialize(path=self.terminal) if self.terminal else mt5.initialize()
        if not ok:
            raise RuntimeError(f"MT5 initialize fallita: {mt5.last_error()}")
        mt5.symbol_select(self.symbol, True)
        info, acc = mt5.terminal_info(), mt5.account_info()
        log.info(f"MT5 {'connesso' if first else 'RICONNESSO'}: {getattr(info, 'name', '?')} "
                 f"account={getattr(acc, 'login', '?')} symbol={self.symbol}")
        self.misses = 0

    def tick(self) -> tuple[datetime, float, float] | None:
        t = self.mt5.symbol_info_tick(self.symbol)
        alive = self.mt5.terminal_info() is not None
        if not alive or not t or not t.bid:
            self.misses += 1
            if not alive or self.misses >= self.reconnect_after:
                log.error(f"MT5 non risponde (terminal_info={'ok' if alive else 'None'}, "
                          f"tick mancanti={self.misses}): riconnessione")
                try:
                    self._connect()
                except Exception as e:  # noqa: BLE001
                    log.error(f"riconnessione MT5 fallita: {e}")
                    self.misses = 0
            return None
        self.misses = 0
        self.last_tick_time = t.time
        return datetime.fromtimestamp(t.time, tz=timezone.utc), float(t.bid), float(t.ask)


def load_doc(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def sync_setups(book: ShadowBook, doc: dict, seen: str | None, now: datetime) -> str | None:
    """Nuova analisi -> sostituisce i setup pendenti (le gambe aperte restano fino a SL/TP)."""
    aid = doc.get("analysis_id")
    if not aid or aid == seen:
        return seen
    setups = [Setup(**s) for s in doc.get("setups", [])]
    book.replace_setups(setups, now)
    log.info(f"analisi {aid}: {len(setups)} setup in osservazione")
    return aid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--terminal", default=None, help="path terminal64.exe (default: ultimo usato da MT5)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--poll", type=float, default=2.0)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    base = Path(cfg["paths"]["base"])
    state_dir = Path(cfg["paths"].get("state", base / "state"))
    ch = next((c for c in cfg.get("channels", []) if c.get("parser") == "sertorio_zones"), {})
    scfg = SetupConfig.from_channel(ch)
    logs = Path(cfg["paths"].get("logs", base / "logs"))
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(logs / "sertorio_shadow.log", encoding="utf-8")])
    live_paths = [] if scfg.shadow else live_signal_paths(cfg, scfg)
    on_fill = None
    if live_paths:
        def on_fill(setup: dict, fill: float, ts: datetime) -> None:
            sig = build_open_signal(setup, fill, ch, scfg, ts)
            write_live_signal(live_paths, ch.get("signal_file", "signal_ch_sertorio.json"), sig)
        log.warning(f"LIVE: ordini reali al tocco verso {[str(x) for x in live_paths]} (lotto da preset EA)")
    elif not scfg.shadow:
        log.error("shadow=false ma nessuna mt5_instances corrisponde a setups.live_instances: resto in shadow")
    book = ShadowBook(scfg, state_dir / SHADOW_STATE_FILE, base / "journal" / SHADOW_CSV_FILE, on_fill=on_fill)
    feed = Mt5Feed(args.terminal, args.symbol)
    zones_path = state_dir / ZONES_FILE
    seen = (load_doc(state_dir / SHADOW_STATE_FILE) or {}).get("analysis_id")
    last_mtime = 0.0
    log.info(f"shadow attivo: {len(book.pending)} pendenti, {len(book.open)} gambe aperte, ultima analisi {seen}")
    while True:
        try:
            if zones_path.exists():
                m = zones_path.stat().st_mtime
                if m != last_mtime:
                    last_mtime = m
                    doc = load_doc(zones_path)
                    if doc:
                        seen = sync_setups(book, doc, seen, datetime.now(timezone.utc))
                        book.save()
            tk = feed.tick()
            if tk:
                book.on_tick(*tk)
            book.extra = {"analysis_id": seen}
        except Exception as e:  # noqa: BLE001
            log.error(f"loop: {e}")
        time.sleep(args.poll)


if __name__ == "__main__":
    main()
