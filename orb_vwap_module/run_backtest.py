"""CLI: python -m orb_vwap_module.run_backtest --data-dir <dir> --out <dir> [--grid]"""
from __future__ import annotations

import argparse
import sys
from datetime import time

from .backtester import BacktestConfig
from .position_manager import RiskParams, TrailParams
from .report import run_ab, run_grid, write_outputs
from .session import SessionSpec
from .setup_engine import SetupParams


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest ORB+VWAP false-breakout")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--end", default=None, help="ISO UTC, default ultima barra")
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--cutoff", default="22:00", help="HH:MM ora locale sessione")
    ap.add_argument("--volume-filter", action="store_true")
    ap.add_argument("--vol-n", type=int, default=20)
    ap.add_argument("--vol-k", type=float, default=1.2)
    ap.add_argument("--immediate-trigger", action="store_true",
                    help="rientro + trigger VWAP obbligatori sulla barra subito dopo il break-in")
    ap.add_argument("--min-range-bars", type=int, default=0,
                    help="lateralità: N barre chiuse dentro l'ORB prima del break-in")
    ap.add_argument("--rolling-tf", default=None, help="ORB rolling: finestre di 1h/4h dall'apertura (con --orb-tf 30min/2h)")
    ap.add_argument("--orb-tf", default="15min")
    ap.add_argument("--setup-mode", choices=["orb", "vwap_cross"], default="orb")
    ap.add_argument("--sl-lookback", type=int, default=6, help="vwap_cross: SL = min/max delle ultime N barre")
    ap.add_argument("--range-mode", choices=["inside", "not_beyond"], default="inside")
    ap.add_argument("--atr-period", type=int, default=14)
    ap.add_argument("--atr-mult", type=float, default=2.0)
    ap.add_argument("--sl-buffer", type=float, default=0.0)
    ap.add_argument("--min-risk-dist", type=float, default=0.0,
                    help="distanza minima entry-SL in prezzo per aprire (0 = nessun filtro)")
    ap.add_argument("--grid", action="store_true", help="griglia ATR {7,14,21}×{1.5,2,2.5,3}×volume on/off")
    ap.add_argument("--data-note", default="export MT5 broker (Vantage demo): volume = tick volume del broker, "
                    "spread = spread MT5 della barra. NON sono dati Dukascopy: la qualità del volume differisce "
                    "e va tenuta presente nel confronto con gli altri sistemi.")
    a = ap.parse_args(argv)
    hh, mm = a.cutoff.split(":")
    cfg = BacktestConfig(
        symbol=a.symbol, months=a.months, end=a.end, initial_capital=a.capital,
        session=SessionSpec(cutoff_time=time(int(hh), int(mm)), orb_tf=a.orb_tf, rolling_tf=a.rolling_tf),
        setup=SetupParams(volume_filter=a.volume_filter, vol_ma_n=a.vol_n, vol_k=a.vol_k,
                          immediate_trigger=a.immediate_trigger, min_range_bars=a.min_range_bars,
                          range_mode=a.range_mode, mode=a.setup_mode, sl_lookback=a.sl_lookback),
        risk=RiskParams(sl_buffer=a.sl_buffer, min_risk_dist=a.min_risk_dist),
        trail=TrailParams(a.atr_period, a.atr_mult),
    )
    ab = run_ab(a.data_dir, cfg)
    grid = run_grid(a.data_dir, cfg) if a.grid else None
    path = write_outputs(a.out, ab, grid, a.data_note)
    text = path.read_text(encoding="utf-8")
    enc = sys.stdout.encoding or "utf-8"
    print(text.encode(enc, errors="replace").decode(enc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
