"""Background status collector for the monitoring dashboard.

All checks run in a daemon thread with per-check timeouts so a hung SMB/UNC
path can never block the web server. The web layer only reads the cached
snapshot.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from datetime import datetime, timezone
from pathlib import Path

from webapp.pnl import build_magic_map, build_phase2, normalize_channel

log = logging.getLogger("TradinGoWeb")

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_CRIT = "crit"
STATUS_OFF = "off"  # check disabled / not configured

_RANK = {STATUS_OK: 0, STATUS_OFF: 0, STATUS_WARN: 1, STATUS_CRIT: 2}

IS_WINDOWS = platform.system() == "Windows"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_from_heartbeat(path: Path, timeout_sec: float) -> tuple[int | None, str | None]:
    """Return (age_sec, error). Runs the read in a worker to survive SMB hangs."""

    def read() -> int | None:
        raw = path.read_text(encoding="utf-8")
        ts = _parse_ts(json.loads(raw).get("ts_utc"))
        if ts is None:
            return None
        return int((_utcnow() - ts).total_seconds())

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(read)
        try:
            return fut.result(timeout=timeout_sec), None
        except FutTimeout:
            return None, f"timeout {timeout_sec}s"
        except FileNotFoundError:
            return None, "file mancante"
        except Exception as exc:  # noqa: BLE001 - report any I/O problem as detail
            return None, f"{type(exc).__name__}: {exc}"


def _ping(host: str, timeout_sec: int = 2) -> bool:
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_sec * 1000), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout_sec), host]
    try:
        res = subprocess.run(
            cmd, capture_output=True, timeout=timeout_sec + 2, check=False
        )
        return res.returncode == 0
    except Exception:
        return False


def _process_running(image_name: str) -> bool | None:
    """True/False, or None when the check cannot run on this platform."""
    try:
        if IS_WINDOWS:
            res = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            return image_name.lower() in (res.stdout or "").lower()
        res = subprocess.run(
            ["pgrep", "-f", image_name], capture_output=True, timeout=10, check=False
        )
        return res.returncode == 0
    except Exception:
        return None


def _newest_mtime(directory: Path, timeout_sec: float) -> float | None:
    def scan() -> float | None:
        newest: float | None = None
        for entry in directory.rglob("*"):
            if not entry.is_file():
                continue
            mt = entry.stat().st_mtime
            if newest is None or mt > newest:
                newest = mt
        return newest

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(scan)
        try:
            return fut.result(timeout=timeout_sec)
        except Exception:
            return None


class Collector:
    def __init__(self, webapp_cfg: dict):
        self.cfg = webapp_cfg
        self.checks = webapp_cfg.get("checks", {})
        self.refresh_sec = int(webapp_cfg.get("refresh_seconds", 15))
        self._snapshot: dict = {"generated_utc": None, "lights": {}, "channels": [], "events": []}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tradingo_cfg_mtime: float = 0.0
        self._tradingo_cfg = self._load_tradingo_config()
        self.accounts = self._load_accounts()

    # ── config ───────────────────────────────────────────────────────────

    def _load_accounts(self) -> list[dict]:
        """Accounts to report on.

        `accounts` in webapp_config wins; without it the legacy single
        `checks.ea_journal_dir` becomes the only account, so an old config keeps
        working unchanged.
        """
        raw = self.cfg.get("accounts")
        if isinstance(raw, list) and raw:
            out = []
            for idx, acc in enumerate(raw):
                if not acc.get("ea_journal_dir"):
                    continue
                out.append(
                    {
                        "id": acc.get("id") or f"acc{idx + 1}",
                        "label": acc.get("label") or acc.get("id") or f"Conto {idx + 1}",
                        "ea_journal_dir": acc["ea_journal_dir"],
                        "signal_stats": acc.get("signal_stats"),
                        "start_date": acc.get("start_date"),
                    }
                )
            if out:
                return out
        return [
            {
                "id": "main",
                "label": self.checks.get("ea_journal_label", "Conto locale"),
                "ea_journal_dir": self.checks.get("ea_journal_dir"),
                "signal_stats": self.checks.get("signal_stats"),
                "start_date": None,
            }
        ]

    def _load_tradingo_config(self) -> dict:
        path = self.cfg.get("tradingo_config")
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:
            log.warning("cannot read tradingo_config %s: %s", path, exc)
            return {}

    def _maybe_reload_tradingo_cfg(self) -> None:
        """Rilegge tradingo_config solo se il file è stato modificato."""
        path = self.cfg.get("tradingo_config")
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
            if mtime != self._tradingo_cfg_mtime:
                with open(path, "r", encoding="utf-8") as fh:
                    self._tradingo_cfg = json.load(fh)
                self._tradingo_cfg_mtime = mtime
                log.info("tradingo_config ricaricato da %s", path)
        except Exception as exc:
            log.warning("cannot reload tradingo_config %s: %s", path, exc)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="collector")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._snapshot)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                snap = self.collect_once()
                with self._lock:
                    self._snapshot = snap
            except Exception as exc:  # noqa: BLE001 - collector must survive anything
                log.error("collector error: %s", exc)
            self._stop.wait(self.refresh_sec)

    # ── checks ───────────────────────────────────────────────────────────

    def collect_once(self) -> dict:
        self._maybe_reload_tradingo_cfg()
        magic_map = build_magic_map(self._tradingo_cfg.get("channels", []))

        lights: dict[str, dict] = {}
        lights["bridge"] = self._check_bridge_heartbeat()
        lights["mt5_local"] = self._check_mt5_local()
        lights["friend_link"] = self._check_friend_link()
        lights["friend_heartbeat"] = self._check_friend_heartbeat()

        channels, events = self._read_journal_today()
        accounts = [self._build_account(acc, magic_map=magic_map) for acc in self.accounts]
        # The first account drives the legacy top-level fields and the per-channel PnL.
        phase2 = accounts[0]["data"] if accounts else self._empty_phase2("no account")
        self._merge_pnl_into_channels(channels, phase2)

        worst = STATUS_OK
        for lt in lights.values():
            if _RANK.get(lt["status"], 1) > _RANK.get(worst, 0):
                worst = lt["status"]

        mt5_terminals = self._discover_mt5_terminals()
        alerts = [
            f"MT5 spento ma dati presenti: {t['broker_name']} ({t['terminal_id'][:8]}…)"
            for t in mt5_terminals if not t["running"] and t["has_data"]
        ]

        return {
            "generated_utc": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "refresh_seconds": self.refresh_sec,
            "overall": worst,
            "lights": lights,
            "channels": channels,
            "events": events,
            "phase": 2,
            "pnl": phase2.get("pnl"),
            "open_positions": phase2.get("open_positions") or [],
            "equity": phase2.get("equity"),
            "exec": phase2.get("exec"),
            "sources": phase2.get("sources"),
            "mt5_terminals": mt5_terminals,
            "alerts": alerts,
            "accounts": [
                {
                    "id": acc["id"],
                    "label": acc["label"],
                    "start_date": acc["start_date"],
                    "pnl": acc["data"].get("pnl"),
                    "open_positions": acc["data"].get("open_positions") or [],
                    "equity": acc["data"].get("equity"),
                    "exec": acc["data"].get("exec"),
                    "sources": acc["data"].get("sources"),
                }
                for acc in accounts
            ],
        }

    def _build_account(self, acc: dict, magic_map: dict | None = None) -> dict:
        return {**acc, "data": self._build_phase2(acc, magic_map=magic_map)}

    @staticmethod
    def _empty_phase2(error: str) -> dict:
        return {
            "pnl": None,
            "open_positions": [],
            "equity": {"points": [], "latest": None, "delta_today": None},
            "exec": {"totals": {}, "by_channel": {}},
            "sources": {"error": error},
        }

    def _build_phase2(self, acc: dict, magic_map: dict | None = None) -> dict:
        try:
            return build_phase2(
                ea_journal_dir=acc.get("ea_journal_dir"),
                signal_stats_path=acc.get("signal_stats"),
                lookback_days=int(self.cfg.get("pnl_lookback_days", 30)),
                equity_days=int(self.cfg.get("equity_days", 3)),
                start_date=acc.get("start_date"),
                magic_map=magic_map,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("phase2 build failed for %s: %s", acc.get("id"), exc)
            return self._empty_phase2(str(exc))

    @staticmethod
    def _merge_pnl_into_channels(channels: list[dict], phase2: dict) -> None:
        pnl = (phase2 or {}).get("pnl") or {}
        by_ch = pnl.get("by_channel") or {}
        exec_by = ((phase2 or {}).get("exec") or {}).get("by_channel") or {}
        for ch in channels:
            cid = ch["id"]
            # also try short tag
            short = cid.replace("CH_", "")
            bucket = by_ch.get(cid) or by_ch.get(normalize_channel(short)) or {}
            ch["pnl"] = {
                "today": bucket.get("d1") or {
                    "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "win_rate": None
                },
                "d7": bucket.get("d7") or {
                    "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "win_rate": None
                },
                "d30": bucket.get("d30") or {
                    "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "win_rate": None
                },
            }
            ex = exec_by.get(cid) or exec_by.get(normalize_channel(short)) or {}
            ch["exec"] = {
                "executed": ex.get("executed", 0),
                "cancelled": ex.get("cancelled", 0),
            }

    def _hb_status(self, age: int | None, err: str | None) -> dict:
        warn = int(self.checks.get("heartbeat_warn_sec", 90))
        crit = int(self.checks.get("heartbeat_crit_sec", 180))
        if err:
            return {"status": STATUS_CRIT, "detail": err, "age_sec": None}
        if age is None:
            return {"status": STATUS_CRIT, "detail": "timestamp illeggibile", "age_sec": None}
        if age <= warn:
            return {"status": STATUS_OK, "detail": f"heartbeat {age}s fa", "age_sec": age}
        if age <= crit:
            return {"status": STATUS_WARN, "detail": f"heartbeat {age}s fa", "age_sec": age}
        return {"status": STATUS_CRIT, "detail": f"heartbeat vecchio {age}s", "age_sec": age}

    def _check_bridge_heartbeat(self) -> dict:
        path = self.checks.get("bridge_heartbeat")
        if not path:
            return {"status": STATUS_OFF, "detail": "check disabilitato"}
        age, err = _age_from_heartbeat(Path(path), timeout_sec=5)
        out = self._hb_status(age, err)
        out["label"] = "Bridge Telegram"
        return out

    def _check_mt5_local(self) -> dict:
        image = self.checks.get("mt5_process", "terminal64.exe")
        ea_dir = self.checks.get("ea_journal_dir")
        if not image and not ea_dir:
            return {"status": STATUS_OFF, "detail": "check disabilitato", "label": "MT5 locale"}

        details: list[str] = []
        status = STATUS_OK

        if image:
            running = _process_running(image)
            if running is None:
                details.append("processo: n/d")
                status = STATUS_WARN
            elif running:
                details.append("terminale attivo")
            else:
                details.append("terminale NON attivo")
                status = STATUS_CRIT

        if ea_dir and status != STATUS_CRIT:
            stale_sec = int(self.checks.get("ea_journal_stale_sec", 300))
            newest = _newest_mtime(Path(ea_dir), timeout_sec=5)
            if newest is None:
                details.append("journal EA assente")
                status = STATUS_WARN if status == STATUS_OK else status
            else:
                age = int(time.time() - newest)
                if age > stale_sec:
                    details.append(f"journal EA fermo {age}s")
                    status = STATUS_WARN if status == STATUS_OK else status
                else:
                    details.append(f"journal EA {age}s fa")

        return {"status": status, "detail": " · ".join(details), "label": "MT5 locale (Moneta/Vantage)"}

    def _check_friend_link(self) -> dict:
        host = self.checks.get("friend_host")
        share = self.checks.get("friend_share")
        if not host and not share:
            return {"status": STATUS_OFF, "detail": "check disabilitato", "label": "Link Gamehosting"}

        details: list[str] = []
        status = STATUS_OK

        if host:
            if _ping(host):
                details.append("ping ok")
            else:
                details.append("ping KO")
                status = STATUS_CRIT

        if share and status != STATUS_CRIT:
            def probe() -> bool:
                return os.path.isdir(share)

            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(probe)
                try:
                    reachable = fut.result(timeout=5)
                except Exception:
                    reachable = False
            if reachable:
                details.append("share ok")
            else:
                details.append("share KO")
                status = STATUS_CRIT

        return {"status": status, "detail": " · ".join(details), "label": "Link Gamehosting"}

    def _check_friend_heartbeat(self) -> dict:
        path = self.checks.get("friend_heartbeat")
        if not path:
            return {"status": STATUS_OFF, "detail": "check disabilitato", "label": "Heartbeat su share"}
        age, err = _age_from_heartbeat(Path(path), timeout_sec=5)
        out = self._hb_status(age, err)
        out["label"] = "Heartbeat su share amico"
        return out

    # ── MT5 auto-discovery ───────────────────────────────────────────────

    def _discover_mt5_terminals(self) -> list[dict]:
        """Scansiona i terminali MT5 installati e rileva stato running/data."""
        base = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal")
        if not base.exists():
            return []
        results: list[dict] = []
        skip = {"Common", "Community"}
        for sub in base.iterdir():
            if not sub.is_dir() or sub.name in skip:
                continue
            terminal_id = sub.name
            # broker name da origin.txt
            broker_name = terminal_id
            try:
                origin_path = sub / "origin.txt"
                if origin_path.exists():
                    broker_name = origin_path.read_text(encoding="utf-8-sig").split("\n")[0].strip() or terminal_id
            except Exception:
                pass
            # has_data: verifica presenza di CSV in MQL5/Files/journal/trades/
            trades_dir = sub / "MQL5" / "Files" / "journal" / "trades"
            has_data = False
            try:
                if trades_dir.exists():
                    has_data = any(trades_dir.glob("*.csv"))
            except Exception:
                pass
            # running: cerca terminal64.exe con path contenente terminal_id
            running = False
            try:
                res = subprocess.run(
                    ["wmic", "process", "where",
                     f"ExecutablePath like '%{terminal_id}%'",
                     "get", "ProcessId"],
                    capture_output=True, text=True, timeout=8, check=False,
                )
                # Se c'è almeno un PID numerico nell'output, il processo gira
                lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip().isdigit()]
                running = len(lines) > 0
            except Exception:
                pass
            results.append({
                "terminal_id": terminal_id,
                "broker_name": broker_name,
                "running": running,
                "has_data": has_data,
                "origin_path": str(sub / "origin.txt"),
            })
        return results

    # ── journal / channels ───────────────────────────────────────────────

    def _read_journal_today(self) -> tuple[list[dict], list[dict]]:
        channels_cfg = self._tradingo_cfg.get("channels", [])
        journal_dir = self.checks.get("journal_dir")
        max_events = int(self.cfg.get("max_events", 20))

        per_channel: dict[str, dict] = {}
        for ch in channels_cfg:
            per_channel[ch["id"]] = {
                "id": ch["id"],
                "name": ch.get("name", ch["id"]),
                "enabled": bool(ch.get("enabled", True)),
                "today": {"emitted": 0, "unparsed": 0, "ignored": 0, "errors": 0, "duplicates": 0},
                "last_event": None,
                "last_emitted": None,
            }

        events: list[dict] = []
        if journal_dir:
            day = _utcnow().strftime("%Y%m%d")
            jpath = Path(journal_dir) / f"events_{day}.jsonl"
            rows = self._read_jsonl(jpath)
            for row in rows:
                cid = row.get("channel_id")
                slot = per_channel.get(cid)
                brief = {
                    "ts_utc": row.get("ts_utc"),
                    "channel_id": cid,
                    "event_type": row.get("event_type"),
                    "outcome": row.get("outcome"),
                    "action": row.get("action"),
                    "signal_id": row.get("signal_id"),
                    "raw_text": (row.get("raw_text") or "")[:120],
                }
                events.append(brief)
                if slot is None:
                    continue
                outcome = row.get("outcome")
                t = slot["today"]
                if outcome == "EMITTED":
                    t["emitted"] += 1
                    slot["last_emitted"] = brief
                elif outcome == "UNPARSED":
                    t["unparsed"] += 1
                elif outcome == "IGNORED_PATTERN":
                    t["ignored"] += 1
                elif outcome == "PARSE_ERROR":
                    t["errors"] += 1
                elif outcome == "DUPLICATE":
                    t["duplicates"] += 1
                slot["last_event"] = brief

        events = events[-max_events:][::-1]
        return list(per_channel.values()), events

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict]:
        def read() -> list[dict]:
            out: list[dict] = []
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return out

        if not path.exists():
            return []
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(read)
            try:
                return fut.result(timeout=5)
            except Exception:
                return []
