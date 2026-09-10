"""Metriche aggregate, confronto A/B, griglia e scrittura dei file di output."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .backtester import BacktestConfig, BacktestResult, config_dict, prepare, run
from .position_manager import TrailParams


def max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """(drawdown massimo in $, in % dal picco)."""
    peak = equity.cummax()
    dd = equity - peak
    ddp = dd / peak * 100.0
    return float(dd.min()), float(ddp.min())


def summarize(res: BacktestResult) -> dict:
    t, su = res.trades, res.setups
    n = len(t)
    out = {
        "variant": res.config.variant,
        "volume_filter": res.config.setup.volume_filter,
        "atr_period": res.config.trail.atr_period,
        "atr_mult": res.config.trail.atr_mult,
        "sessions": res.sessions_total,
        "sessions_with_orb": res.sessions_with_orb,
        "setups_armed": int(len(su)),
        "setups_confirmed": int(su["reentry_ts"].notna().sum()) if len(su) else 0,
        "setups_invalidated": int((su["final_state"] == "INVALID").sum()) if len(su) else 0,
        "setups_expired": int((su["final_state"] == "EXPIRED").sum()) if len(su) else 0,
        "setups_disarmed_by_opposite": int((su["final_state"] == "DISARMED").sum()) if len(su) else 0,
        "skipped_min_risk": int((su["final_state"] == "SKIPPED_MIN_RISK").sum()) if len(su) else 0,
        "vol_filter_rejections": int(su["vol_filter_rejections"].sum()) if len(su) else 0,
        "trades": n,
    }
    if n == 0:
        out.update({"win_rate": None, "pnl": 0.0, "final_equity": res.config.initial_capital,
                    "max_dd": 0.0, "max_dd_pct": 0.0})
        return out
    wins = (t["outcome"] == "win").sum()
    losses = (t["outcome"] == "loss").sum()
    gp = t.loc[t["pnl"] > 0, "pnl"].sum()
    gl = -t.loc[t["pnl"] < 0, "pnl"].sum()
    dd, ddp = max_drawdown(res.equity["equity"])
    out.update({
        "longs": int((t["direction"] == "long").sum()),
        "shorts": int((t["direction"] == "short").sum()),
        "wins": int(wins), "losses": int(losses), "be": int((t["outcome"] == "be").sum()),
        "win_rate": round(wins / n * 100.0, 1),
        "win_rate_incl_be": round((n - losses) / n * 100.0, 1),
        "avg_rr": round(t["rr_realized"].mean(), 3),
        "avg_rr_win": round(t.loc[t["outcome"] == "win", "rr_realized"].mean(), 3) if wins else None,
        "avg_rr_loss": round(t.loc[t["outcome"] == "loss", "rr_realized"].mean(), 3) if losses else None,
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "pnl": round(t["pnl"].sum(), 2),
        "final_equity": round(res.equity["equity"].iloc[-1], 2),
        "return_pct": round((res.equity["equity"].iloc[-1] / res.config.initial_capital - 1) * 100, 2),
        "max_dd": round(dd, 2), "max_dd_pct": round(ddp, 2),
        "same_bar_reentry_trigger": int(t["reentry_is_trigger_bar"].sum()),
        "leg1_tp1_hits": int((t["leg1_reason"] == "TP1").sum()),
        "cutoff_closes": int((t["leg1_reason"] == "CUTOFF").sum() + (t["leg2_reason"] == "CUTOFF").sum()),
    })
    if "leg2_reason" in t and t["leg2_reason"].notna().any():
        out["leg2_reasons"] = t["leg2_reason"].value_counts().to_dict()
    return out


def monthly(res: BacktestResult) -> pd.DataFrame:
    if res.trades.empty:
        return pd.DataFrame()
    t = res.trades.copy()
    t["month"] = pd.to_datetime(t["session"]).dt.to_period("M").astype(str)
    g = t.groupby("month").agg(trades=("pnl", "size"), pnl=("pnl", "sum"),
                               wins=("outcome", lambda s: (s == "win").sum()))
    g["win_rate"] = (g["wins"] / g["trades"] * 100).round(1)
    g["pnl"] = g["pnl"].round(2)
    return g.reset_index()


def run_ab(data_dir: str | Path, base: BacktestConfig) -> dict[str, BacktestResult]:
    prepared = prepare(data_dir, base)
    return {v: run(data_dir, replace(base, variant=v), prepared) for v in ("A", "B")}


def run_grid(data_dir: str | Path, base: BacktestConfig, periods=(7, 14, 21),
             mults=(1.5, 2.0, 2.5, 3.0), vol_filters=(False, True)) -> pd.DataFrame:
    prepared = prepare(data_dir, base)
    rows = []
    for vf in vol_filters:
        for p in periods:
            for m in mults:
                cfg = replace(base, variant="B", setup=replace(base.setup, volume_filter=vf),
                              trail=TrailParams(p, m))
                rows.append(summarize(run(data_dir, cfg, prepared)))
    cols = ["volume_filter", "atr_period", "atr_mult", "trades", "win_rate", "win_rate_incl_be",
            "avg_rr", "profit_factor", "pnl", "return_pct", "max_dd", "max_dd_pct"]
    return pd.DataFrame(rows)[cols]


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_nessun dato_\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in r.values) + " |")
    return "\n".join(lines) + "\n"


def write_outputs(out_dir: str | Path, ab: dict[str, BacktestResult], grid: pd.DataFrame | None,
                  data_note: str) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summaries = {v: summarize(r) for v, r in ab.items()}
    for v, r in ab.items():
        r.trades.to_csv(out / f"trades_{v}.csv", index=False)
        r.setups.to_csv(out / f"setups_{v}.csv", index=False)
        r.equity.to_csv(out / f"equity_{v}.csv", index=False)
    if grid is not None:
        grid.to_csv(out / "grid_B.csv", index=False)
    (out / "summary.json").write_text(json.dumps(
        {"config": config_dict(ab["B"].config), "summaries": summaries}, indent=2, default=str), encoding="utf-8")

    cfg = ab["B"].config
    b = ab["B"]
    lines = [
        "# ORB + VWAP false-breakout — report backtest", "",
        f"Simbolo **{cfg.symbol}**, ultimi **{cfg.months} mesi**, capitale iniziale {cfg.initial_capital:,.0f} $.",
        f"Periodo dati: {b.equity['ts'].iloc[0]} → {b.trades['exit_ts'].iloc[-1] if len(b.trades) else '-'} (UTC).", "",
        f"> **Dati:** {data_note}", "",
        "## Regole simulate", "",
        f"- Sessione: apertura {cfg.session.open_time} {cfg.session.tz}, cutoff {cfg.session.cutoff_time} (DST gestito via zoneinfo).",
        f"- ORB = High/Low della finestra {cfg.session.orb_tf} all'apertura; setup/trigger su {cfg.session.trigger_tf}.",
        "- Break-in: close oltre il livello ORB; rientro: close di nuovo dentro; trigger: corpo intero oltre il VWAP di sessione (ricalcolato su M5, reset all'apertura).",
        "- Invalidazione: dopo il rientro una chiusura torna oltre il livello → direzione bloccata fino alla sessione successiva; cutoff → EXPIRED.",
        "- Una sola operazione per sessione; ingresso al close della barra trigger; spread applicato in ingresso e in uscita.",
        f"- Rischio {cfg.risk.risk_pct}% del capitale corrente; SL = ORB_Low/High (buffer {cfg.risk.sl_buffer}); TP1 = RR 1:{cfg.risk.rr_tp1:g}.",
        f"- Variante B: 2 gambe 0.5%; al TP1 gamba 2 → BE + trailing {cfg.trail.atr_mult}×ATR({cfg.trail.atr_period}) M5, mai arretrato.",
        "- Su una barra che tocca sia stop sia TP prevale lo stop; posizione aperta al cutoff chiusa al close dell'ultima M5.",
        "", "## Confronto Variante A vs B (parametri default)", "",
    ]
    cmp = pd.DataFrame(summaries).T
    keep = [c for c in ["trades", "wins", "losses", "be", "win_rate", "win_rate_incl_be", "avg_rr", "avg_rr_win",
                        "avg_rr_loss", "profit_factor", "pnl", "return_pct", "final_equity", "max_dd",
                        "max_dd_pct", "leg1_tp1_hits", "cutoff_closes"] if c in cmp.columns]
    lines.append(_md_table(cmp[keep].reset_index().rename(columns={"index": "variant"})))
    sA = summaries["A"]
    lines += ["", "## Conteggio setup (identico per A e B: stesso motore di setup)", "",
              f"- Sessioni nel periodo: {sA['sessions']} (con ORB disponibile: {sA['sessions_with_orb']})",
              f"- Setup armati (break-in): {sA['setups_armed']} — di cui con rientro confermato: {sA['setups_confirmed']}",
              f"- Trigger → trade aperti: {sA['trades']}",
              f"- Invalidati (rientro fallito): {sA['setups_invalidated']}",
              f"- Scaduti al cutoff senza trigger: {sA['setups_expired']}",
              f"- Disarmati dal trade opposto: {sA['setups_disarmed_by_opposite']}",
              f"- Trigger scartati per distanza SL < min_risk_dist ({cfg.risk.min_risk_dist}): {sA['skipped_min_risk']}",
              f"- Rientro e trigger sulla stessa barra: {sA.get('same_bar_reentry_trigger', 0)} trade", ""]
    if "leg2_reasons" in summaries["B"]:
        lines += ["Esiti gamba 2 (B): " + ", ".join(f"{k} {v}" for k, v in summaries["B"]["leg2_reasons"].items()), ""]
    for v in ("A", "B"):
        lines += [f"## Mensile variante {v}", "", _md_table(monthly(ab[v]))]
    if grid is not None:
        lines += ["## Griglia variante B — ATR(periodo) × moltiplicatore × filtro volume", "",
                  _md_table(grid.sort_values(["volume_filter", "atr_period", "atr_mult"]))]
    lines += ["", "## File", "", "- `trades_A.csv`, `trades_B.csv`: log per trade (timestamp break-in / rientro / trigger, livelli, uscite per gamba, esito, RR, filtro volume).",
              "- `setups_A.csv`: tutti i setup armati con stato finale (TRIGGERED / INVALID / EXPIRED / DISARMED) per validazione manuale.",
              "- `equity_*.csv`: curva equity a trade chiuso. `grid_B.csv`: griglia completa. `summary.json`: config + metriche."]
    path = out / "REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
