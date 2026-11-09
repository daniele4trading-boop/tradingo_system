"""
Backtest Engine - Test trading agents on historical data.

Flow:
1. Load historical OHLC data
2. For each bar:
   - Run chart analyst on previous bars (chart screenshot simulation)
   - Run strategy executor for decision
   - Simulate order execution
   - Track position P&L
3. Generate performance report
"""

import pandas as pd
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Simulated trade record."""
    symbol: str
    entry_time: datetime
    entry_price: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    direction: str = "BUY"  # BUY or SELL
    volume: float = 0.1
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    bars_held: int = 0
    close_reason: str = "OPEN"  # OPEN, TP_HIT, SL_HIT, MANUAL_CLOSE

    def to_dict(self) -> Dict:
        return asdict(self)


class BacktestEngine:
    """Run backtest simulation with trading agents."""

    def __init__(
        self,
        initial_balance: float = 1000.0,
        risk_per_trade: float = 1.0,
        max_positions: int = 3,
        slippage_pips: float = 1.0,
    ):
        """
        Initialize backtest engine.

        Args:
            initial_balance: Starting account balance
            risk_per_trade: % of balance to risk per trade
            max_positions: Max concurrent open positions
            slippage_pips: Simulated slippage in pips (0.1$ increments)
        """
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.risk_per_trade = risk_per_trade
        self.max_positions = max_positions
        self.slippage_pips = slippage_pips

        self.trades: List[BacktestTrade] = []
        self.open_positions: Dict[str, BacktestTrade] = {}
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = initial_balance

        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "final_balance": initial_balance,
            "sharpe_ratio": 0.0,
        }

    def execute_trade(
        self,
        symbol: str,
        direction: str,
        entry_price: float,
        volume: float,
        stop_loss: float,
        take_profit: float,
        timestamp: datetime,
    ) -> Tuple[bool, str]:
        """
        Execute a trade in backtest.

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            direction: "BUY" or "SELL"
            entry_price: Entry price
            volume: Position size (lots)
            stop_loss: Stop loss price
            take_profit: Take profit price
            timestamp: Entry timestamp

        Returns:
            (success, reason)
        """
        # Check max positions
        if len(self.open_positions) >= self.max_positions:
            return False, f"Max positions ({self.max_positions}) reached"

        # Check balance
        required_margin = entry_price * volume * 100  # Rough estimation
        if required_margin > self.current_balance:
            return False, "Insufficient balance"

        # Add slippage
        if direction == "BUY":
            entry_price += self.slippage_pips * 0.1  # 1 pip = 0.1$
        else:
            entry_price -= self.slippage_pips * 0.1

        # Create trade
        trade = BacktestTrade(
            symbol=symbol,
            direction=direction,
            entry_time=timestamp,
            entry_price=entry_price,
            volume=volume,
            stop_loss=stop_loss,
            take_profit=take_profit,
            bars_held=0,
        )

        # Store position
        position_key = f"{symbol}_{direction}_{len(self.open_positions)}"
        self.open_positions[position_key] = trade

        logger.info(f"✅ Trade entered: {direction} {volume} {symbol} @ {entry_price:.2f}")
        return True, "Executed"

    def update_position(
        self,
        position_key: str,
        current_price: float,
        timestamp: datetime,
        close_reason: str = "OPEN",
    ) -> Optional[BacktestTrade]:
        """
        Update open position and check for exit conditions.

        Args:
            position_key: Position identifier
            current_price: Current price
            timestamp: Current timestamp
            close_reason: Reason for closing (if applicable)

        Returns:
            Closed trade record if position was closed, else None
        """
        if position_key not in self.open_positions:
            return None

        trade = self.open_positions[position_key]
        trade.bars_held += 1

        # Check stop loss
        if trade.direction == "BUY":
            if current_price <= trade.stop_loss:
                return self._close_position(position_key, trade.stop_loss, timestamp, "SL_HIT")
            if current_price >= trade.take_profit:
                return self._close_position(position_key, trade.take_profit, timestamp, "TP_HIT")
        else:  # SELL
            if current_price >= trade.stop_loss:
                return self._close_position(position_key, trade.stop_loss, timestamp, "SL_HIT")
            if current_price <= trade.take_profit:
                return self._close_position(position_key, trade.take_profit, timestamp, "TP_HIT")

        return None

    def _close_position(
        self,
        position_key: str,
        exit_price: float,
        timestamp: datetime,
        close_reason: str,
    ) -> BacktestTrade:
        """Close a position and calculate P&L."""
        trade = self.open_positions.pop(position_key)

        # Calculate P&L
        if trade.direction == "BUY":
            pnl = (exit_price - trade.entry_price) * trade.volume * 100  # $100 per pip per lot
        else:
            pnl = (trade.entry_price - exit_price) * trade.volume * 100

        trade.exit_price = exit_price
        trade.exit_time = timestamp
        trade.pnl = pnl
        trade.pnl_pct = (pnl / (trade.entry_price * trade.volume * 100)) * 100
        trade.close_reason = close_reason

        # Update balance
        self.current_balance += pnl
        self.daily_pnl += pnl

        # Track drawdown
        self.peak_balance = max(self.peak_balance, self.current_balance)
        drawdown = (self.peak_balance - self.current_balance) / self.peak_balance * 100
        self.max_drawdown = max(self.max_drawdown, drawdown)

        # Store trade
        self.trades.append(trade)
        self.stats["total_trades"] += 1

        if pnl > 0:
            self.stats["winning_trades"] += 1
        else:
            self.stats["losing_trades"] += 1

        logger.info(f"📊 Position closed: {trade.direction} {trade.symbol} | "
                   f"P&L: {pnl:+.2f}$ ({trade.pnl_pct:+.1f}%) | Reason: {close_reason}")

        return trade

    def get_performance_stats(self) -> Dict:
        """Calculate backtest performance statistics."""
        if not self.trades:
            return self.stats

        closed_trades = [t for t in self.trades if t.pnl is not None]

        if not closed_trades:
            return self.stats

        # Win rate
        self.stats["win_rate"] = len([t for t in closed_trades if t.pnl > 0]) / len(closed_trades) * 100

        # Average win/loss
        wins = [t.pnl for t in closed_trades if t.pnl > 0]
        losses = [abs(t.pnl) for t in closed_trades if t.pnl < 0]

        self.stats["avg_win"] = sum(wins) / len(wins) if wins else 0
        self.stats["avg_loss"] = sum(losses) / len(losses) if losses else 0

        # Total P&L
        self.stats["total_pnl"] = sum(t.pnl for t in closed_trades)
        self.stats["final_balance"] = self.current_balance

        # Sharpe ratio (simplified)
        if len(closed_trades) > 1:
            pnls = [t.pnl for t in closed_trades]
            mean_pnl = sum(pnls) / len(pnls)
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
            std_dev = variance ** 0.5
            self.stats["sharpe_ratio"] = (mean_pnl / std_dev) if std_dev > 0 else 0

        return self.stats

    def print_report(self):
        """Print backtest report."""
        stats = self.get_performance_stats()

        print("\n" + "="*60)
        print("BACKTEST REPORT")
        print("="*60)
        print(f"Initial Balance: ${self.initial_balance:.2f}")
        print(f"Final Balance: ${stats['final_balance']:.2f}")
        print(f"Total P&L: ${stats['total_pnl']:+.2f}")
        print(f"Return: {(stats['total_pnl']/self.initial_balance)*100:+.2f}%")
        print(f"\nTotal Trades: {stats['total_trades']}")
        print(f"Winning Trades: {stats['winning_trades']}")
        print(f"Losing Trades: {stats['losing_trades']}")
        print(f"Win Rate: {stats['win_rate']:.1f}%")
        print(f"Avg Win: ${stats['avg_win']:+.2f}")
        print(f"Avg Loss: ${stats['avg_loss']:+.2f}")
        print(f"Max Drawdown: {self.max_drawdown:.2f}%")
        print(f"Sharpe Ratio: {stats['sharpe_ratio']:.2f}")
        print("="*60 + "\n")
