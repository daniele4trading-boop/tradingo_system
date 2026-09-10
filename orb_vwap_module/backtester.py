"""Orchestrazione del backtest: dati → sessioni → ORB/VWAP → setup → posizione."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

import pandas as pd

from .orb import compute_orb
from .position_manager import RiskParams, TrailParams, TradeResult, simulate
from .session import SessionSpec, build_sessions, build_windows, session_bars
from .setup_engine import LONG, SHORT, SetupParams, run_session_setup
from .vwap import atr, session_vwap, volume_ma

CANON_COLS = ["open", "high", "low", "close", "volume", "spread_med"]


def load_csv(path: str | Path) -> pd.DataFrame:
    """Stesso comportamento del loader gold-agent (indice ts UTC naive, ordinato,
    deduplicato); tiene solo le colonne necessarie. `spread` = spread_med (0 se assente)."""
    df = pd.read_csv(path, parse_dates=["ts"]).set_index("ts").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    keep = [c for c in CANON_COLS if c in df.columns]
    df = df[keep].copy()
    df["spread"] = df["spread_med"] if "spread_med" in df.columns else 0.0
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df


def load_symbol(data_dir: str | Path, symbol: str, tf: str) -> pd.DataFrame:
    files = sorted(Path(data_dir).glob(f"export_{symbol}_{tf}_*.csv"))
    if not files:
        raise FileNotFoundError(f"Nessun CSV export_{symbol}_{tf}_*.csv in {data_dir}")
    df = pd.concat([load_csv(f) for f in files]).sort_index()
    return df[~df.index.duplicated(keep="last")]


@dataclass
class BacktestConfig:
    symbol: str = "XAUUSD"
    months: int = 12
    end: str | None = None                # ISO UTC; None = ultima barra
    initial_capital: float = 100_000.0
    session: SessionSpec = field(default_factory=SessionSpec)
    setup: SetupParams = field(default_factory=SetupParams)
    risk: RiskParams = field(default_factory=RiskParams)
    trail: TrailParams = field(default_factory=TrailParams)
    variant: str = "B"                    # "A" | "B"


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: pd.DataFrame
    setups: pd.DataFrame
    equity: pd.DataFrame
    sessions_total: int
    sessions_with_orb: int


def prepare(data_dir: str | Path, cfg: BacktestConfig) -> tuple[pd.DataFrame, pd.DataFrame, list]:
    """Carica trigger-tf e orb-tf, taglia agli ultimi `months` mesi, calcola
    VWAP di sessione, ATR e media volume sul trigger-tf."""
    tf_map = {"5min": "M5", "15min": "M15", "1min": "M1", "1h": "H1", "4h": "H4"}
    trig = load_symbol(data_dir, cfg.symbol, tf_map[cfg.session.trigger_tf])
    # ORB su un timeframe non esportato (es. 30min, 2h): aggregato dalle barre trigger
    orb_tf = tf_map.get(cfg.session.orb_tf)
    orbdf = load_symbol(data_dir, cfg.symbol, orb_tf) if orb_tf else trig
    end = pd.Timestamp(cfg.end) if cfg.end else trig.index[-1]
    start = end - pd.DateOffset(months=cfg.months)
    # ATR/volume MA hanno bisogno di storia prima dello start: calcolo su tutto poi taglio
    trig = trig.copy()
    trig["atr"] = atr(trig, cfg.trail.atr_period)
    trig["vol_ma"] = volume_ma(trig, cfg.setup.vol_ma_n)
    full = trig[trig.index <= end]
    trig = full[full.index >= start].copy()
    trig.attrs["atr_period"] = cfg.trail.atr_period
    trig.attrs["vol_ma_n"] = cfg.setup.vol_ma_n
    orbdf = orbdf[(orbdf.index >= start) & (orbdf.index <= end)]
    days = build_sessions(trig.index, cfg.session)
    trig["vwap"] = session_vwap(trig, days, cfg.session)
    return trig, orbdf, build_windows(days, cfg.session), full


def run(data_dir: str | Path, cfg: BacktestConfig, prepared: tuple | None = None) -> BacktestResult:
    trig, orbdf, sessions, full = prepared if prepared else prepare(data_dir, cfg)
    if (trig.attrs.get("atr_period") != cfg.trail.atr_period
            or trig.attrs.get("vol_ma_n") != cfg.setup.vol_ma_n):
        # riuso dei dati preparati con parametri diversi (grid search): ricalcolo
        # gli indicatori sulla storia completa (warm-up conservato) e riallineo
        trig = trig.copy()
        trig["atr"] = atr(full, cfg.trail.atr_period).reindex(trig.index)
        trig["vol_ma"] = volume_ma(full, cfg.setup.vol_ma_n).reindex(trig.index)
    capital = cfg.initial_capital
    trades: list[dict] = []
    setups: list[dict] = []
    eq = [{"ts": trig.index[0], "equity": capital}]
    n_orb = 0
    busy_until: pd.Timestamp | None = None    # una posizione alla volta (finestre rolling)
    for s in sessions:
        if busy_until is not None and pd.Timestamp(s.open_utc) < busy_until:
            continue
        orb = compute_orb(orbdf, s, cfg.session)
        if orb is None:
            continue
        bars = session_bars(trig, s, cfg.session)
        if bars.empty:
            continue
        n_orb += 1
        res = run_session_setup(bars, s, orb, cfg.setup)
        setup_rows: dict[str, dict] = {}
        for d in (LONG, SHORT):
            st = res.dirs[d]
            if st.breakin_ts is None and st.failed_attempts == 0 and st.range_rejects == 0:
                continue                  # mai armato (IDLE o disarmato senza break-in)
            state = st.state if st.breakin_ts is not None else ("FAILED" if st.failed_attempts else "RANGE_REJECT")
            setup_rows[d] = {"session": s.day, "direction": d, "final_state": state,
                           "breakin_ts": st.breakin_ts, "reentry_ts": st.reentry_ts,
                           "trigger_ts": st.trigger_ts, "end_ts": st.end_ts,
                           "vol_filter_rejections": st.vol_checks_failed,
                           "failed_attempts": st.failed_attempts, "range_rejects": st.range_rejects,
                           "orb_high": orb.high, "orb_low": orb.low}
            setups.append(setup_rows[d])
        trg = res.trigger
        if trg is None:
            continue
        sl_lo, sl_hi = orb.low, orb.high
        if not pd.isna(trg.sl_level):
            sl_lo = sl_hi = trg.sl_level
        sl_level = sl_lo - cfg.risk.sl_buffer if trg.direction == LONG else sl_hi + cfg.risk.sl_buffer
        if abs(trg.entry_close - sl_level) < cfg.risk.min_risk_dist:
            setup_rows[trg.direction]["final_state"] = "SKIPPED_MIN_RISK"
            continue
        after = bars[bars.index > trg.ts]
        if trg.intrabar:
            # Resto della barra d'ingresso: si assume che il prezzo sia arrivato al VWAP
            # dall'estremo opposto (il minimo del long era già fatto), poi prosegua
            # verso l'estremo a favore e chiuda al close. Stop possibile solo se il close
            # torna oltre; TP possibile se l'estremo a favore lo raggiunge.
            e = trg.entry_close
            lo = min(e, trg.bar_close) if trg.direction == LONG else trg.bar_low
            hi = trg.bar_high if trg.direction == LONG else max(e, trg.bar_close)
            entry_bar = bars.loc[[trg.ts]].copy()
            entry_bar.loc[trg.ts, ["open", "high", "low", "close"]] = [e, hi, lo, trg.bar_close]
            after = pd.concat([entry_bar, after])
        tr: TradeResult = simulate(trg.direction, trg.ts, trg.entry_close, trg.spread, sl_lo, sl_hi,
                                   after, capital if cfg.risk.compound else cfg.initial_capital,
                                   cfg.variant, cfg.risk, cfg.trail)
        capital += tr.pnl
        busy_until = pd.Timestamp(tr.exit_ts)
        eq.append({"ts": tr.exit_ts, "equity": capital})
        row = {
            "session": s.day, "window_open": s.open_utc, "direction": tr.direction, "variant": cfg.variant,
            "orb_high": orb.high, "orb_low": orb.low,
            "breakin_ts": trg.breakin_ts, "reentry_ts": trg.reentry_ts, "trigger_ts": trg.ts,
            "reentry_is_trigger_bar": trg.same_bar, "vwap_at_trigger": round(trg.vwap, 2),
            "entry_price": round(tr.entry_price, 2), "sl": round(tr.sl, 2), "tp1": round(tr.tp1, 2),
            "risk_dist": round(tr.risk_dist, 2), "risk_money": round(tr.risk_money, 2),
            "lots_per_leg": tr.legs[0].lots,
            "leg1_exit_ts": tr.legs[0].exit_ts, "leg1_exit_price": round(tr.legs[0].exit_price, 2),
            "leg1_reason": tr.legs[0].reason, "leg1_pnl": round(tr.legs[0].pnl, 2),
            "leg2_exit_ts": tr.legs[1].exit_ts if len(tr.legs) > 1 else None,
            "leg2_exit_price": round(tr.legs[1].exit_price, 2) if len(tr.legs) > 1 else None,
            "leg2_reason": tr.legs[1].reason if len(tr.legs) > 1 else None,
            "leg2_pnl": round(tr.legs[1].pnl, 2) if len(tr.legs) > 1 else None,
            "exit_ts": tr.exit_ts, "pnl": round(tr.pnl, 2), "rr_realized": round(tr.rr, 3),
            "outcome": tr.outcome, "vol_filter_on": trg.vol_filter_on, "vol_filter_passed": trg.vol_filter_passed,
            "trigger_volume": trg.volume, "trigger_vol_ma": round(trg.vol_ma, 1) if not pd.isna(trg.vol_ma) else None,
            "equity_after": round(capital, 2),
        }
        trades.append(row)
    return BacktestResult(cfg, pd.DataFrame(trades), pd.DataFrame(setups), pd.DataFrame(eq),
                          len(sessions), n_orb)


def config_dict(cfg: BacktestConfig) -> dict:
    d = asdict(cfg)
    d["session"]["open_time"] = str(cfg.session.open_time)
    d["session"]["cutoff_time"] = str(cfg.session.cutoff_time)
    return d
