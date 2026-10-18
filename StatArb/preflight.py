from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import textwrap
import venv
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
STATE_DIR = PROJECT_ROOT / "state"
VENV_DIR = PROJECT_ROOT / ".venv"
PORT = 8520
WINDOWS_TARGET_ROOT = Path("C:/StatArb")

REQUIRED_DIRS = (
    "core",
    "scanner",
    "engine",
    "execution",
    "risk",
    "ui",
    "backtest",
    "launch",
    "state",
    "state/statarb_logs",
)

REQUIRED_FILES = (
    "accounts.json",
    "params.json",
    "requirements.txt",
    "preflight.py",
    "core/config.py",
    "core/mt5_connection.py",
    "core/symbols.py",
    "core/logging_setup.py",
)


@dataclass
class CheckResult:
    name: str
    status: str
    details: str


def _ansi(text: str, code: str, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def _status_label(status: str, use_color: bool) -> str:
    colors = {"OK": "32", "WARN": "33", "FAIL": "31"}
    return _ansi(f"[{status}]", colors.get(status, "0"), use_color)


def ensure_tree() -> CheckResult:
    created: list[str] = []
    for relative_dir in REQUIRED_DIRS:
        path = PROJECT_ROOT / relative_dir
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(relative_dir)
    details = "cartelle gia' presenti" if not created else f"create: {', '.join(created)}"
    return CheckResult("Albero cartelle isolato", "OK", details)


def check_required_files() -> CheckResult:
    missing = [relative for relative in REQUIRED_FILES if not (PROJECT_ROOT / relative).exists()]
    if missing:
        return CheckResult("File bootstrap/core richiesti", "FAIL", f"mancanti: {', '.join(missing)}")
    return CheckResult("File bootstrap/core richiesti", "OK", "tutti presenti")


def ensure_venv() -> CheckResult:
    if not VENV_DIR.exists():
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
        except BaseException as exc:  # noqa: BLE001 - venv may raise SystemExit if ensurepip is absent.
            return CheckResult("Virtual environment dedicato", "FAIL", str(exc))
        return CheckResult("Virtual environment dedicato", "OK", f"creato: {VENV_DIR}")
    python_path = venv_python()
    if not python_path.exists():
        return CheckResult("Virtual environment dedicato", "FAIL", f"python non trovato: {python_path}")
    return CheckResult("Virtual environment dedicato", "OK", f"presente: {VENV_DIR}")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def install_requirements(force_install: bool = False) -> CheckResult:
    requirements = PROJECT_ROOT / "requirements.txt"
    if not requirements.exists():
        return CheckResult("Installazione requirements", "FAIL", "requirements.txt mancante")

    if os.name != "nt" and not force_install:
        return CheckResult(
            "Installazione requirements",
            "WARN",
            "saltata su host non-Windows; MetaTrader5 e' verificabile sulla VPS Windows target",
        )

    python_path = venv_python()
    if not python_path.exists():
        return CheckResult("Installazione requirements", "FAIL", f"python venv mancante: {python_path}")

    command = [str(python_path), "-m", "pip", "install", "-r", str(requirements)]
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        return CheckResult(
            "Installazione requirements",
            "FAIL",
            completed.stdout.strip()[-1000:] or f"exit_code={completed.returncode}",
        )
    return CheckResult("Installazione requirements", "OK", "requirements installati nella .venv")


def check_project_root() -> CheckResult:
    if os.name == "nt":
        try:
            same_root = PROJECT_ROOT.resolve() == WINDOWS_TARGET_ROOT.resolve()
        except OSError:
            same_root = False
        if not same_root:
            return CheckResult(
                "Root isolamento C:\\StatArb",
                "FAIL",
                f"root corrente={PROJECT_ROOT}; atteso={WINDOWS_TARGET_ROOT}",
            )
        return CheckResult("Root isolamento C:\\StatArb", "OK", str(PROJECT_ROOT))
    return CheckResult(
        "Root isolamento C:\\StatArb",
        "WARN",
        f"host non-Windows: sorgenti in {PROJECT_ROOT}; runtime target C:\\StatArb",
    )


def check_port_free(port: int = PORT) -> CheckResult:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        result = sock.connect_ex(("127.0.0.1", port))
    if result == 0:
        return CheckResult(f"Porta Streamlit {port}", "FAIL", "porta occupata su 127.0.0.1")
    return CheckResult(f"Porta Streamlit {port}", "OK", "libera")


def list_python_processes() -> CheckResult:
    if os.name == "nt":
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -match 'python' } | "
                "Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize"
            ),
        ]
    else:
        command = ["ps", "-eo", "pid=,comm=,args="]

    try:
        completed = subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=10,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics must not hide platform failures.
        return CheckResult("Processi Python attivi", "WARN", f"impossibile elencare: {exc}")

    output = completed.stdout.strip()
    if os.name != "nt":
        lines = [
            line.strip()
            for line in output.splitlines()
            if "python" in line.lower() and "preflight.py" not in line
        ]
        output = "\n".join(lines)

    if not output:
        return CheckResult("Processi Python attivi", "OK", "nessun altro processo Python rilevato")

    log_dir = STATE_DIR / "statarb_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "statarb_preflight_processes.log"
    log_path.write_text(output + "\n", encoding="utf-8")
    return CheckResult(
        "Processi Python attivi",
        "WARN",
        f"rilevati e loggati in {log_path}; nessun processo terminato",
    )


def validate_config() -> CheckResult:
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from core.config import load_all

        load_all()
    except Exception as exc:  # noqa: BLE001 - show exact validation error to the operator.
        return CheckResult("Validazione accounts/params", "FAIL", str(exc))
    return CheckResult("Validazione accounts/params", "OK", "schema valido")


def validate_terminal_paths() -> CheckResult:
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from core.config import load_accounts

        accounts = load_accounts()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Path terminali MT5", "FAIL", f"config non caricabile: {exc}")

    missing: list[str] = []
    for profile_name, profile in accounts["profiles"].items():
        terminal_path = Path(profile["path"])
        if not terminal_path.exists():
            missing.append(f"{profile_name}={profile['path']}")

    if missing:
        return CheckResult("Path terminali MT5", "FAIL", "mancanti: " + "; ".join(missing))
    return CheckResult("Path terminali MT5", "OK", "tutti i terminali configurati esistono")


def render_report(results: list[CheckResult], use_color: bool = True) -> None:
    print("\nSTATARB PREFLIGHT REPORT")
    print("=" * 80)
    for result in results:
        label = _status_label(result.status, use_color)
        print(f"{label} {result.name}")
        wrapped = textwrap.wrap(result.details, width=74) or [""]
        for line in wrapped:
            print(f"     {line}")
    print("=" * 80)
    failed = sum(1 for result in results if result.status == "FAIL")
    warnings = sum(1 for result in results if result.status == "WARN")
    print(f"Totale: {len(results)} controlli | FAIL={failed} | WARN={warnings}")


def run(force_install: bool = False, use_color: bool = True) -> int:
    results = [
        ensure_tree(),
        check_required_files(),
        ensure_venv(),
        install_requirements(force_install=force_install),
        check_project_root(),
        check_port_free(),
        list_python_processes(),
        validate_config(),
        validate_terminal_paths(),
    ]
    render_report(results, use_color=use_color)
    return 1 if any(result.status == "FAIL" for result in results) else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preflight isolamento StatArb.")
    parser.add_argument(
        "--force-install",
        action="store_true",
        help="Forza pip install anche su host non-Windows.",
    )
    parser.add_argument("--no-color", action="store_true", help="Disabilita colori ANSI.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(run(force_install=args.force_install, use_color=not args.no_color))
