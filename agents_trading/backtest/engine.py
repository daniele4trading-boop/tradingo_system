"""
Backtest Engine - Simulate trade execution on historical data.
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
    direction: str = "BUY"
    volume: float = 0.1
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    close_reason: str = "OPEN"

    def to_dict(self) -> Dict:
        return asdict(self)


class BacktestEngine:
    """Simulate trade execution."""

    def __init__(
        self,
        initial_balance: float = 1000.0,
        slippage_pips: float = 1.0,
    ):
        """
        Initialize backtest engine.

        Args:
            initial_balance: Starting balance
            slippage_pips: Simulated slippage in pips (0.1$ increments)
        """
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.slippage_pips = slippage_pips

        self.trades: List[BacktestTrade] = []
        self.open_positions: Dict[str, BacktestTrade] = {}
        self.daily_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = initial_balance

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
        """Execute a trade."""
        # Add slippage
        if direction == "BUY":
            entry_price += self.slippage_pips * 0.1
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
        )

        # Store position
        position_key = f"{symbol}_{direction}_{len(self.open_positions)}"
        self.open_positions[position_key] = trade

        logger.info(f"✅ Trade entered: {direction} {volume}L {symbol} @ {entry_price:.2f}")
        return True, "Executed"

    def update_position(
        self,
        position_key: str,
        current_price: float,
        timestamp: datetime,
        close_reason: str = "OPEN",
    ) -> Optional[BacktestTrade]:
        """Update position and check for exit conditions."""
        if position_key not in self.open_positions:
            return None

        trade = self.open_positions[position_key]

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
        """Close a position."""
        trade = self.open_positions.pop(position_key)

        # Calculate P&L
        if trade.direction == "BUY":
            pnl = (exit_price - trade.entry_price) * trade.volume * 100
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

        logger.info(f"📊 Position closed: {trade.direction} | "
                   f"P&L: {pnl:+.2f}$ | Reason: {close_reason}")

        return trade

    def print_report(self):
        """Print backtest report."""
        if not self.trades:
            print("No trades executed")
            return

        closed_trades = [t for t in self.trades if t.pnl is not None]

        if not closed_trades:
            print("No closed trades")
            return

        wins = [t for t in closed_trades if t.pnl > 0]
        losses = [t for t in closed_trades if t.pnl < 0]

        win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_loss = sum(abs(t.pnl) for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t.pnl for t in closed_trades)

        print("\n" + "="*60)
        print("BACKTEST REPORT")
        print("="*60)
        print(f"Initial Balance: ${self.initial_balance:.2f}")
        print(f"Final Balance: ${self.current_balance:.2f}")
        print(f"Total P&L: ${total_pnl:+.2f}")
        print(f"Return: {(total_pnl/self.initial_balance)*100:+.2f}%")
        print(f"\nTotal Trades: {len(closed_trades)}")
        print(f"Winning Trades: {len(wins)}")
        print(f"Losing Trades: {len(losses)}")
        print(f"Win Rate: {win_rate:.1f}%")
        print(f"Avg Win: ${avg_win:+.2f}")
        print(f"Avg Loss: ${avg_loss:+.2f}")
        print(f"Max Drawdown: {self.max_drawdown:.2f}%")
        print("="*60 + "\n")
