"""Test del supervisore dei servizi."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from service_watchdog import (
    ALERT_FLAPPING,
    ALERT_NO_START,
    OK,
    RESTART_DEAD,
    RESTART_STALE,
    WAIT_GRACE,
    ServiceSpec,
    ServiceState,
    Watchdog,
    decide,
    find_pids,
    heartbeat_age,
    load_config,
    main,
    probe_command,
)

NOW = 1_800_000_000.0

BRIDGE = ServiceSpec(
    name="tradingo_bridge",
    process_match=r"tradingo_bridge\.py",
    start_command=["cmd", "/c", "run_bridge_task.cmd"],
    heartbeat_file="hb.json",
    max_age_sec=180.0,
    grace_sec=120.0,
    max_restarts_per_hour=4,
)


def running(started_ago: float = 10_000.0, restarts: list[float] | None = None) -> ServiceState:
    return ServiceState(started_at=NOW - started_ago, restarts=list(restarts or []))


# ── nucleo decisionale ────────────────────────────────────────────────────


def test_healthy_service_is_left_alone():
    decision = decide(BRIDGE, NOW, process_alive=True, heartbeat_age=30.0, state=running())
    assert decision.action == OK
    assert (decision.restart, decision.alert) == (False, False)


def test_dead_process_is_restarted():
    decision = decide(BRIDGE, NOW, process_alive=False, heartbeat_age=5.0, state=running())
    assert decision.action == RESTART_DEAD
    assert decision.restart is True
    assert decision.alert is True


def test_alive_but_stale_heartbeat_is_restarted():
    """Il buco che il Task Scheduler non copre: processo vivo, bridge fermo."""
    decision = decide(BRIDGE, NOW, process_alive=True, heartbeat_age=600.0, state=running())
    assert decision.action == RESTART_STALE
    assert decision.restart is True
    assert "600s" in decision.reason


def test_missing_heartbeat_file_counts_as_stale():
    decision = decide(BRIDGE, NOW, process_alive=True, heartbeat_age=None, state=running())
    assert decision.action == RESTART_STALE
    assert "assente" in decision.reason


def test_heartbeat_exactly_at_the_limit_is_still_healthy():
    decision = decide(BRIDGE, NOW, process_alive=True, heartbeat_age=180.0, state=running())
    assert decision.action == OK


def test_grace_period_prevents_a_restart_loop_at_startup():
    just_started = ServiceState(started_at=NOW - 30.0)
    decision = decide(BRIDGE, NOW, process_alive=True, heartbeat_age=None, state=just_started)
    assert decision.action == WAIT_GRACE
    assert decision.restart is False


def test_grace_period_does_not_protect_a_dead_process():
    just_started = ServiceState(started_at=NOW - 30.0)
    decision = decide(BRIDGE, NOW, process_alive=False, heartbeat_age=None, state=just_started)
    assert decision.action == RESTART_DEAD


def test_flapping_stops_restarts_and_alerts_instead():
    hot = running(restarts=[NOW - 100, NOW - 200, NOW - 300, NOW - 400])
    decision = decide(BRIDGE, NOW, process_alive=False, heartbeat_age=None, state=hot)
    assert decision.action == ALERT_FLAPPING
    assert decision.restart is False
    assert decision.alert is True


def test_old_restarts_fall_out_of_the_flapping_window():
    old = running(restarts=[NOW - 7200, NOW - 7300, NOW - 7400, NOW - 7500])
    old.prune(NOW)
    assert old.recent_restarts(NOW) == 0
    assert decide(BRIDGE, NOW, False, None, old).action == RESTART_DEAD


def test_service_without_start_command_only_alerts():
    spec = ServiceSpec(name="manuale", process_match="x", start_command=None)
    decision = decide(spec, NOW, process_alive=False, heartbeat_age=None, state=running())
    assert decision.action == ALERT_NO_START
    assert decision.restart is False


def test_service_without_heartbeat_is_judged_only_on_the_process():
    spec = ServiceSpec(name="webapp", process_match="webapp", start_command=["x"])
    assert decide(spec, NOW, True, None, running()).action == OK
    assert decide(spec, NOW, False, None, running()).action == RESTART_DEAD


# ── lettura heartbeat ─────────────────────────────────────────────────────


def test_heartbeat_age_reads_the_timestamp_inside_the_file(tmp_path):
    path = tmp_path / "hb.json"
    path.write_text(
        json.dumps({"ts_utc": "2026-08-04T08:30:34Z", "source": "tradingo_bridge"}),
        encoding="utf-8",
    )
    stamp = 1785832234.0  # 2026-08-04T08:30:34Z
    assert heartbeat_age(str(path), "ts_utc", stamp + 45) == pytest.approx(45.0)


def test_heartbeat_age_accepts_iso_with_offset(tmp_path):
    path = tmp_path / "hb.json"
    path.write_text(json.dumps({"ts_utc": "2026-08-04T08:30:34+00:00"}), encoding="utf-8")
    assert heartbeat_age(str(path), "ts_utc", 1785832234.0 + 10) == pytest.approx(10.0)


def test_heartbeat_age_is_none_for_missing_or_broken_file(tmp_path):
    assert heartbeat_age(str(tmp_path / "nope.json"), "ts_utc", NOW) is None
    broken = tmp_path / "broken.json"
    broken.write_text("non json", encoding="utf-8")
    assert heartbeat_age(str(broken), "ts_utc", NOW) is None
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"altro": 1}), encoding="utf-8")
    assert heartbeat_age(str(empty), "ts_utc", NOW) is None
    assert heartbeat_age(None, "ts_utc", NOW) is None


def test_heartbeat_age_never_returns_a_negative_value(tmp_path):
    """Orologi sfasati tra bridge e watchdog non devono dare età negative."""
    path = tmp_path / "hb.json"
    path.write_text(json.dumps({"ts_utc": "2026-08-04T08:30:34Z"}), encoding="utf-8")
    assert heartbeat_age(str(path), "ts_utc", 1785832234.0 - 60) == 0.0


# ── sonda dei processi ────────────────────────────────────────────────────


def test_probe_command_never_puts_the_pattern_on_the_command_line():
    """Su Windows la sonda legge le command line altrui: se il pattern fosse
    nella propria, troverebbe se stessa e ogni servizio risulterebbe vivo."""
    argv, env = probe_command(r"tradingo_bridge\.py", system="Windows")
    assert all(r"tradingo_bridge\.py" not in arg for arg in argv)
    assert env["TG_WATCHDOG_PATTERN"] == r"tradingo_bridge\.py"
    assert "$env:TG_WATCHDOG_PATTERN" in argv[-1]


def test_probe_command_on_posix_uses_pgrep():
    argv, env = probe_command("qualcosa", system="Linux")
    assert argv == ["pgrep", "-f", "qualcosa"]
    assert env["TG_WATCHDOG_PATTERN"] == "qualcosa"


def test_find_pids_does_not_match_its_own_probe():
    assert find_pids("pattern-che-non-esiste-in-nessun-processo-9f3a") == []


def test_find_pids_ignores_an_empty_pattern():
    assert find_pids("") == []


# ── supervisore completo ──────────────────────────────────────────────────


class Recorder:
    def __init__(self, pids=(), now=NOW):
        self.pids = list(pids)
        self.killed: list[int] = []
        self.started: list[tuple] = []
        self.alerts: list[str] = []
        self.now = now

    def probe(self, pattern):
        return list(self.pids)

    def kill(self, pids):
        self.killed.extend(pids)
        self.pids = []

    def start(self, command, cwd):
        self.started.append((tuple(command), cwd))
        self.pids = [4242]

    def clock(self):
        return self.now


def make_watchdog(tmp_path, rec, dry_run=False, heartbeat=None, **overrides):
    spec = ServiceSpec(
        name="tradingo_bridge",
        process_match=r"tradingo_bridge\.py",
        start_command=["cmd", "/c", "run_bridge_task.cmd"],
        heartbeat_file=str(heartbeat) if heartbeat else None,
        max_age_sec=180.0,
        grace_sec=120.0,
        **overrides,
    )
    dog = Watchdog(
        [spec],
        state_file=tmp_path / "state.json",
        dry_run=dry_run,
        pid_probe=rec.probe,
        killer=rec.kill,
        starter=rec.start,
        clock=rec.clock,
    )
    dog.alert = lambda message: rec.alerts.append(message)
    return dog


def write_heartbeat(tmp_path, age_sec, now=NOW):
    from datetime import datetime, timezone

    stamp = datetime.fromtimestamp(now - age_sec, tz=timezone.utc)
    path = tmp_path / "hb.json"
    path.write_text(
        json.dumps({"ts_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ")}), encoding="utf-8"
    )
    return path


def test_watchdog_leaves_a_healthy_service_running(tmp_path):
    rec = Recorder(pids=[100])
    dog = make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 20))
    assert dog.run_once()["tradingo_bridge"].action == OK
    assert rec.killed == [] and rec.started == [] and rec.alerts == []


def test_watchdog_restarts_a_dead_service(tmp_path):
    rec = Recorder(pids=[])
    dog = make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 20))
    assert dog.run_once()["tradingo_bridge"].action == RESTART_DEAD
    assert rec.started == [(("cmd", "/c", "run_bridge_task.cmd"), None)]
    assert len(rec.alerts) == 1


def test_watchdog_kills_the_hung_process_by_pid_before_restarting(tmp_path):
    rec = Recorder(pids=[777])
    dog = make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 900))
    assert dog.run_once()["tradingo_bridge"].action == RESTART_STALE
    assert rec.killed == [777]
    assert len(rec.started) == 1


def test_watchdog_persists_state_across_runs(tmp_path):
    rec = Recorder(pids=[])
    make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 20)).run_once()
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["tradingo_bridge"]["started_at"] == NOW
    assert saved["tradingo_bridge"]["restarts"] == [NOW]

    # secondo giro: il servizio e' ripartito ed e' nel periodo di grazia
    rec2 = Recorder(pids=[4242], now=NOW + 30)
    again = make_watchdog(tmp_path, rec2, heartbeat=write_heartbeat(tmp_path, 900, now=NOW))
    assert again.run_once()["tradingo_bridge"].action == WAIT_GRACE
    assert rec2.killed == []


def test_watchdog_stops_after_repeated_restarts(tmp_path):
    rec = Recorder(pids=[])
    for _ in range(4):
        make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 900)).run_once()
        rec.pids = []
    assert len(rec.started) == 4

    final = make_watchdog(tmp_path, rec, heartbeat=write_heartbeat(tmp_path, 900))
    decision = final.run_once()["tradingo_bridge"]
    assert decision.action == ALERT_FLAPPING
    assert len(rec.started) == 4  # nessun quinto riavvio
    assert "riavvii nell'ultima ora" in decision.reason


def test_dry_run_decides_but_does_not_touch_anything(tmp_path):
    rec = Recorder(pids=[555])
    dog = make_watchdog(tmp_path, rec, dry_run=True, heartbeat=write_heartbeat(tmp_path, 900))
    assert dog.run_once()["tradingo_bridge"].action == RESTART_STALE
    assert rec.killed == [] and rec.started == []
    assert not (tmp_path / "state.json").exists()


# ── configurazione ────────────────────────────────────────────────────────


def test_example_config_loads():
    specs, data = load_config(Path(__file__).resolve().parents[1] / "watchdog_config.example.json")
    names = [s.name for s in specs]
    assert "tradingo_bridge" in names
    bridge = next(s for s in specs if s.name == "tradingo_bridge")
    assert bridge.heartbeat_field == "ts_utc"
    assert bridge.max_age_sec == 180
    assert data["state_file"].endswith("watchdog_state.json")


def test_config_rejects_unknown_fields(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"services": [{"name": "x", "max_age": 10}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="max_age"):
        load_config(path)


def test_config_requires_a_name(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"services": [{"process_match": "x"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="name"):
        load_config(path)


def test_cli_dry_run_on_the_example_config(tmp_path, capsys):
    config = tmp_path / "cfg.json"
    config.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "name": "assente",
                        "process_match": "processo-che-non-esiste-xyz",
                        "start_command": ["echo", "ciao"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert main(["--config", str(config), "--dry-run", "--once"]) == 0


def test_cli_reports_a_broken_config(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{ non json", encoding="utf-8")
    assert main(["--config", str(bad)]) == 2
    assert "Config non valido" in capsys.readouterr().err
