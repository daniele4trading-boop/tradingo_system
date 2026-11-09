"""
Main Orchestrator - Coordinates all trading agents for live execution.

Flow:
1. Get current symbol chart data from MT5
2. Chart Analyst analyzes chart → returns sentiment, patterns, levels
3. Strategy Executor decides trade → returns signal with entry, SL, TP
4. Risk Manager validates signal → checks position limits, daily loss
5. MT5 Connector executes order if validated
6. Monitor position and close based on TP/SL

Status: Paper trading on demo account only.
"""

import logging
import asyncio
import json
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from pathlib import Path

from agents_trading.connectors.mt5_direct import MT5Connector, OrderResult
from agents_trading.exness.chart_analyst import ChartAnalyst
from agents_trading.exness.strategy_executor import StrategyExecutor, TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class ExecutionStats:
    """Track execution statistics."""
    total_signals: int = 0
    trades_executed: int = 0
    trades_won: int = 0
    trades_lost: int = 0
    total_pnl: float = 0.0
    winning_pct: float = 0.0
    daily_pnl: float = 0.0
    max_drawdown: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


class TradingOrchestrator:
    """Main orchestrator that runs the complete trading loop."""

    def __init__(
        self,
        login: int,
        server: str,
        password: str,
        mt5_path: str = None,
        api_key: Optional[str] = None,
    ):
        """
        Initialize orchestrator with all agents.

        Args:
            login: MT5 account login
            server: MT5 server
            password: MT5 password (from secrets)
            mt5_path: Path to MT5 terminal
            api_key: Anthropic API key
        """
        self.login = login
        self.server = server

        # Initialize agents
        self.mt5 = MT5Connector(login=login, server=server, mt5_path=mt5_path)
        self.chart_analyst = ChartAnalyst(api_key=api_key)
        self.strategy_executor = StrategyExecutor(api_key=api_key)

        # Connection state
        self.connected = False

        # Trading state
        self.open_positions: Dict[str, Dict] = {}  # ticket -> position info
        self.signals_history: List[TradeSignal] = []
        self.stats = ExecutionStats()

        # Configuration
        self.symbols_to_trade = ["XAUUSD", "EURUSD", "GBPUSD"]
        self.timeframes = {"XAUUSD": "1H", "EURUSD": "4H", "GBPUSD": "4H"}
        self.max_open_positions = 3
        self.max_daily_loss_pct = 5.0
        self.risk_per_trade_pct = 1.0

        # Monitoring
        self.last_analysis_time: Dict[str, datetime] = {}
        self.analysis_interval_minutes = 5
        self.start_time = datetime.utcnow()

    def connect(self, password: str) -> bool:
        """Connect to MT5."""
        if self.mt5.connect(password=password):
            self.connected = True
            logger.info(f"✅ Orchestrator connected to {self.server}")
            return True
        logger.error("Failed to connect to MT5")
        return False

    def disconnect(self):
        """Disconnect from MT5."""
        self.mt5.disconnect()
        self.connected = False

    def should_analyze(self, symbol: str) -> bool:
        """Check if enough time has passed since last analysis."""
        last_time = self.last_analysis_time.get(symbol)
        if last_time is None:
            return True
        elapsed = (datetime.utcnow() - last_time).total_seconds() / 60
        return elapsed >= self.analysis_interval_minutes

    def run_analysis_cycle(self, symbol: str) -> Optional[TradeSignal]:
        """
        Run one complete cycle: analyze → decide → validate → execute.

        Returns:
            Executed TradeSignal if trade was placed, None otherwise
        """
        if not self.connected:
            logger.warning("Not connected to MT5")
            return None

        if not self.should_analyze(symbol):
            return None

        try:
            timeframe = self.timeframes.get(symbol, "1H")
            logger.info(f"▶️ Starting analysis cycle: {symbol} {timeframe}")

            # Step 1: Get symbol info
            symbol_info = self.mt5.get_symbol_info(symbol)
            if not symbol_info:
                logger.warning(f"Could not get {symbol} info")
                return None

            logger.debug(f"  Bid: {symbol_info['bid']}, Ask: {symbol_info['ask']}")

            # Step 2: Chart analyst (TODO: get actual screenshot from MT5)
            # For now, use mock analysis
            chart_analysis = self._mock_chart_analysis(symbol, symbol_info)
            logger.info(f"  Chart: {chart_analysis.get('sentiment')} | "
                       f"Pattern: {chart_analysis.get('trading_setup', {}).get('type')}")

            # Step 3: Strategy executor
            signal = self.strategy_executor.execute_strategy(
                chart_analysis=chart_analysis,
                symbol=symbol,
                timeframe=timeframe,
                current_price={"bid": symbol_info["bid"], "ask": symbol_info["ask"]},
                open_positions=len([p for p in self.open_positions.values() if p["symbol"] == symbol]),
            )

            if not signal or signal.action.value == "HOLD":
                logger.info(f"  No trade signal for {symbol}")
                self.last_analysis_time[symbol] = datetime.utcnow()
                return None

            # Step 4: Risk validation
            if not self._validate_risk(signal):
                logger.warning(f"  Signal rejected by risk manager: {signal.reason}")
                return None

            # Step 5: Execute trade
            executed = self._execute_signal(signal)
            if executed:
                self.signals_history.append(signal)
                self.stats.trades_executed += 1
                self.last_analysis_time[symbol] = datetime.utcnow()
                return signal

            return None

        except Exception as e:
            logger.error(f"Analysis cycle error for {symbol}: {e}", exc_info=True)
            return None

    def _mock_chart_analysis(self, symbol: str, symbol_info: Dict) -> Dict:
        """Generate mock chart analysis for demo (TODO: use real chart screenshots)."""
        return {
            "sentiment": "BULLISH" if symbol_info["bid"] > 2040 else "BEARISH",
            "confidence": 0.65,
            "patterns": [{"type": "breakout", "reliability": "medium"}],
            "support_resistance": {
                "resistance": [symbol_info["bid"] + 50],
                "support": [symbol_info["bid"] - 50],
                "key_level": symbol_info["bid"]
            },
            "volatility": "medium",
            "volume_analysis": "increasing",
            "momentum": "moderate",
            "next_move": "up",
            "trading_setup": {
                "type": "breakout",
                "probability": 0.65,
                "entry_zone": f"{symbol_info['bid']}-{symbol_info['bid'] + 5}"
            },
            "risks": ["potential pullback", "news event"],
            "notes": "Demo analysis - replace with real chart screenshot"
        }

    def _validate_risk(self, signal: TradeSignal) -> bool:
        """Validate signal meets risk management rules."""

        # Check max open positions
        if len(self.open_positions) >= self.max_open_positions:
            logger.warning(f"Max positions ({self.max_open_positions}) reached")
            return False

        # Check daily loss limit
        if self.stats.daily_pnl < -(self.stats.daily_pnl * self.max_daily_loss_pct / 100):
            logger.warning(f"Daily loss limit ({self.max_daily_loss_pct}%) reached")
            return False

        # Check risk:reward ratio
        if signal.risk_reward_ratio < 1.5:
            logger.warning(f"R:R ratio too low: {signal.risk_reward_ratio}")
            return False

        # Check confidence
        if signal.confidence < 0.55:
            logger.warning(f"Confidence too low: {signal.confidence}")
            return False

        return True

    def _execute_signal(self, signal: TradeSignal) -> bool:
        """Execute trade on MT5."""
        try:
            result: OrderResult = self.mt5.place_order(
                symbol=signal.symbol,
                action=signal.action.value,
                volume=signal.position_size,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                comment=f"Claude-Agent | {signal.reason[:30]}",
                magic=12000,
            )

            if result.success:
                self.open_positions[result.order_ticket] = {
                    "symbol": signal.symbol,
                    "ticket": result.order_ticket,
                    "entry": result.price,
                    "action": signal.action.value,
                    "volume": signal.position_size,
                    "sl": signal.stop_loss,
                    "tp": signal.take_profit,
                    "opened_at": datetime.utcnow(),
                    "pnl": 0.0,
                }
                logger.info(f"✅ Trade executed: {signal.action.value} {signal.position_size} {signal.symbol}")
                return True
            else:
                logger.error(f"Order failed: {result.error}")
                return False

        except Exception as e:
            logger.error(f"Execution error: {e}")
            return False

    def update_open_positions(self):
        """Update PnL and close positions if needed."""
        # TODO: fetch live positions from MT5, update PnL, close if TP/SL hit
        pass

    def run_live_loop(self, duration_minutes: int = 60):
        """
        Run the live trading loop for specified duration.

        Args:
            duration_minutes: How long to run the loop
        """
        if not self.connected:
            logger.error("Not connected. Call connect() first.")
            return

        logger.info(f"🟢 Starting live trading loop for {duration_minutes} minutes")
        start_time = datetime.utcnow()

        try:
            while True:
                elapsed = (datetime.utcnow() - start_time).total_seconds() / 60
                if elapsed >= duration_minutes:
                    logger.info(f"🛑 Duration reached ({duration_minutes}m). Stopping loop.")
                    break

                # Analyze each symbol
                for symbol in self.symbols_to_trade:
                    try:
                        self.run_analysis_cycle(symbol)
                    except Exception as e:
                        logger.error(f"Cycle error for {symbol}: {e}")

                # Update positions
                try:
                    self.update_open_positions()
                except Exception as e:
                    logger.error(f"Position update error: {e}")

                # Sleep before next cycle
                asyncio.run(asyncio.sleep(30))  # 30 second cycle

        except KeyboardInterrupt:
            logger.info("🛑 Loop interrupted by user")
        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)
        finally:
            logger.info(f"📊 Final stats: {self.stats.to_dict()}")

    def get_status(self) -> Dict:
        """Get current trading status."""
        return {
            "connected": self.connected,
            "open_positions": len(self.open_positions),
            "signals_sent": len(self.signals_history),
            "trades_executed": self.stats.trades_executed,
            "stats": self.stats.to_dict(),
            "uptime_minutes": (datetime.utcnow() - self.start_time).total_seconds() / 60,
        }

    def save_session_log(self, filepath: str):
        """Save session log to file."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "session": {
                "broker": self.server,
                "account": self.login,
                "duration_minutes": (datetime.utcnow() - self.start_time).total_seconds() / 60,
            },
            "signals": [s.to_dict() for s in self.signals_history],
            "stats": self.stats.to_dict(),
        }

        with open(filepath, "w") as f:
            json.dump(log_data, f, indent=2)

        logger.info(f"📝 Session log saved to {filepath}")
