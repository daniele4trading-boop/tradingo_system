"""Supervisore dei servizi TradinGo: processo vivo **e** heartbeat fresco.

Il Task Scheduler della Contabo oggi riavvia il bridge solo se il processo non
esiste (`run_bridge_task.cmd` conta i `python.exe` e esce se ne trova uno). Un
processo bloccato — event loop fermo, sessione scaduta, share di rete appesa —
resta "vivo" per il Task Scheduler e non viene mai riavviato.

Questo modulo aggiunge il pezzo mancante: legge l'heartbeat che il servizio
scrive su disco e riavvia quando è vecchio, con tre protezioni che su un
sistema che manda ordini contano più del riavvio stesso:

* **grazia all'avvio**: dopo un riavvio l'heartbeat non viene valutato finché
  il servizio non ha avuto il tempo di scriverlo;
* **anti-flapping**: oltre N riavvii in un'ora smette di riavviare e alza un
  allarme, invece di entrare in un ciclo di riavvii su un guasto persistente;
* **kill per PID**: i processi si terminano con il PID trovato dalla sonda,
  mai con un match sul nome, per non spegnere processi omonimi.

Uso:

    python service_watchdog.py --config watchdog_config.json --once
    python service_watchdog.py --config watchdog_config.json --dry-run

Pensato per girare dal Task Scheduler con ripetizione ogni minuto, come già
fa `TG_TradinGo_BridgeAtLogon`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

log = logging.getLogger("TradinGo.watchdog")

# Esiti della valutazione.
OK = "OK"
WAIT_GRACE = "WAIT_GRACE"
RESTART_DEAD = "RESTART_PROCESS_DEAD"
RESTART_STALE = "RESTART_HEARTBEAT_STALE"
ALERT_FLAPPING = "ALERT_FLAPPING"
ALERT_NO_START = "ALERT_NO_START_COMMAND"

FLAP_WINDOW_SEC = 3600.0


@dataclass
class ServiceSpec:
    name: str
    start_command: list[str] | None = None
    process_match: str | None = None
    heartbeat_file: str | None = None
    heartbeat_field: str = "ts_utc"
    max_age_sec: float = 180.0
    grace_sec: float = 120.0
    max_restarts_per_hour: int = 4
    working_dir: str | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "ServiceSpec":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"campi non riconosciuti in '{data.get('name')}': {sorted(unknown)}")
        if not data.get("name"):
            raise ValueError("ogni servizio deve avere un name")
        return cls(**data)


@dataclass
class ServiceState:
    started_at: float | None = None
    restarts: list[float] = field(default_factory=list)

    def recent_restarts(self, now: float, window: float = FLAP_WINDOW_SEC) -> int:
        return sum(1 for ts in self.restarts if now - ts <= window)

    def prune(self, now: float, window: float = FLAP_WINDOW_SEC) -> None:
        self.restarts = [ts for ts in self.restarts if now - ts <= window]


@dataclass
class Decision:
    action: str
    reason: str
    restart: bool = False
    alert: bool = False


# ── nucleo decisionale (puro, testabile) ──────────────────────────────────


def decide(
    spec: ServiceSpec,
    now: float,
    process_alive: bool,
    heartbeat_age: float | None,
    state: ServiceState,
) -> Decision:
    """Cosa fare adesso per questo servizio.

    ``heartbeat_age`` è in secondi, oppure ``None`` se il file manca o non è
    leggibile — che dopo la grazia vale come heartbeat fermo.
    """
    flapping = state.recent_restarts(now) >= spec.max_restarts_per_hour

    if not process_alive:
        if not spec.start_command:
            return Decision(ALERT_NO_START, "processo assente e nessun comando di avvio", alert=True)
        if flapping:
            return Decision(
                ALERT_FLAPPING,
                f"processo assente ma già {state.recent_restarts(now)} riavvii nell'ultima ora",
                alert=True,
            )
        return Decision(RESTART_DEAD, "processo non attivo", restart=True, alert=True)

    if not spec.heartbeat_file:
        return Decision(OK, "processo attivo (nessun heartbeat configurato)")

    if state.started_at is not None and now - state.started_at < spec.grace_sec:
        return Decision(
            WAIT_GRACE,
            f"avviato da {now - state.started_at:.0f}s, grazia {spec.grace_sec:.0f}s",
        )

    if heartbeat_age is None:
        reason = "heartbeat assente o illeggibile"
    elif heartbeat_age > spec.max_age_sec:
        reason = f"heartbeat vecchio di {heartbeat_age:.0f}s (limite {spec.max_age_sec:.0f}s)"
    else:
        return Decision(OK, f"heartbeat fresco ({heartbeat_age:.0f}s)")

    if flapping:
        return Decision(
            ALERT_FLAPPING,
            f"{reason}, ma già {state.recent_restarts(now)} riavvii nell'ultima ora",
            alert=True,
        )
    return Decision(RESTART_STALE, reason, restart=True, alert=True)


def heartbeat_age(path: str | None, field_name: str, now: float) -> float | None:
    """Età in secondi dell'heartbeat, o ``None`` se non determinabile.

    Si usa il timestamp scritto **dentro** il file, non l'mtime: su share di
    rete l'mtime può essere aggiornato da una copia senza che il servizio sia
    vivo davvero.
    """
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = payload.get(field_name)
    if not raw:
        return None
    try:
        stamp = datetime.strptime(str(raw), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, now - stamp.timestamp())


# ── sonde di sistema ──────────────────────────────────────────────────────


PATTERN_ENV = "TG_WATCHDOG_PATTERN"


def probe_command(pattern: str, system: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Comando per cercare i PID, più le variabili d'ambiente da passargli.

    Il pattern viaggia **in una variabile d'ambiente, mai nella riga di
    comando**: su Windows la sonda elenca le command line di tutti i processi
    e finirebbe per trovare se stessa, riportando "processo attivo" anche per
    un servizio morto.
    """
    system = system or platform.system()
    env = {PATTERN_ENV: pattern}
    if system == "Windows":
        script = (
            f"$p = $env:{PATTERN_ENV}; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.CommandLine -match $p } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        return ["powershell", "-NoProfile", "-Command", script], env
    return ["pgrep", "-f", pattern], env


def find_pids(pattern: str) -> list[int]:
    """PID dei processi la cui riga di comando corrisponde a ``pattern``."""
    if not pattern:
        return []
    cmd, extra_env = probe_command(pattern)
    env = {**os.environ, **extra_env}
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit() and int(line) != os.getpid():
            pids.append(int(line))
    return pids


def kill_pids(pids: Sequence[int]) -> None:
    """Termina per PID. Mai per nome: si rischia di spegnere processi terzi."""
    for pid in pids:
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, timeout=30)
            else:
                os.kill(pid, 15)
            log.info("terminato pid %s", pid)
        except (OSError, subprocess.SubprocessError) as err:
            log.warning("impossibile terminare pid %s: %s", pid, err)


def spawn(command: Sequence[str], working_dir: str | None) -> None:
    kwargs: dict = {"cwd": working_dir} if working_dir else {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(list(command), **kwargs)


# ── supervisore ───────────────────────────────────────────────────────────


class Watchdog:
    def __init__(
        self,
        specs: Sequence[ServiceSpec],
        state_file: str | Path,
        dry_run: bool = False,
        alert_command: Sequence[str] | None = None,
        pid_probe: Callable[[str], list[int]] = find_pids,
        killer: Callable[[Sequence[int]], None] = kill_pids,
        starter: Callable[[Sequence[str], str | None], None] = spawn,
        clock: Callable[[], float] = time.time,
    ):
        self.specs = [s for s in specs if s.enabled]
        self.state_file = Path(state_file)
        self.dry_run = dry_run
        self.alert_command = list(alert_command) if alert_command else None
        self.pid_probe = pid_probe
        self.killer = killer
        self.starter = starter
        self.clock = clock
        self.states: dict[str, ServiceState] = self._load_state()

    def _load_state(self) -> dict[str, ServiceState]:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {
            name: ServiceState(
                started_at=value.get("started_at"),
                restarts=list(value.get("restarts") or []),
            )
            for name, value in raw.items()
        }

    def _save_state(self) -> None:
        if self.dry_run:
            return
        payload = {
            name: {"started_at": st.started_at, "restarts": st.restarts}
            for name, st in self.states.items()
        }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError as err:
            log.warning("stato non salvato: %s", err)

    def alert(self, message: str) -> None:
        log.warning("ALERT %s", message)
        if not self.alert_command or self.dry_run:
            return
        try:
            subprocess.run([*self.alert_command, message], capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as err:
            log.warning("alert non inviato: %s", err)

    def check(self, spec: ServiceSpec) -> Decision:
        now = self.clock()
        state = self.states.setdefault(spec.name, ServiceState())
        state.prune(now)

        pids = self.pid_probe(spec.process_match) if spec.process_match else []
        alive = bool(pids)
        age = heartbeat_age(spec.heartbeat_file, spec.heartbeat_field, now)
        decision = decide(spec, now, alive, age, state)

        log.info("[%s] %s — %s", spec.name, decision.action, decision.reason)

        if decision.restart and not self.dry_run:
            if pids:
                self.killer(pids)
            if spec.start_command:
                self.starter(spec.start_command, spec.working_dir)
            state.restarts.append(now)
            state.started_at = now
        elif decision.restart and self.dry_run:
            log.info("[%s] DRY-RUN: riavvio non eseguito", spec.name)

        if decision.alert:
            self.alert(f"[TradinGo watchdog] {spec.name}: {decision.action} — {decision.reason}")
        return decision

    def run_once(self) -> dict[str, Decision]:
        results = {spec.name: self.check(spec) for spec in self.specs}
        self._save_state()
        return results


def load_config(path: str | Path) -> tuple[list[ServiceSpec], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = [ServiceSpec.from_dict(item) for item in data.get("services", [])]
    return specs, data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervisore dei servizi TradinGo")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", default=None, help="file di stato (default: accanto al config)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true", help="un solo giro (default)")
    parser.add_argument("--interval", type=float, default=0.0,
                        help="se > 0 resta in esecuzione e ricontrolla ogni N secondi")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        specs, data = load_config(args.config)
    except (OSError, ValueError) as err:
        print(f"Config non valido: {err}", file=sys.stderr)
        return 2
    if not specs:
        print("Nessun servizio abilitato nel config", file=sys.stderr)
        return 1

    state_file = args.state or data.get("state_file") or (Path(args.config).with_name("watchdog_state.json"))
    dog = Watchdog(
        specs,
        state_file,
        dry_run=args.dry_run,
        alert_command=data.get("alert_command"),
    )

    if args.interval > 0 and not args.once:
        log.info("watchdog in esecuzione, intervallo %.0fs", args.interval)
        while True:
            dog.run_once()
            time.sleep(args.interval)
    else:
        dog.run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
