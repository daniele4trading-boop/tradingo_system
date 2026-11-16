from __future__ import annotations

import base64
import hashlib
import html
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Config
from ..event_study import bh, diff_test, fe_mean, mfe_mae, prepare
from ..manifest import write_manifest

BH_Q = 0.10
PRIMARY_CLUSTER = "trading_week"
FAMILIES = ["equal", "swing20", "swing10", "swing5"]


def _cell(frame: pd.DataFrame, h: int, rng: np.random.Generator, equal_weight: bool = False) -> dict:
    groups = frame["symbol"].to_numpy()
    week = frame[PRIMARY_CLUSTER].to_numpy()
    net = fe_mean(frame[f"net_{h}_bp"].to_numpy(float), groups, week, rng, equal_weight)
    gross = fe_mean(frame[f"fwd_ret_{h}_ex_bp"].to_numpy(float), groups, week, rng, equal_weight,
                    bootstrap=False)
    day = fe_mean(frame[f"net_{h}_bp"].to_numpy(float), groups, frame["trading_day"].to_numpy(),
                  rng, equal_weight, bootstrap=False)
    raw = frame[f"fwd_ret_{h}_dir_bp"].astype(float)
    return {
        "n": net["n"],
        "n_weeks": net.get("n_clusters"),
        "gross_dir_mean": float(raw.mean()) if raw.notna().any() else None,
        "gross_mean": gross["mean"],
        "gross_t": gross["t"],
        "cost_mean": float(frame["cost_rt_bp"].mean()) if len(frame) else None,
        "net_mean": net["mean"],
        "net_median": net.get("median"),
        "net_hit_rate": net.get("hit_rate"),
        "net_t": net["t"],
        "net_p": net["p"],
        "net_ci95": net["boot_ci95"],
        "net_t_day": day["t"],
    }


def _grid(frame: pd.DataFrame, cfg: Config, rng: np.random.Generator) -> list[dict]:
    rows: list[dict] = []
    symbols = [s for s in cfg.symbols if s in set(frame["symbol"])]
    for h in cfg.horizons_bars:
        def add(split: str, key: str, subset: pd.DataFrame, equal_weight: bool = False,
                h: int = h) -> None:
            if len(subset) >= 2:
                rows.append({"split": split, "key": key, "h": h,
                             **_cell(subset, h, rng, equal_weight)})

        add("pooled", "fe_nweighted", frame)
        add("pooled", "fe_equal_weight", frame, equal_weight=True)
        for symbol in symbols:
            add("symbol", symbol, frame[frame["symbol"] == symbol])
        for family in FAMILIES:
            add("level_family", family, frame[frame["level_family"] == family])
        for symbol in symbols:
            for family in FAMILIES:
                sub = frame[(frame["symbol"] == symbol) & (frame["level_family"] == family)]
                add("symbol_x_level", f"{symbol}|{family}", sub)
        for direction in ["sweep_high", "sweep_low"]:
            add("dir", direction, frame[frame["dir"] == direction])
        for year in sorted(frame["event_year"].unique()):
            add("year", str(int(year)), frame[frame["event_year"] == year])
        asym = diff_test(frame[frame["dir"] == "sweep_high"], frame[frame["dir"] == "sweep_low"],
                         f"net_{h}_bp", PRIMARY_CLUSTER)
        rows.append({"split": "asymmetry", "key": "high-low", "h": h, "n": asym["n_a"] + asym["n_b"],
                     "net_mean": asym["diff"], "net_t": asym["t"], "net_p": asym["p"]})
    survive = bh([r.get("net_p") for r in rows], BH_Q)
    for row, keep in zip(rows, survive, strict=True):
        row["bh_q10"] = bool(keep)
    return rows


def _mfe_tables(frame: pd.DataFrame, cfg: Config) -> dict:
    h = cfg.mfe_mae_horizon_bars
    out = {"pooled": mfe_mae(frame, h)}
    out["symbol"] = {s: mfe_mae(g, h) for s, g in frame.groupby("symbol")}
    out["level_family"] = {f: mfe_mae(g, h) for f, g in frame.groupby("level_family")}
    return out


def _plots(frame: pd.DataFrame, rows: list[dict], cfg: Config, output: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    paths = []
    fig, axes = plt.subplots(1, len(cfg.horizons_bars), figsize=(18, 4.5), squeeze=False)
    for axis, h in zip(axes[0], cfg.horizons_bars, strict=True):
        sel = [r for r in rows if r["h"] == h and r["split"] in {"pooled", "symbol", "level_family"}]
        labels = [f"{r['split'][:3]}:{r['key']}" for r in sel]
        means = np.array([r["net_mean"] for r in sel], dtype=float)
        lo = np.array([r["net_ci95"][0] if r["net_ci95"][0] is not None else np.nan for r in sel])
        hi = np.array([r["net_ci95"][1] if r["net_ci95"][1] is not None else np.nan for r in sel])
        y = np.arange(len(sel))
        axis.errorbar(means, y, xerr=[means - lo, hi - means], fmt="o", capsize=3)
        axis.axvline(0, color="k", lw=0.8)
        axis.set_yticks(y, labels, fontsize=7)
        axis.invert_yaxis()
        axis.set_title(f"net bp, h={h} H4 bars (95% CI week bootstrap)", fontsize=9)
    fig.tight_layout()
    path = output / "s1_net_ci.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    paths.append(path)

    h = cfg.mfe_mae_horizon_bars
    fig, axis = plt.subplots(figsize=(6, 5))
    mfe = frame[f"mfe_{h}_atr"].to_numpy(float)
    mae = frame[f"mae_{h}_atr"].to_numpy(float)
    ok = np.isfinite(mfe) & np.isfinite(mae)
    if ok.any():
        axis.hexbin(np.clip(mfe[ok], 0, 10), np.clip(mae[ok], 0, 10), gridsize=40, mincnt=1)
    axis.plot([0, 10], [0, 10], "k--", lw=0.8)
    axis.set_xlabel(f"MFE {h} bars (ATR)")
    axis.set_ylabel(f"MAE {h} bars (ATR)")
    fig.tight_layout()
    path = output / "s1_mfe_mae.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    paths.append(path)
    return paths


def _f(value: object, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return html.escape(str(value))


def _table(headers: list[str], body: list[list[object]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    rows = "".join("<tr>" + "".join(f"<td>{_f(c)}</td>" for c in r) + "</tr>" for r in body)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def _split_table(rows: list[dict], split: str) -> str:
    body = []
    for r in rows:
        if r["split"] != split:
            continue
        ci = r.get("net_ci95") or [None, None]
        body.append([r["key"], r["h"], r["n"], r.get("n_weeks"), r.get("gross_dir_mean"),
                     r.get("gross_mean"), r.get("gross_t"), r.get("cost_mean"), r["net_mean"],
                     r["net_t"], r.get("net_t_day"), ci[0], ci[1], r.get("net_hit_rate"),
                     "yes" if r["bh_q10"] else ""])
    return _table(["key", "h", "n", "weeks", "gross dir", "gross ex", "t gross", "cost",
                   "net", "t net (week)", "t net (day)", "CI lo", "CI hi", "hit", "BH q.10"], body)


def _write_html(summary: dict, rows: list[dict], plots: list[Path], output: Path) -> None:
    v = summary["verdict"]
    mfe = summary["mfe_mae"]
    mfe_rows = [["pooled", "", *[mfe["pooled"].get(k) for k in _MFE_KEYS]]]
    for split in ("symbol", "level_family"):
        mfe_rows += [[split, key, *[val.get(k) for k in _MFE_KEYS]] for key, val in mfe[split].items()]
    images = "".join(
        f'<img src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}" '
        f'style="max-width:100%">' for p in plots
    )
    sections = [
        ("Pooled (effetto fisso simbolo)", "pooled"),
        ("Per simbolo", "symbol"),
        ("Per level family", "level_family"),
        ("Simbolo × level family", "symbol_x_level"),
        ("Per direzione", "dir"),
        ("Per anno (pooled FE)", "year"),
    ]
    asym = [[r["h"], r["n"], r["net_mean"], r["net_t"], "yes" if r["bh_q10"] else ""]
            for r in rows if r["split"] == "asymmetry"]
    parts = [
        "<html><head><meta charset='utf-8'><title>S1 H4</title><style>"
        "body{font-family:sans-serif;margin:24px}table{border-collapse:collapse;font-size:12px;"
        "margin-bottom:18px}td,th{border:1px solid #ccc;padding:3px 6px;text-align:right}"
        "th{background:#eee}</style></head><body>",
        "<h1>S1 H4 — event study sweep (continuation)</h1>",
        f"<p><b>Verdetto:</b> {html.escape(v['text'])}</p>",
        f"<p>Eventi: {summary['n_events']} · test S1: {v['n_tests']} · sopravvissuti BH q=0.10: "
        f"{v['n_bh']} (di cui positivi: {v['n_bh_positive']}). Metrica primaria: "
        "<code>fwd_ret_h_ex_bp − cost_rt_bp</code> (entry next open, direzione continuation, "
        "drift simbolo×anno rimosso, spread round-trip sottratto). SE cluster per settimana di "
        "trading (ts+3h), t per giorno come robustezza; CI bootstrap a blocchi settimanali "
        f"({summary['n_boot']} draw). Effetto fisso simbolo: residui demeanati per simbolo; "
        "<i>fe_equal_weight</i> pesa ogni simbolo 1/K per neutralizzare la prevalenza FX.</p>",
    ]
    for title, split in sections:
        parts += [f"<h2>{title}</h2>", _split_table(rows, split)]
    parts += ["<h2>Asimmetria sweep_high − sweep_low (net)</h2>",
              _table(["h", "n", "diff", "t (week)", "BH q.10"], asym),
              f"<h2>MFE/MAE a {summary['mfe_mae_horizon_bars']} barre (ATR)</h2>",
              _table(["split", "key", *_MFE_KEYS], mfe_rows), "<h2>Grafici</h2>", images,
              "</body></html>"]
    (output / "s1_report.html").write_text("".join(parts))


_MFE_KEYS = ["n", "share_mfe_gt_mae", "mfe_p50", "mae_p50", "mfe_p75", "mae_p75", "mfe_p90", "mae_p90"]


def _verdict(rows: list[dict]) -> dict:
    tested = [r for r in rows if r.get("net_p") is not None]
    survivors = [r for r in tested if r["bh_q10"]]
    positive = [r for r in survivors if r["net_mean"] > 0 and r["split"] != "asymmetry"]
    if positive:
        keys = ", ".join(f"{r['split']}:{r['key']}@h{r['h']}" for r in positive[:10])
        text = (f"{len(positive)} celle con net > 0 sopravvivono a BH q=0.10 ({keys}). "
                "Da validare in S2/out-of-sample.")
    else:
        text = "Nessuna cella con rendimento netto positivo sopravvive a BH q=0.10."
    return {"n_tests": len(tested), "n_bh": len(survivors), "n_bh_positive": len(positive), "text": text}


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def run(cfg: Config) -> dict:
    started = time.time()
    output = Path(cfg.output_dir)
    events_path = output / "events_h4.parquet"
    if not events_path.exists():
        raise SystemExit(f"S1: {events_path} non trovato, eseguire prima S0")
    frame = prepare(pd.read_parquet(events_path), cfg.horizons_bars)
    rng = np.random.default_rng(cfg.seed)
    rows = _grid(frame, cfg, rng)
    s1_dir = output / "s1"
    s1_dir.mkdir(parents=True, exist_ok=True)
    summary = _clean({
        "n_events": int(len(frame)),
        "n_events_by_symbol": frame["symbol"].value_counts().sort_index().to_dict(),
        "n_events_by_level_family": frame["level_family"].value_counts().sort_index().to_dict(),
        "hypothesis": cfg.hypothesis,
        "primary_metric": "fwd_ret_h_ex_bp - cost_rt_bp",
        "cluster": PRIMARY_CLUSTER,
        "bh_q": BH_Q,
        "n_boot": 2000,
        "mfe_mae_horizon_bars": cfg.mfe_mae_horizon_bars,
        "mfe_mae": _mfe_tables(frame, cfg),
        "rows": rows,
        "verdict": _verdict(rows),
    })
    (s1_dir / "s1_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    pd.DataFrame(summary["rows"]).to_csv(s1_dir / "s1_tests.csv", index=False)
    plots = _plots(frame, summary["rows"], cfg, s1_dir)
    _write_html(summary, summary["rows"], plots, s1_dir)
    write_manifest(s1_dir / "manifest.json", cfg,
                   {events_path.name: hashlib.sha256(events_path.read_bytes()).hexdigest()}, started)
    return summary
