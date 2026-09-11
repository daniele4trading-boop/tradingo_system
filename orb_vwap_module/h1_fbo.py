"""Variante "H1 false-breakout su M1".

Per ogni candela H1 chiusa tra `first_hour` e `cutoff` (ora locale NY) il range
H/L della candela intera e' l'ORB. Nella candela H1 successiva, entro i primi
`window_min` minuti:

1. break-out: un M1 chiude col corpo (open e close) oltre il range H1;
2. rientro/trigger: un M1 successivo (non necessariamente il seguente) chiude col
   corpo oltre il VWAP M15 cumulato da inizio sessione, dal lato interno al range,
   con volume M1 > media del volume M1 da inizio sessione x `vol_k`;
3. ingresso a mercato al close del trigger, direzione opposta al break-out,
   SL sull'estremo H1 rotto, TP unico sull'estremo H1 opposto.

Il VWAP usato su ogni M1 e' quello dell'ultima M15 chiusa (nessun lookahead);
la media volume usa le M1 chiuse prima della barra corrente. Una posizione per
volta; per ogni finestra al massimo un trade. Posizione ancora aperta alla fine
sessione (`close_time`) chiusa al close dell'ultima M1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

import pandas as pd

from .backtester import load_symbol
from .position_manager import RiskParams, TradeResult, simulate
from .report import max_drawdown
from .session import SessionSpec, build_sessions
from .setup_engine import LONG, SHORT
from .vwap import session_vwap

MAGIC = 20260910


@dataclass
class H1FboConfig:
    symbol: str = "XAUUSD"
    months: int = 12
    end: str | None = None
    initial_capital: float = 100_000.0
    tz: str = "America/New_York"
    session_open: time = time(9, 30)     # inizio cumulata VWAP M15 e media volume M1
    first_hour: time = time(9, 0)        # prima candela H1 usata come ORB
    cutoff: time = time(12, 0)           # ultima candela H1 usata come ORB chiude entro quest'ora
    window_min: int = 35
    close_time: time = time(22, 0)       # chiusura forzata
    vol_k: float = 1.0
    volume_filter: bool = True
    trigger_tf: str = "M1"              # timeframe di break-out/trigger (M1 richiesto; M5 come ripiego se lo storico M1 e' corto)
    vwap_tf: str = "M15"                # "M15": VWAP cumulato M15 da `session_open`; "H1": cumulato sulle H1 chiuse da `first_hour`
    vol_mode: str = "session"           # "session": media M1 da inizio sessione; "lastN": media delle ultime `vol_n` M1
    vol_n: int = 5
    risk: RiskParams = field(default_factory=RiskParams)


@dataclass
class H1FboResult:
    config: H1FboConfig
    trades: pd.DataFrame
    windows: pd.DataFrame
    equity: pd.DataFrame


def _utc(spec: SessionSpec, d: date, t: time) -> datetime:
    return spec.local_to_utc(d, t)


def prepare(data_dir: str, cfg: H1FboConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list]:
    m1 = load_symbol(data_dir, cfg.symbol, cfg.trigger_tf)
    m15 = load_symbol(data_dir, cfg.symbol, "M15")
    end = pd.Timestamp(cfg.end) if cfg.end else m1.index[-1]
    start = end - pd.DateOffset(months=cfg.months)
    m1 = m1[(m1.index > start) & (m1.index <= end)].copy()
    m15 = m15[(m15.index > start - pd.Timedelta(days=3)) & (m15.index <= end)].copy()
    spec = SessionSpec(tz=cfg.tz, open_time=cfg.session_open, cutoff_time=cfg.close_time)
    days = build_sessions(m1.index, spec)
    m1["atr"] = float("nan")
    h1 = m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                "volume": "sum"}).dropna()
    if cfg.vwap_tf == "H1":
        spec_h1 = SessionSpec(tz=cfg.tz, open_time=cfg.first_hour, cutoff_time=cfg.close_time)
        h1["vwap"] = session_vwap(h1, build_sessions(h1.index, spec_h1), spec_h1)
        vwap_df = h1
    else:
        m15["vwap"] = session_vwap(m15, days, spec)
        vwap_df = m15
    return m1, vwap_df, h1, days


def _vwap_at(vwap_df: pd.DataFrame, ts: pd.Timestamp, period_min: int) -> float:
    """VWAP cumulato dell'ultima barra (M15 o H1) chiusa prima dell'apertura della M1 `ts`."""
    closed = vwap_df.loc[:ts - pd.Timedelta(minutes=period_min)]
    if closed.empty:
        return float("nan")
    return float(closed["vwap"].iloc[-1])


def run(data_dir: str, cfg: H1FboConfig, prepared: tuple | None = None) -> H1FboResult:
    m1, vwap_df, h1, days = prepared or prepare(data_dir, cfg)
    vwap_period = 60 if cfg.vwap_tf == "H1" else 15
    spec = SessionSpec(tz=cfg.tz, open_time=cfg.session_open, cutoff_time=cfg.close_time)
    capital = cfg.initial_capital
    trades: list[dict] = []
    wins: list[dict] = []
    eq = [(m1.index[0], capital)]

    for s in days:
        d = s.day
        sess_open = _utc(spec, d, cfg.session_open)
        sess_close = _utc(spec, d, cfg.close_time)
        sess_m1 = m1[(m1.index >= sess_open) & (m1.index < sess_close)]
        if sess_m1.empty:
            continue
        cum_vol = sess_m1["volume"].astype(float).cumsum()
        cum_n = pd.Series(range(1, len(sess_m1) + 1), index=sess_m1.index, dtype=float)
        if cfg.vol_mode == "lastN":
            vol_mean_prev = sess_m1["volume"].astype(float).rolling(cfg.vol_n).mean().shift(1)
        else:
            vol_mean_prev = (cum_vol / cum_n).shift(1)  # media delle M1 chiuse prima della corrente

        busy_until: pd.Timestamp | None = None
        h0 = _utc(spec, d, cfg.first_hour)
        h_last = _utc(spec, d, cfg.cutoff)
        t = pd.Timestamp(h0)
        while t + pd.Timedelta(hours=1) <= h_last:
            orb_start, orb_end = t, t + pd.Timedelta(hours=1)
            t = orb_end
            if orb_start not in h1.index:
                continue
            orb = h1.loc[orb_start]
            hi, lo = float(orb["high"]), float(orb["low"])
            win_end = orb_end + pd.Timedelta(minutes=cfg.window_min)
            wbars = m1[(m1.index >= orb_end) & (m1.index < win_end)]
            row = {"day": d, "orb_start": orb_start, "orb_high": hi, "orb_low": lo,
                   "breakout_ts": None, "breakout_side": None, "trigger_ts": None,
                   "state": "NO_BREAKOUT"}
            if busy_until is not None and orb_end < busy_until:
                row["state"] = "BUSY"
                wins.append(row)
                continue
            side: str | None = None
            for ts, b in wbars.iterrows():
                o, c = float(b["open"]), float(b["close"])
                body_lo, body_hi = min(o, c), max(o, c)
                if side is None:
                    if body_lo > hi:
                        side = "UP"
                    elif body_hi < lo:
                        side = "DOWN"
                    if side:
                        row.update(breakout_ts=ts, breakout_side=side, state="BREAKOUT_NO_TRIGGER")
                    continue
                vw = _vwap_at(vwap_df, ts, vwap_period)
                if pd.isna(vw):
                    continue
                direction = SHORT if side == "UP" else LONG
                beyond = body_hi < vw if direction == SHORT else body_lo > vw
                if not beyond:
                    continue
                vm = vol_mean_prev.get(ts, float("nan"))
                vol_ok = (not cfg.volume_filter) or (not pd.isna(vm) and float(b["volume"]) > vm * cfg.vol_k)
                row["state"] = "TRIGGER_NO_VOLUME" if not vol_ok else row["state"]
                if not vol_ok:
                    continue
                sl_side, tp_lvl = (hi, lo) if direction == SHORT else (lo, hi)
                if not (lo < c < hi):
                    row["state"] = "TRIGGER_OUTSIDE_RANGE"
                    break
                after = m1[(m1.index > ts) & (m1.index < sess_close)]
                tr: TradeResult = simulate(direction, ts, c, float(b["spread"]), lo, hi, after,
                                           capital, "A", cfg.risk, None, tp_level=tp_lvl)
                if tr.risk_dist <= max(0.0, cfg.risk.min_risk_dist):
                    row["state"] = "BAD_RISK"
                    break
                capital += tr.pnl
                busy_until = pd.Timestamp(tr.exit_ts)
                eq.append((tr.exit_ts, capital))
                leg = tr.legs[0]
                trades.append({
                    "day": d, "orb_start": orb_start, "orb_high": hi, "orb_low": lo,
                    "breakout_ts": row["breakout_ts"], "breakout_side": side,
                    "trigger_ts": ts, "direction": direction, "vwap_m15": vw,
                    "volume": float(b["volume"]), "vol_mean_sess": vm,
                    "entry": tr.entry_price, "sl": tr.sl, "tp": tr.tp1,
                    "risk_dist": tr.risk_dist, "rr_target": (abs(tr.tp1 - tr.entry_price) / tr.risk_dist),
                    "lots": leg.lots, "exit_ts": leg.exit_ts, "exit_price": leg.exit_price,
                    "exit_reason": leg.reason, "pnl": tr.pnl, "rr": tr.rr, "outcome": tr.outcome,
                    "capital_after": capital, "magic": MAGIC,
                })
                row.update(trigger_ts=ts, state="TRADE")
                break
            wins.append(row)

    tdf = pd.DataFrame(trades)
    edf = pd.DataFrame(eq, columns=["ts", "equity"]).set_index("ts")
    return H1FboResult(cfg, tdf, pd.DataFrame(wins), edf)


def summarize(res: H1FboResult) -> dict:
    t, w = res.trades, res.windows
    n = len(t)
    out = {
        "trigger_tf": res.config.trigger_tf,
        "vwap_tf": res.config.vwap_tf,
        "vol_mode": res.config.vol_mode,
        "vol_n": res.config.vol_n,
        "cutoff": res.config.cutoff.strftime("%H:%M"),
        "window_min": res.config.window_min,
        "volume_filter": res.config.volume_filter,
        "vol_k": res.config.vol_k,
        "windows": len(w),
        "breakouts": int(w["breakout_ts"].notna().sum()) if len(w) else 0,
        "trigger_no_volume": int((w["state"] == "TRIGGER_NO_VOLUME").sum()) if len(w) else 0,
        "trades": n,
        "wins": 0, "losses": 0, "be": 0, "win_rate": 0.0, "avg_rr": 0.0, "avg_rr_target": 0.0,
        "profit_factor": 0.0, "pnl": 0.0, "pnl_pct": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0,
        "final_capital": res.config.initial_capital,
    }
    if n == 0:
        return out
    gp = t.loc[t["pnl"] > 0, "pnl"].sum()
    gl = -t.loc[t["pnl"] < 0, "pnl"].sum()
    dd, ddp = max_drawdown(res.equity["equity"])
    out.update({
        "wins": int((t["outcome"] == "win").sum()),
        "losses": int((t["outcome"] == "loss").sum()),
        "be": int((t["outcome"] == "be").sum()),
        "win_rate": round(float((t["outcome"] == "win").mean() * 100), 1),
        "avg_rr": round(float(t["rr"].mean()), 3),
        "avg_rr_target": round(float(t["rr_target"].mean()), 2),
        "profit_factor": round(float(gp / gl), 3) if gl > 0 else float("inf"),
        "pnl": round(float(t["pnl"].sum()), 2),
        "pnl_pct": round(float(t["pnl"].sum() / res.config.initial_capital * 100), 2),
        "max_dd": round(dd, 2), "max_dd_pct": round(ddp, 2),
        "final_capital": round(float(t["capital_after"].iloc[-1]), 2),
    })
    return out
