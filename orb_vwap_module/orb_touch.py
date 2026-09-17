"""Variante "ORB break + tocco del VWAP": ORB M15 all'apertura NY, VWAP di sessione su M5.

Setup: una M5 chiude oltre l'ORB (sopra il massimo → bias long, sotto il minimo → bias short;
un break successivo dal lato opposto sostituisce il bias). Trigger: la prima M5 successiva al
break che TOCCA il VWAP di sessione (low <= vwap <= high). Entry al close di quella candela,
SL = min/max della candela trigger ± buffer, TP a RR fisso. Un trade al giorno; setup e trigger
validi solo nelle prime `window_hours` dall'apertura; posizione aperta chiusa forzatamente a
`close_time` (locale). Sizing `risk_pct` sul capitale corrente; spread pagato in entrata e uscita.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .backtester import load_symbol
from .orb import compute_orb
from .position_manager import RiskParams, TradeResult, TrailParams, simulate
from .session import SessionSpec, build_sessions
from .setup_engine import LONG, SHORT
from .vwap import session_vwap


@dataclass
class OrbTouchConfig:
    symbol: str = "XAUUSD"
    start: str = "2026-01-01"
    end: str | None = None
    initial_capital: float = 100_000.0
    tz: str = "America/New_York"
    open_time: time = time(9, 30)
    orb_tf: str = "15min"
    window_hours: float = 4.0        # setup + trigger entro N ore dall'apertura
    close_time: time = time(17, 0)   # chiusura forzata della posizione (locale)
    sl_buffer: float = 2.0           # $ oltre il min/max della candela trigger (200 punti)
    rr: float = 1.0
    risk: RiskParams = field(default_factory=lambda: RiskParams(risk_pct=1.0, rr_tp1=1.0, sl_buffer=2.0))


def run(data_dir: str | Path, cfg: OrbTouchConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Ritorna (trades, days, equity)."""
    m5 = load_symbol(data_dir, cfg.symbol, "M5")
    m5 = m5[m5.index >= pd.Timestamp(cfg.start)]
    if cfg.end:
        m5 = m5[m5.index <= pd.Timestamp(cfg.end)]
    spec = SessionSpec(tz=cfg.tz, open_time=cfg.open_time, cutoff_time=cfg.close_time,
                       orb_tf=cfg.orb_tf, trigger_tf="5min")
    sessions = build_sessions(m5.index, spec)
    m5 = m5.copy()
    m5["vwap_s"] = session_vwap(m5, sessions, spec)
    m5["atr"] = np.nan
    rp = RiskParams(**{**vars(cfg.risk), "sl_buffer": cfg.sl_buffer, "rr_tp1": cfg.rr})

    capital = cfg.initial_capital
    trades: list[dict] = []
    days: list[dict] = []
    eq = [(pd.Timestamp(m5.index[0]), capital)]
    for s in sessions:
        orb = compute_orb(m5, s, spec)
        rec = {"day": s.day, "orb_high": orb.high if orb else np.nan, "orb_low": orb.low if orb else np.nan,
               "break_side": None, "break_ts": None, "trigger_ts": None, "state": "NO_ORB"}
        if orb is None:
            days.append(rec)
            continue
        win_end = s.open_utc + timedelta(hours=cfg.window_hours)
        last_open = s.cutoff_utc - spec.trigger_td
        bars = m5[(m5.index >= s.orb_end_utc) & (m5.index <= last_open)]
        bias: str | None = None
        rec["state"] = "NO_BREAK"
        tr: TradeResult | None = None
        for ts, b in bars.iterrows():
            if ts >= win_end:
                break
            c, hi, lo, vw = float(b["close"]), float(b["high"]), float(b["low"]), float(b["vwap_s"])
            if bias is not None and not np.isnan(vw) and lo <= vw <= hi:
                rec.update(trigger_ts=ts, state="TRIGGERED")
                after = bars[bars.index > ts]
                # SL sull'estremo della candela trigger ± buffer: passo hi/lo come "range".
                tr = simulate(bias, ts, c, float(b["spread"]), lo, hi, after, capital if cfg.risk.compound else cfg.initial_capital, "A", rp,
                              TrailParams(), None)
                break
            if c > orb.high and bias != LONG:
                bias = LONG
                rec.update(break_side=LONG, break_ts=ts, state="BREAK")
            elif c < orb.low and bias != SHORT:
                bias = SHORT
                rec.update(break_side=SHORT, break_ts=ts, state="BREAK")
        if tr is not None:
            leg = tr.legs[0]
            capital += tr.pnl
            trades.append({"day": s.day, "direction": tr.direction, "break_ts": rec["break_ts"],
                           "entry_ts": tr.entry_ts, "entry": tr.entry_price, "sl": tr.sl, "tp": tr.tp1,
                           "risk_dist": tr.risk_dist, "lots": leg.lots, "exit_ts": leg.exit_ts,
                           "exit": leg.exit_price, "reason": leg.reason, "pnl": tr.pnl, "rr": tr.rr,
                           "outcome": tr.outcome, "equity": capital})
            eq.append((leg.exit_ts, capital))
        days.append(rec)
    trades_df = pd.DataFrame(trades)
    equity = pd.DataFrame(eq, columns=["ts", "equity"]).set_index("ts")
    return trades_df, pd.DataFrame(days), equity


def summarize(trades: pd.DataFrame, days: pd.DataFrame, equity: pd.DataFrame, capital0: float) -> dict:
    n = len(trades)
    out = {"days": len(days), "days_with_break": int((days["break_ts"].notna()).sum()) if len(days) else 0,
           "trades": n}
    if n == 0:
        return out
    wins = int((trades["outcome"] == "win").sum())
    gp = trades.loc[trades.pnl > 0, "pnl"].sum()
    gl = -trades.loc[trades.pnl < 0, "pnl"].sum()
    peak = equity["equity"].cummax()
    dd = (equity["equity"] - peak)
    out.update({
        "longs": int((trades.direction == LONG).sum()), "shorts": int((trades.direction == SHORT).sum()),
        "wins": wins, "losses": int((trades["outcome"] == "loss").sum()),
        "win_rate": round(wins / n * 100, 1), "avg_rr": round(trades.rr.mean(), 3),
        "profit_factor": round(gp / gl, 2) if gl > 0 else None,
        "pnl": round(trades.pnl.sum(), 2), "return_pct": round((equity["equity"].iloc[-1] / capital0 - 1) * 100, 2),
        "max_dd": round(dd.min(), 2), "max_dd_pct": round((dd / peak).min() * 100, 2),
        "median_risk_dist": round(trades.risk_dist.median(), 2),
        "exits": trades.reason.value_counts().to_dict(),
    })
    return out
