"""
MT5 Direct Connector - Direct Python connection to MetaTrader 5
No cloud relay, minimal latency.

Usage:
    from connectors.mt5_direct import MT5Connector
    mt5 = MT5Connector(login=480578231, server="Exness-MT5Trial11")
    mt5.connect(password="...")  # from secrets_store
    mt5.place_order(symbol="XAUUSD", action="BUY", volume=0.1, sl=..., tp=...)
"""

import MetaTrader5 as mt5
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_ticket: Optional[int] = None
    price: Optional[float] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class MT5Connector:
    """Direct connection to MetaTrader 5 terminal."""

    def __init__(self, login: int, server: str, mt5_path: str = None):
        """
        Initialize MT5 connector.

        Args:
            login: MT5 account login number
            server: MT5 server name (e.g. "Exness-MT5Trial11")
            mt5_path: Path to MT5 terminal (auto-detect if None)
        """
        self.login = login
        self.server = server
        self.mt5_path = mt5_path or r"C:\Program Files\MetaTrader 5\terminal64.exe"
        self.connected = False
        self.account_info = None

    def connect(self, password: str) -> bool:
        """Connect to MT5 terminal and login to account."""
        try:
            if not mt5.initialize(path=self.mt5_path):
                logger.error(f"MT5 init failed: {mt5.last_error()}")
                return False

            if not mt5.login(self.login, password=password, server=self.server):
                logger.error(f"MT5 login failed: {mt5.last_error()}")
                return False

            self.account_info = mt5.account_info()
            self.connected = True
            logger.info(f"✅ Connected to {self.server} | Account {self.login} | Balance: {self.account_info.balance}")
            return True

        except Exception as e:
            logger.error(f"MT5 connection error: {e}")
            return False

    def disconnect(self):
        """Disconnect from MT5."""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            logger.info("MT5 disconnected")

    def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Get symbol info (bid, ask, spread, etc)."""
        if not self.connected:
            logger.warning("Not connected to MT5")
            return None

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"Symbol {symbol} not found")
            return None

        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "spread_pips": (tick.ask - tick.bid) / (10 ** (-mt5.symbol_info(symbol).digits)),
            "time": datetime.fromtimestamp(tick.time),
        }

    def place_order(
        self,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        volume: float,
        entry_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        comment: str = "Claude-Agent",
        magic: int = 12000,
    ) -> OrderResult:
        """
        Place a market order.

        Args:
            symbol: Symbol to trade (e.g. "XAUUSD")
            action: "BUY" or "SELL"
            volume: Order size in lots
            entry_price: Entry price (if None, uses market)
            stop_loss: Stop loss price
            take_profit: Take profit price
            comment: Order comment
            magic: Magic number for order identification

        Returns:
            OrderResult with ticket, price, success status
        """
        if not self.connected:
            return OrderResult(success=False, error="Not connected to MT5")

        try:
            symbol_info = mt5.symbol_info(symbol)
            if symbol_info is None:
                return OrderResult(success=False, error=f"Symbol {symbol} not found")

            tick = mt5.symbol_info_tick(symbol)
            price = tick.ask if action == "BUY" else tick.bid

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
                "price": entry_price or price,
                "sl": stop_loss,
                "tp": take_profit,
                "deviation": 20,
                "magic": magic,
                "comment": comment,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)

            if result.retcode != mt5.TRADE_RETCODE_DONE:
                return OrderResult(
                    success=False,
                    error=f"Order failed: {result.comment} (code: {result.retcode})"
                )

            logger.info(f"✅ Order placed: {action} {volume} {symbol} @ {result.price} | Ticket: {result.order}")
            return OrderResult(
                success=True,
                order_ticket=result.order,
                price=result.price,
            )

        except Exception as e:
            logger.error(f"Order placement error: {e}")
            return OrderResult(success=False, error=str(e))

    def close_order(self, order_ticket: int) -> bool:
        """Close an open order by ticket."""
        if not self.connected:
            logger.warning("Not connected")
            return False

        try:
            deal = mt5.order_get(ticket=order_ticket)
            if deal is None:
                logger.warning(f"Order {order_ticket} not found")
                return False

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": deal.symbol,
                "volume": deal.volume,
                "type": mt5.ORDER_TYPE_SELL if deal.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": order_ticket,
                "magic": deal.magic,
            }

            result = mt5.order_send(request)
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ Order {order_ticket} closed")
                return True
            else:
                logger.error(f"Close failed: {result.comment}")
                return False
        except Exception as e:
            logger.error(f"Close order error: {e}")
            return False

    def get_open_positions(self, symbol: Optional[str] = None) -> list:
        """Get list of open positions."""
        if not self.connected:
            return []

        positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        return positions or []
