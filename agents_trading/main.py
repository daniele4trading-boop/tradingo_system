#!/usr/bin/env python3
"""
Main Entry Point - Run the complete trading agent system.

Usage (on VPS Gamehosting):
    cd C:\TG_TradinGo
    C:\TG_TradinGo\.venv\Scripts\python.exe agents_trading/main.py --duration 120

    or with secrets:
    powershell -ExecutionPolicy Bypass -File scripts/with_secrets.ps1 \
        python C:\TG_TradinGo\agents_trading\main.py --duration 120
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents_trading.exness.orchestrator import TradingOrchestrator
from agents_trading.exness.risk_manager import RiskManager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("C:\\TG_TradinGo\\logs\\agents_trading.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run trading agents on Exness demo account"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Duration to run in minutes (default: 60)",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="XAUUSD,EURUSD,GBPUSD",
        help="Comma-separated symbols to trade",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run analysis only, no actual trades",
    )
    parser.add_argument(
        "--mt5-path",
        type=str,
        default=r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
        help="Path to MT5 terminal",
    )

    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("🚀 TRADING AGENTS START")
    logger.info("=" * 80)
    logger.info(f"Duration: {args.duration} minutes")
    logger.info(f"Symbols: {args.symbols}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info(f"MT5 path: {args.mt5_path}")

    # Get credentials from environment (set by with_secrets.ps1)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("❌ ANTHROPIC_API_KEY not set")
        return 1

    # Get MT5 password from secrets (must be saved beforehand)
    # In production, read from secrets_store.py
    password = os.environ.get("EXNESS_MT5_PASSWORD_480578231")
    if not password:
        logger.error("❌ EXNESS_MT5_PASSWORD_480578231 not set")
        logger.error("   Save it first: python C:\\TG_TradinGo\\secrets_store.py set EXNESS_MT5_PASSWORD_480578231")
        return 1

    try:
        # Initialize orchestrator
        orchestrator = TradingOrchestrator(
            login=480578231,
            server="Exness-MT5Trial11",
            password=password,
            mt5_path=args.mt5_path,
            api_key=api_key,
        )

        # Configure symbols
        orchestrator.symbols_to_trade = args.symbols.split(",")

        # Initialize risk manager
        risk_mgr = RiskManager(
            account_balance=1000.0,  # Demo account starting balance
            risk_per_trade_pct=1.0,
            max_daily_loss_pct=5.0,
        )

        # Connect to MT5
        if not orchestrator.connect(password):
            logger.error("❌ Failed to connect to MT5")
            return 1

        logger.info(f"✅ Connected to Exness")

        # Show account info
        account_info = orchestrator.mt5.account_info
        if account_info:
            logger.info(f"   Account: {account_info.login}")
            logger.info(f"   Balance: {account_info.balance:.2f}")
            logger.info(f"   Equity: {account_info.equity:.2f}")
            logger.info(f"   Margin: {account_info.margin:.2f}")

        # Dry run: just analyze, don't trade
        if args.dry_run:
            logger.info("📊 DRY RUN MODE - Analysis only, no trades")
            for symbol in orchestrator.symbols_to_trade:
                signal = orchestrator.run_analysis_cycle(symbol)
                if signal:
                    logger.info(f"  Would execute: {signal.to_dict()}")

            logger.info("✅ Dry run complete")
            status = orchestrator.get_status()
            logger.info(f"Status: {status}")

        else:
            # Live trading loop
            logger.info(f"🟢 STARTING LIVE TRADING LOOP ({args.duration} minutes)")
            orchestrator.run_live_loop(duration_minutes=args.duration)

            # Save session log
            log_path = f"C:\\TG_TradinGo\\logs\\session_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            orchestrator.save_session_log(log_path)

            # Final status
            status = orchestrator.get_status()
            logger.info("=" * 80)
            logger.info("📊 FINAL STATUS")
            logger.info("=" * 80)
            logger.info(f"Trades executed: {status['trades_executed']}")
            logger.info(f"Open positions: {status['open_positions']}")
            logger.info(f"Uptime: {status['uptime_minutes']:.1f} minutes")
            logger.info(f"Stats: {status['stats']}")
            logger.info(f"Session log: {log_path}")

        # Disconnect
        orchestrator.disconnect()
        logger.info("🛑 Disconnected")
        logger.info("=" * 80)

        return 0

    except KeyboardInterrupt:
        logger.info("\n🛑 Interrupted by user")
        orchestrator.disconnect()
        return 0

    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}", exc_info=True)
        orchestrator.disconnect()
        return 1


if __name__ == "__main__":
    sys.exit(main())
