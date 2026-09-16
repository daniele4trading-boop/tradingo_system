from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .schema import schema_registry


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return lines


NOTES_PATH = Path(__file__).resolve().parents[1] / "NOTES_DUKASCOPY.md"


def _fmt(value: object, digits: int = 3) -> object:
    return round(value, digits) if isinstance(value, float) else value


def _notes(path: Path) -> list[str]:
    if not path.exists():
        return ["Nessuna nota (file NOTES_DUKASCOPY.md assente)."]
    return path.read_text().strip().splitlines()


def write_reports(
    events, cfg, leakage: dict, missing: dict, inventory: dict | None = None,
    s1_summary: dict | None = None, s2_summary: dict | None = None,
) -> None:
    root = Path(cfg.reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    specs = schema_registry(list(events.columns))
    schema_rows = [
        [
            s.name, s.definition, s.unit, s.source, s.proxy_or_missing, s.horizon,
            "sì" if s.is_outcome else "no",
        ]
        for s in specs
    ]
    (root / "SCHEMA.md").write_text(
        "# SCHEMA\n\n" + "\n".join(_table(
            ["Nome", "Definizione", "Unità", "Fonte", "Proxy/missing", "Orizzonte", "Outcome"], schema_rows
        )) + "\n"
    )
    event_rows = []
    if not events.empty:
        grouped = events.groupby(["tf", "level_type", "dir"], dropna=False)
        for key, frame in grouped:
            event_rows.append([*key, len(frame), frame["event_ts_utc"].min(), frame["event_ts_utc"].max()])
    inventory_rows = []
    for key, value in (inventory or {}).items():
        if isinstance(value, dict):
            inventory_rows.append([
                key, value.get("rows", ""), value.get("min_ts", ""),
                value.get("max_ts", ""), "missing" if value.get("missing") else value.get("files", ""),
            ])
    if s1_summary is None:
        candidate = Path(cfg.output_dir) / "s1" / "s1_summary.json"
        if candidate.exists():
            s1_summary = json.loads(candidate.read_text())
    if s2_summary is None:
        candidate = Path(cfg.output_dir) / "s2" / "s2_summary.json"
        if candidate.exists():
            s2_summary = json.loads(candidate.read_text())
    s1_count = s1_summary.get("n_tests_s1", 0) if s1_summary else 0
    s1_lines = ["## Test statistici eseguiti: 0 (S0 non esegue test)"]
    if s2_summary:
        survivors = s2_summary.get("survivors", [])
        survivor_rows = [
            [
                row.get("feature"), row.get("bin"), row.get("horizon_min"),
                _fmt(row.get("n")), _fmt(row.get("mean")), _fmt(row.get("t_cluster_day")),
                _fmt(row.get("p_adj_bh")),
            ]
            for row in survivors
        ]
        survivor_section = (
            _table(["feature", "bin", "h", "n", "mean", "t_cluster_day", "p_adj"], survivor_rows)
            if survivor_rows else ["nessun segmento sopravvive alla correzione FDR"]
        )
        s1_lines = [
            f"## Test statistici eseguiti: S0=0, S1={s1_count}, "
            f"S2={s2_summary.get('n_tests_s2', 0)}, "
            f"totale={s1_count + s2_summary.get('n_tests_s2', 0)}",
            "",
            f"- S2: m={s2_summary.get('n_tests_s2', 0)}; positivi grezzi α=0.05: "
            f"{s2_summary.get('n_raw_p_lt_05', 0)}; attesi: "
            f"{_fmt(s2_summary.get('expected_false_positives_05'))}",
            f"- survivors BH q=0.10: {s2_summary.get('n_survivors_q10', 0)}",
            f"- survivors BH q=0.05: {s2_summary.get('n_survivors_q05', 0)}",
            "",
            *survivor_section,
            "",
            f"S2: {s2_summary.get('n_tests_s2', 0) - s2_summary.get('n_survivors_q10', 0)} "
            f"segmenti su {s2_summary.get('n_tests_s2', 0)} non sopravvivono a BH q=0.10.",
            "",
            "Report completo: [report_s2.html](output/s2/report_s2.html)",
        ]
    elif s1_summary:
        pooled_rows = [
            [
                row.get("horizon_min"), row.get("n"), _fmt(row.get("mean")),
                _fmt(row.get("t_cluster_day")), [_fmt(v) for v in row.get("boot_ci95", [])],
                _fmt(row.get("hit_rate")),
            ]
            for row in s1_summary.get("pooled_dedup_all", [])
        ]
        s1_lines = [
            f"## Test statistici eseguiti: S0=0, S1={s1_count} "
            "(t cluster-day, nessuna correzione FDR)",
            "",
            *_table(["h", "n", "mean", "t_cluster_day", "boot_ci95", "hit_rate"], pooled_rows),
            "",
            "Nessuna correzione per test multipli è applicata in S1; arriva in S2.",
            "S1 non autorizza alcuna conclusione di edge.",
            "La popolazione raw contiene duplicati ed è riportata solo descrittivamente.",
            "",
            "Report completo: [report_s1.html](output/s1/report_s1.html)",
        ]
    findings = [
        "# FINDINGS", "",
        "## Inventario dati", "",
        *_table(["Simbolo/timeframe", "Righe", "Min timestamp", "Max timestamp", "File"], inventory_rows),
        "", "## Conteggi eventi", "",
        *_table(["TF", "Level type", "Dir", "Totale", "Da", "A"], event_rows),
        "", "## Feature calcolate / proxy / mancanti", "",
        *_table(["Feature", "Fonte", "Proxy/missing", "Outcome"], [
            [s.name, s.source, s.proxy_or_missing, "sì" if s.is_outcome else "no"] for s in specs
        ]),
        "", "## Operazioni Dukascopy", "", *_notes(NOTES_PATH),
        "", *s1_lines,
        "", "## Cosa NON ha funzionato", "",
    ]
    missing_rows = [[name, "missing" if value else "available"] for name, value in missing.items()]
    tick_missing = int(events["ticks_missing"].sum()) if "ticks_missing" in events else 0
    findings.extend(_table(
        ["Fonte", "Stato"], missing_rows + [["tick days", f"{tick_missing} eventi senza tick"]]
    ))
    findings.extend(["", "## Esito anti-leakage", "",
                     f"- cut count: {leakage.get('n_cuts', 0)}",
                     f"- compared events: {leakage.get('n_events', 0)}",
                     f"- verified columns: {leakage.get('verified_columns', 0)}",
                     f"- failed columns: {', '.join(leakage.get('columns_failed', [])) or 'nessuno'}"])
    (root / "FINDINGS.md").write_text("\n".join(findings) + "\n")


def regenerate(cfg, output_dir: str | Path) -> None:
    """Rigenera SCHEMA.md e FINDINGS.md dagli artefatti di una run."""
    output = Path(output_dir)
    events = pd.read_parquet(output / "events.parquet")
    _manifest = json.loads((output / "manifest.json").read_text())
    _summary = json.loads((output / "s0_summary.json").read_text())
    leakage = json.loads((output / "leakage.json").read_text())
    missing = json.loads((output / "missing_external.json").read_text())
    inventory = json.loads((output / "inventory.json").read_text())
    s1_path = output / "s1" / "s1_summary.json"
    s1_summary = json.loads(s1_path.read_text()) if s1_path.exists() else None
    s2_path = output / "s2" / "s2_summary.json"
    s2_summary = json.loads(s2_path.read_text()) if s2_path.exists() else None
    write_reports(
        events, replace(cfg, reports_dir=str(output)), leakage, missing, inventory,
        s1_summary, s2_summary,
    )
