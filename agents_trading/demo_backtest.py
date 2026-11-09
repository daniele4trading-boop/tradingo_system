#!/usr/bin/env python3
"""
Quick demo backtest - Run from agents_trading directory

Usage:
    cd agents_trading
    python demo_backtest.py
"""

import sys
import os
import logging

logging.basicConfig(level=logging.INFO)

from backtest.qlab_backtest_runner import QLabBacktestRunner

print("="*60)
print("QLAB BACKTEST DEMO - Sample Data")
print("="*60)
print()

try:
    runner = QLabBacktestRunner(
        symbol="XAUUSD",
        timeframe="H1",
        start_date="2025-01-01",
        end_date="2025-01-03",
        data_file="data/XAUUSD_H1_sample.csv",
        initial_balance=1000.0,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
    )

    print("🟢 Running backtest on 72 bars...\n")
    runner.run(max_bars=None, verbose=False)

    print()
    runner.print_results()

    runner.export_trades_csv("backtest_sample_trades.csv")
    runner.export_hypothesis_analysis("backtest_sample_hypotheses.csv")

    print("\n✅ Backtest complete!")
    print("Files generated:")
    print("  - backtest_sample_trades.csv")
    print("  - backtest_sample_hypotheses.csv")

except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
