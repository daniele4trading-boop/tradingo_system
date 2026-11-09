"""
Data Loader - Load historical OHLC data for backtesting.
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class DataLoader:
    """Load and validate historical OHLC data."""

    def __init__(self):
        """Initialize data loader."""
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def load_csv(
        self,
        symbol: str,
        timeframe: str,
        filepath: str,
    ) -> pd.DataFrame:
        """
        Load OHLC data from CSV file.

        Expected columns: time, open, high, low, close, volume
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"Data file not found: {filepath}")

        try:
            df = pd.read_csv(
                filepath,
                parse_dates=["time"],
                index_col="time",
                dtype={
                    "open": float,
                    "high": float,
                    "low": float,
                    "close": float,
                    "volume": float,
                }
            )

            # Validate columns
            required_cols = ["open", "high", "low", "close", "volume"]
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"Missing required columns: {required_cols}")

            # Validate OHLC
            df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
            df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

            # Sort by time
            df = df.sort_index()

            logger.info(f"✅ Loaded {len(df)} bars: {symbol} {timeframe}")
            logger.info(f"   Date range: {df.index.min()} to {df.index.max()}")

            return df

        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")
            raise

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        tr = pd.concat([
            df["high"] - df["low"],
            abs(df["high"] - df["close"].shift()),
            abs(df["low"] - df["close"].shift()),
        ], axis=1).max(axis=1)

        atr = tr.rolling(window=period).mean()
        return atr

    def calculate_ema(self, df: pd.DataFrame, period: int = 21, column: str = "close") -> pd.Series:
        """Calculate Exponential Moving Average."""
        return df[column].ewm(span=period, adjust=False).mean()
