#!/usr/bin/env python3
"""
Launch Paper Trading - Start automated trading on Exness demo

This script prepares the environment and launches the trading orchestrator.
It can run in two modes:
1. Demo mode (mock MT5): For testing without real MT5 connection
2. Live mode: Connects to real MT5 terminal on VPS

Usage:
    python launch_paper_trading.py --mode demo --duration 60 --symbols XAUUSD,EURUSD
    python launch_paper_trading.py --mode live --duration 1440
"""

import sys
import os
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
import time

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("paper_trading.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from main import TradingOrchestrator


def validate_setup() -> bool:
    """Validate that all prerequisites are met."""
    logger.info("="*60)
    logger.info("PAPER TRADING SETUP VALIDATION")
    logger.info("="*60)

    checks_passed = 0
    checks_total = 0

    # Check 1: Python version
    checks_total += 1
    if sys.version_info >= (3, 9):
        logger.info("✅ Python version: {}.{}".format(sys.version_info.major, sys.version_info.minor))
        checks_passed += 1
    else:
        logger.error("❌ Python 3.9+ required (current: {}.{})".format(
            sys.version_info.major, sys.version_info.minor))

    # Check 2: Required modules
    checks_total += 1
    try:
        import anthropic
        import pandas
        import numpy
        logger.info("✅ All required packages installed")
        checks_passed += 1
    except ImportError as e:
        logger.error(f"❌ Missing package: {e}")

    # Check 3: API Key
    checks_total += 1
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        logger.info(f"✅ API Key found: {api_key[:10]}...")
        checks_passed += 1
    else:
        logger.warning("⚠️  No API Key in ANTHROPIC_API_KEY (will use mock mode)")

    # Check 4: Log directory
    checks_total += 1
    log_dir = Path("logs")
    if log_dir.exists() or log_dir.mkdir(exist_ok=True):
        logger.info(f"✅ Log directory: {log_dir.absolute()}")
        checks_passed += 1
    else:
        logger.error(f"❌ Cannot create log directory: {log_dir}")

    # Summary
    logger.info("")
    logger.info(f"Validation: {checks_passed}/{checks_total} checks passed")

    if checks_passed >= 3:
        logger.info("✅ Setup OK - Ready to launch paper trading")
        return True
    else:
        logger.error("❌ Setup incomplete - fix issues above")
        return False


def print_banner():
    """Print ASCII banner."""
    banner = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                                                                              ║
║                    🚀 PAPER TRADING - EXNESS DEMO 🚀                        ║
║                                                                              ║
║  Autonomous Trading with Claude + QLAB Liquidity Analysis                   ║
║                                                                              ║
║  Components:                                                                 ║
║    ✓ ChartAnalyst: Claude vision candlestick analysis                      ║
║    ✓ StrategyExecutor: Trade signal generation                             ║
║    ✓ RiskManager: Position sizing & validation                             ║
║    ✓ MT5Connector: Order execution                                         ║
║    ✓ QLAB Hypotheses: H0-H30 statistical analysis                          ║
║                                                                              ║
║  Account: Exness Demo 480578231                                             ║
║  Schedule: Daily 06:00 UTC - 05:59 UTC (1440 minutes)                       ║
║  Monitoring: Real-time P&L, trades, hypothesis accuracy                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Launch paper trading on Exness demo account"
    )

    parser.add_argument(
        "--mode",
        choices=["demo", "live"],
        default="demo",
        help="Mode: demo (mock MT5) or live (real MT5)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=120,
        help="Trading duration in minutes (default: 120)",
    )
    parser.add_argument(
        "--symbols",
        default="XAUUSD,EURUSD",
        help="Symbols to trade (comma-separated)",
    )
    parser.add_argument(
        "--cycle",
        type=int,
        default=30,
        help="Analysis cycle in seconds (default: 30)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip setup validation",
    )

    args = parser.parse_args()

    # Print banner
    print_banner()

    # Validate setup
    if not args.skip_validation:
        if not validate_setup():
            logger.error("Setup validation failed. Aborting.")
            return 1

    # Parse symbols
    symbols = [s.strip() for s in args.symbols.split(",")]

    logger.info("")
    logger.info("="*60)
    logger.info("LAUNCHING PAPER TRADING")
    logger.info("="*60)
    logger.info(f"Mode: {args.mode.upper()}")
    logger.info(f"Duration: {args.duration} minutes")
    logger.info(f"Symbols: {', '.join(symbols)}")
    logger.info(f"Cycle: {args.cycle} seconds")
    logger.info(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info(f"End: {(datetime.now() + timedelta(minutes=args.duration)).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("="*60)
    logger.info("")

    # Initialize orchestrator
    try:
        orchestrator = TradingOrchestrator(
            symbols=symbols,
            account=480578231,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            initial_balance=1000.0,
        )

        # Connect
        if args.mode == "demo":
            logger.info("🟢 Connecting in DEMO mode (mock MT5)...")
            if orchestrator.connect("demo"):
                logger.info("✅ Demo mode ready - using mock MT5 connector")
            else:
                logger.error("❌ Failed to initialize demo mode")
                return 1
        else:
            logger.info("🟢 Connecting to live MT5 terminal...")
            password = os.getenv(f"EXNESS_MT5_PASSWORD_480578231")
            if not password:
                logger.error("❌ MT5 password not found in EXNESS_MT5_PASSWORD_480578231")
                logger.error("   Set it: export EXNESS_MT5_PASSWORD_480578231='password'")
                return 1

            if not orchestrator.connect(password):
                logger.error("❌ Failed to connect to MT5")
                return 1

        # Run trading
        logger.info("")
        logger.info("🟢 Starting trading orchestrator...")
        logger.info("")

        orchestrator.run(
            duration_minutes=args.duration,
            cycle_seconds=args.cycle,
        )

        logger.info("")
        logger.info("✅ Paper trading session complete")

        return 0

    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        return 1
    finally:
        if 'orchestrator' in locals():
            orchestrator.disconnect()


if __name__ == "__main__":
    sys.exit(main())
