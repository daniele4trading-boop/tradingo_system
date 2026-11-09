"""
Liquidity Analyzer - Detect and analyze QLAB liquidity levels.

Implements key definitions from SPECIFICA_QLAB.md:
- Structural levels (swing high/low, strong/weak, equal highs/lows)
- Order blocks
- FVG (Fair Value Gaps)
- Hot levels (PDH/PDL, sessions, opens, round numbers)
- EMA 21 (dynamic level)
- Volume Profile (POC, Value Area)

Output: Level objects with state tracking (intact → touched → swept → broken)
"""

import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class LevelFamily(Enum):
    """Level family classifications."""
    STRUCTURAL = "structural"
    ZONE = "zone"
    HOT = "hot"
    DYNAMIC = "dynamic"
    VOLUMETRIC = "volumetric"


class LevelState(Enum):
    """Level state evolution."""
    INTACT = "intact"
    TOUCHED = "touched"
    SWEPT = "swept"
    BROKEN = "broken"


@dataclass
class Level:
    """Liquidity level definition."""
    level_id: str
    family: LevelFamily
    type_name: str  # swing_high, swing_low, order_block, fvg, poc, etc
    side: str  # HIGH or LOW (or range for zones)
    timeframe: str
    price: float  # or (high, low) tuple for zones
    birth_time: datetime
    state: LevelState = LevelState.INTACT
    n_touches: int = 0
    n_sweeps: int = 0
    n_returns: int = 0
    strong: bool = False  # For structural levels
    attributes: Dict = field(default_factory=dict)  # Extra data

    def __repr__(self) -> str:
        return f"{self.type_name} {self.price:.2f} ({self.state.value})"


class LiquidityAnalyzer:
    """Detect and track liquidity levels per QLAB spec."""

    def __init__(self, symbol: str = "XAUUSD", atr_period: int = 14):
        """
        Initialize analyzer.

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            atr_period: ATR period for distance calculations
        """
        self.symbol = symbol
        self.atr_period = atr_period
        self.levels: Dict[str, Level] = {}
        self.level_counter = 0

    def detect_levels(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """
        Detect all liquidity levels in OHLC data.

        Args:
            df: OHLC DataFrame
            timeframe: Timeframe name (H1, D1, etc)

        Returns:
            List of detected levels
        """
        detected_levels = []

        logger.info(f"🔍 Detecting liquidity levels: {self.symbol} {timeframe}")

        # 1. Structural levels (swings)
        detected_levels.extend(self._detect_swings(df, timeframe))

        # 2. Order blocks
        detected_levels.extend(self._detect_order_blocks(df, timeframe))

        # 3. FVG (Fair Value Gaps)
        detected_levels.extend(self._detect_fvg(df, timeframe))

        # 4. Hot levels (PDH, PDL, sessions, etc)
        detected_levels.extend(self._detect_hot_levels(df, timeframe))

        # 5. EMA 21 (dynamic)
        detected_levels.extend(self._detect_ema_levels(df, timeframe))

        # 6. Volume Profile (POC, Value Area)
        detected_levels.extend(self._detect_volume_profile(df, timeframe))

        logger.info(f"✅ Detected {len(detected_levels)} levels")

        # Store levels
        for level in detected_levels:
            self.levels[level.level_id] = level

        return detected_levels

    def _detect_swings(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect swing high/low (fractal pattern N=2,3,5)."""
        levels = []

        for N in [2, 3, 5]:
            for i in range(N, len(df) - N):
                high = df["high"].iloc[i]
                low = df["low"].iloc[i]
                timestamp = df.index[i]

                # Swing high: local maximum
                if high == df["high"].iloc[i-N:i+N+1].max():
                    level_id = self._gen_level_id("swing_high")
                    level = Level(
                        level_id=level_id,
                        family=LevelFamily.STRUCTURAL,
                        type_name="swing_high",
                        side="HIGH",
                        timeframe=timeframe,
                        price=high,
                        birth_time=timestamp,
                        attributes={"N": N},
                    )
                    levels.append(level)

                # Swing low: local minimum
                if low == df["low"].iloc[i-N:i+N+1].min():
                    level_id = self._gen_level_id("swing_low")
                    level = Level(
                        level_id=level_id,
                        family=LevelFamily.STRUCTURAL,
                        type_name="swing_low",
                        side="LOW",
                        timeframe=timeframe,
                        price=low,
                        birth_time=timestamp,
                        attributes={"N": N},
                    )
                    levels.append(level)

        logger.info(f"  Detected {len(levels)} swing levels")
        return levels

    def _detect_order_blocks(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect order blocks (last candle of opposite color before impulse)."""
        levels = []

        # Simplified: last 3 candles of opposite color
        for i in range(2, len(df)):
            close_prev = df["close"].iloc[i-1]
            open_prev = df["open"].iloc[i-1]
            close_curr = df["close"].iloc[i]
            open_curr = df["open"].iloc[i]

            # Bearish candle followed by bullish (potential OB low)
            if open_prev > close_prev and open_curr < close_curr:
                ob_low = df["low"].iloc[i-1]
                level_id = self._gen_level_id("order_block_low")
                level = Level(
                    level_id=level_id,
                    family=LevelFamily.ZONE,
                    type_name="order_block",
                    side="LOW",
                    timeframe=timeframe,
                    price=ob_low,
                    birth_time=df.index[i],
                    attributes={"high": df["high"].iloc[i-1]},
                )
                levels.append(level)

            # Bullish candle followed by bearish (potential OB high)
            elif open_prev < close_prev and open_curr > close_curr:
                ob_high = df["high"].iloc[i-1]
                level_id = self._gen_level_id("order_block_high")
                level = Level(
                    level_id=level_id,
                    family=LevelFamily.ZONE,
                    type_name="order_block",
                    side="HIGH",
                    timeframe=timeframe,
                    price=ob_high,
                    birth_time=df.index[i],
                    attributes={"low": df["low"].iloc[i-1]},
                )
                levels.append(level)

        logger.info(f"  Detected {len(levels)} order block levels")
        return levels

    def _detect_fvg(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect FVG (Fair Value Gaps) - gap between candles."""
        levels = []

        for i in range(2, len(df)):
            high_i_minus_2 = df["high"].iloc[i-2]
            low_i = df["low"].iloc[i]
            low_i_minus_2 = df["low"].iloc[i-2]
            high_i = df["high"].iloc[i]

            # Bullish FVG: high[i-2] < low[i]
            if high_i_minus_2 < low_i:
                gap_low = high_i_minus_2
                gap_high = low_i
                level_id = self._gen_level_id("fvg_bullish")
                level = Level(
                    level_id=level_id,
                    family=LevelFamily.ZONE,
                    type_name="fvg",
                    side="HIGH",
                    timeframe=timeframe,
                    price=(gap_low + gap_high) / 2,
                    birth_time=df.index[i],
                    attributes={"gap_range": (gap_low, gap_high)},
                )
                levels.append(level)

            # Bearish FVG: low[i-2] > high[i]
            elif low_i_minus_2 > high_i:
                gap_high = low_i_minus_2
                gap_low = high_i
                level_id = self._gen_level_id("fvg_bearish")
                level = Level(
                    level_id=level_id,
                    family=LevelFamily.ZONE,
                    type_name="fvg",
                    side="LOW",
                    timeframe=timeframe,
                    price=(gap_low + gap_high) / 2,
                    birth_time=df.index[i],
                    attributes={"gap_range": (gap_low, gap_high)},
                )
                levels.append(level)

        logger.info(f"  Detected {len(levels)} FVG levels")
        return levels

    def _detect_hot_levels(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect hot levels (PDH/PDL, round numbers, session levels)."""
        levels = []

        # Previous day high/low
        if len(df) >= 24:  # At least 1 day of H1 data
            prev_day_high = df["high"].iloc[-24:].max()
            prev_day_low = df["low"].iloc[-24:].min()

            level = Level(
                level_id=self._gen_level_id("pdh"),
                family=LevelFamily.HOT,
                type_name="PDH",
                side="HIGH",
                timeframe=timeframe,
                price=prev_day_high,
                birth_time=df.index[-1],
            )
            levels.append(level)

            level = Level(
                level_id=self._gen_level_id("pdl"),
                family=LevelFamily.HOT,
                type_name="PDL",
                side="LOW",
                timeframe=timeframe,
                price=prev_day_low,
                birth_time=df.index[-1],
            )
            levels.append(level)

        # Round numbers (5, 10, 25, 50, 100)
        current_price = df["close"].iloc[-1]
        for round_val in [5, 10, 25, 50, 100]:
            round_price = (current_price // round_val) * round_val
            if abs(round_price - current_price) < 100:  # Within 100 pips
                level = Level(
                    level_id=self._gen_level_id("round_number"),
                    family=LevelFamily.HOT,
                    type_name="round_number",
                    side="HIGH" if round_price > current_price else "LOW",
                    timeframe=timeframe,
                    price=round_price,
                    birth_time=df.index[-1],
                    attributes={"increment": round_val},
                )
                levels.append(level)

        logger.info(f"  Detected {len(levels)} hot levels")
        return levels

    def _detect_ema_levels(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect EMA 21 (dynamic level)."""
        levels = []

        if len(df) < 21:
            return levels

        ema21 = df["close"].ewm(span=21, adjust=False).mean()
        current_ema = ema21.iloc[-1]

        level = Level(
            level_id=self._gen_level_id("ema21"),
            family=LevelFamily.DYNAMIC,
            type_name="EMA21",
            side="HIGH" if df["close"].iloc[-1] > current_ema else "LOW",
            timeframe=timeframe,
            price=current_ema,
            birth_time=df.index[-1],
        )
        levels.append(level)

        return levels

    def _detect_volume_profile(self, df: pd.DataFrame, timeframe: str) -> List[Level]:
        """Detect Volume Profile levels (POC, Value Area)."""
        levels = []

        if "volume" not in df.columns or len(df) < 20:
            return levels

        # POC: price with highest volume
        # Simplified: use close price weighted by volume
        price_volume = pd.Series(
            df["volume"].values,
            index=df["close"].values,
        )
        poc_price = price_volume.groupby(level=0).sum().idxmax()

        level = Level(
            level_id=self._gen_level_id("poc"),
            family=LevelFamily.VOLUMETRIC,
            type_name="POC",
            side="HIGH",
            timeframe=timeframe,
            price=poc_price,
            birth_time=df.index[-1],
        )
        levels.append(level)

        logger.info(f"  Detected {len(levels)} volume profile levels")
        return levels

    def _gen_level_id(self, level_type: str) -> str:
        """Generate unique level ID."""
        self.level_counter += 1
        return f"{self.symbol}_{level_type}_{self.level_counter}"

    def get_levels_near_price(
        self,
        current_price: float,
        distance_atr: float = 1.0,
        atr_value: float = 50.0,  # Typical ATR value in pips
    ) -> List[Level]:
        """Get levels within distance_atr from current price."""
        distance = distance_atr * atr_value * 0.1  # Convert to $ (1 pip = 0.1$)

        nearby = [
            level for level in self.levels.values()
            if abs(level.price - current_price) <= distance
        ]

        return sorted(nearby, key=lambda l: abs(l.price - current_price))

    def print_levels_summary(self):
        """Print summary of detected levels."""
        if not self.levels:
            logger.info("No levels detected")
            return

        by_family = {}
        for level in self.levels.values():
            family = level.family.value
            if family not in by_family:
                by_family[family] = []
            by_family[family].append(level)

        print("\n" + "="*60)
        print("LIQUIDITY LEVELS SUMMARY")
        print("="*60)
        for family, levels in by_family.items():
            print(f"\n{family.upper()}: {len(levels)} levels")
            for level in levels[:5]:  # Show first 5
                print(f"  {level.type_name:15} @ {level.price:8.2f} ({level.state.value})")
        print("="*60 + "\n")
