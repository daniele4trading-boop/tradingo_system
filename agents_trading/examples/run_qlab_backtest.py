#!/usr/bin/env python3
"""
Example: Run Qlab-Integrated Backtest

This script demonstrates how to use the Qlab analysis with the backtest framework.

Usage:
    python run_qlab_backtest.py --symbol XAUUSD --timeframe H1 --data data.csv

Requirements:
    - Historical OHLC data CSV (columns: time, open, high, low, close, volume)
    - Anthropic API key (for agents)
"""

import sys
import argparse
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.qlab_backtest_runner import QLabBacktestRunner

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Run Qlab backtest from command line."""
    parser = argparse.ArgumentParser(
        description="Run Qlab-integrated backtest with liquidity + volume + hypotheses analysis"
    )

    parser.add_argument(
        "--symbol",
        default="XAUUSD",
        help="Trading symbol (default: XAUUSD)",
    )
    parser.add_argument(
        "--timeframe",
        default="H1",
        help="Timeframe (default: H1)",
    )
    parser.add_argument(
        "--data",
        required=False,
        help="Path to historical data CSV file",
    )
    parser.add_argument(
        "--start-date",
        default="2025-01-01",
        help="Start date YYYY-MM-DD (default: 2025-01-01)",
    )
    parser.add_argument(
        "--end-date",
        default="2025-12-31",
        help="End date YYYY-MM-DD (default: 2025-12-31)",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=1000.0,
        help="Initial account balance (default: 1000.0)",
    )
    parser.add_argument(
        "--max-bars",
        type=int,
        default=None,
        help="Maximum bars to process (default: all)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: $ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--export-trades",
        default="trades_qlab.csv",
        help="Export trades CSV (default: trades_qlab.csv)",
    )
    parser.add_argument(
        "--export-hypotheses",
        default="hypothesis_analysis.csv",
        help="Export hypothesis analysis CSV (default: hypothesis_analysis.csv)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    logger.info("="*60)
    logger.info("QLAB BACKTEST RUNNER")
    logger.info("="*60)
    logger.info(f"Symbol: {args.symbol}")
    logger.info(f"Timeframe: {args.timeframe}")
    logger.info(f"Date range: {args.start_date} to {args.end_date}")
    logger.info(f"Initial balance: ${args.balance:.2f}")
    if args.data:
        logger.info(f"Data file: {args.data}")
    logger.info("="*60 + "\n")

    try:
        # Initialize runner
        runner = QLabBacktestRunner(
            symbol=args.symbol,
            timeframe=args.timeframe,
            start_date=args.start_date,
            end_date=args.end_date,
            data_file=args.data,
            initial_balance=args.balance,
            api_key=args.api_key,
        )

        # Run backtest
        logger.info("Starting backtest...\n")
        runner.run(max_bars=args.max_bars, verbose=args.verbose)

        # Print results
        runner.print_results()

        # Export
        if args.export_trades:
            runner.export_trades_csv(args.export_trades)
            logger.info(f"Trades exported to: {args.export_trades}")

        if args.export_hypotheses:
            runner.export_hypothesis_analysis(args.export_hypotheses)
            logger.info(f"Hypothesis analysis exported to: {args.export_hypotheses}")

        logger.info("\n✅ Backtest complete")
        return 0

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
