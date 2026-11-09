#!/usr/bin/env python3
"""
Sample Backtest - Run Qlab backtest on sample data

Quick way to verify everything works before paper trading.
"""

import sys
import os
from pathlib import Path

# Add parent directories to path
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))
sys.path.insert(0, str(parent.parent))  # For agents_trading imports

from backtest.qlab_backtest_runner import QLabBacktestRunner


def run_sample_backtest():
    """Run backtest on sample data."""

    # Sample data file
    data_file = Path(__file__).parent.parent / "data" / "XAUUSD_H1_sample.csv"

    if not data_file.exists():
        print(f"❌ Sample data file not found: {data_file}")
        return False

    print("="*60)
    print("QLAB BACKTEST - SAMPLE DATA")
    print("="*60)
    print(f"Data file: {data_file}")
    print()

    try:
        # Initialize runner
        runner = QLabBacktestRunner(
            symbol="XAUUSD",
            timeframe="H1",
            start_date="2025-01-01",
            end_date="2025-01-03",
            data_file=str(data_file),
            initial_balance=1000.0,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )

        # Run backtest
        print("🟢 Running backtest on 72 bars...\n")
        runner.run(max_bars=None, verbose=False)

        # Print results
        print()
        runner.print_results()

        # Export
        runner.export_trades_csv("backtest_sample_trades.csv")
        runner.export_hypothesis_analysis("backtest_sample_hypotheses.csv")

        print("\n✅ Backtest complete!")
        print("Files generated:")
        print("  - backtest_sample_trades.csv")
        print("  - backtest_sample_hypotheses.csv")

        return True

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_sample_backtest()
    sys.exit(0 if success else 1)
