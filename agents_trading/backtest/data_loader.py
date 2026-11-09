"""
Data Loader - Load historical OHLC data for backtesting.

Sources:
- Dukascopy (tick data, already available in QLAB)
- Local CSV files (CSV from MT5, TradingView)
- Pandas DataFrames (in-memory)

Supports: XAUUSD, EURUSD, GBPUSD, USDJPY, SPX500, FTSE100
Timeframes: M1, M5, M15, H1, H4, D1, W1
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple, List
from pathlib import Path
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class DataLoader:
    """Load and validate historical OHLC data."""

    SUPPORTED_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "SPX500", "FTSE100"]
    SUPPORTED_TIMEFRAMES = ["M1", "M5", "M15", "H1", "H4", "D1", "W1"]

    def __init__(self, data_dir: str = "C:\\TG_TradinGo\\historical_data"):
        """
        Initialize data loader.

        Args:
            data_dir: Directory containing historical data files
        """
        self.data_dir = Path(data_dir)
        self.data_cache: Dict[str, pd.DataFrame] = {}

    def load_csv(
        self,
        symbol: str,
        timeframe: str,
        filepath: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load OHLC data from CSV file.

        Expected CSV format:
            time,open,high,low,close,volume
            2026-01-01 00:00:00,2000.50,2001.00,2000.00,2000.75,1000

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            timeframe: Timeframe (e.g., "H1")
            filepath: Full path to CSV file. If None, infer from data_dir

        Returns:
            DataFrame with columns: [open, high, low, close, volume]
                Index: datetime
        """
        if not filepath:
            filepath = self.data_dir / f"{symbol}_{timeframe}.csv"

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

            # Validate
            required_cols = ["open", "high", "low", "close", "volume"]
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"Missing required columns: {required_cols}")

            # Ensure high >= low, high >= open/close, low <= open/close
            df = self._validate_ohlc(df)

            # Sort by time
            df = df.sort_index()

            logger.info(f"✅ Loaded {len(df)} bars: {symbol} {timeframe} ({filepath})")
            logger.info(f"   Date range: {df.index.min()} to {df.index.max()}")

            return df

        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")
            raise

    def _validate_ohlc(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate OHLC data integrity."""
        # High >= all prices
        df["high"] = df[["open", "high", "low", "close"]].max(axis=1)

        # Low <= all prices
        df["low"] = df[["open", "high", "low", "close"]].min(axis=1)

        # No NaN
        if df.isnull().any().any():
            logger.warning("Found NaN values, forward-filling")
            df = df.fillna(method="ffill").fillna(method="bfill")

        return df

    def load_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str = "CUSTOM",
        timeframe: str = "H1",
    ) -> pd.DataFrame:
        """
        Load from pandas DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
            symbol: Symbol name
            timeframe: Timeframe name

        Returns:
            Validated DataFrame
        """
        df = df.copy()

        # Ensure datetime index
        if not isinstance(df.index, pd.DatetimeIndex):
            if "time" in df.columns:
                df["time"] = pd.to_datetime(df["time"])
                df = df.set_index("time")
            else:
                raise ValueError("DataFrame must have datetime index or 'time' column")

        # Validate
        df = self._validate_ohlc(df)

        logger.info(f"✅ Loaded {len(df)} bars: {symbol} {timeframe}")

        return df

    def get_price_stats(self, df: pd.DataFrame) -> Dict:
        """Get price statistics for data validation."""
        return {
            "symbol_stats": {
                "count": len(df),
                "start": df.index.min(),
                "end": df.index.max(),
                "duration_days": (df.index.max() - df.index.min()).days,
            },
            "price_stats": {
                "open": {"min": df["open"].min(), "max": df["open"].max(), "mean": df["open"].mean()},
                "close": {"min": df["close"].min(), "max": df["close"].max(), "mean": df["close"].mean()},
                "volume": {"min": df["volume"].min(), "max": df["volume"].max(), "mean": df["volume"].mean()},
            },
        }

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

    def aggregate_timeframe(
        self,
        df: pd.DataFrame,
        source_tf_minutes: int,
        target_tf_minutes: int,
    ) -> pd.DataFrame:
        """
        Aggregate from lower timeframe to higher.

        Args:
            df: Source DataFrame (minute-level or lower)
            source_tf_minutes: Source timeframe in minutes (1 for M1)
            target_tf_minutes: Target timeframe in minutes (60 for H1)

        Returns:
            Aggregated DataFrame
        """
        if target_tf_minutes <= source_tf_minutes:
            logger.warning("Target TF <= source TF, returning as-is")
            return df

        # Resample to target timeframe
        agg = df.resample(f"{target_tf_minutes}T").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

        logger.info(f"Aggregated {len(df)} bars to {len(agg)} bars")
        return agg

    def get_cached_data(self, key: str) -> Optional[pd.DataFrame]:
        """Get data from cache."""
        return self.data_cache.get(key)

    def cache_data(self, key: str, df: pd.DataFrame):
        """Cache data in memory."""
        self.data_cache[key] = df
        logger.info(f"Cached: {key}")

    def clear_cache(self):
        """Clear all cached data."""
        self.data_cache.clear()
        logger.info("Cache cleared")
