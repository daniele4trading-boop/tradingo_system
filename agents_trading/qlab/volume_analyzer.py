"""
Volume Analyzer - Analyze price-volume correlations per QLAB spec.

Implements H9, H28, H29, H30 hypotheses:
- H9: Volume absorption (high relative volume + opposite delta = reacting sweep)
- H28: Mean reversion (price outside Value Area → reacts toward POC)
- H29: LVN vacuum (low volume node acceleration vs HVN deceleration)
- H30: Robustness check (tick volume vs real volume agreement)

Data sources:
- Dukascopy tick volume (builtin)
- Databento real volume (optional, if available)
"""

import pandas as pd
import numpy as np
import logging
from typing import Dict, Optional, Tuple, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class VolumeMetrics:
    """Volume analysis for an event."""
    event_time: pd.Timestamp
    symbol: str

    # Relative volume
    tick_volume: float
    tick_vol_ratio: float  # Current vs 20-day average same hour
    real_volume: Optional[float] = None
    real_vol_ratio: Optional[float] = None

    # Delta (buy vs sell volume)
    delta: Optional[float] = None
    delta_ratio: Optional[float] = None

    # Volume Profile
    poc_distance: float = 0.0
    va_side: str = "INSIDE"  # INSIDE, ABOVE, BELOW
    volume_node_type: str = "NEUTRAL"  # HVN, LVN, NEUTRAL

    # Momentum
    volume_trend: str = "STABLE"  # INCREASING, DECREASING, STABLE

    def summary(self) -> str:
        return (f"Vol: {self.tick_vol_ratio:.2f}x | "
                f"Delta: {self.delta_ratio:.2f} | "
                f"POC: {self.poc_distance:.1f}$ | "
                f"VA: {self.va_side} | Node: {self.volume_node_type}")


class VolumeAnalyzer:
    """Analyze volume-price correlations."""

    def __init__(self, symbol: str = "XAUUSD", lookback_period: int = 20):
        """
        Initialize volume analyzer.

        Args:
            symbol: Symbol (e.g., "XAUUSD")
            lookback_period: Days to lookback for average volume
        """
        self.symbol = symbol
        self.lookback_period = lookback_period
        self.hourly_volume_avg: Dict[int, float] = {}  # hour -> avg volume

    def analyze_event(
        self,
        df: pd.DataFrame,
        event_time: pd.Timestamp,
        event_idx: int,
        tick_volume_col: str = "volume",
        real_volume_col: Optional[str] = None,
        delta_col: Optional[str] = None,
    ) -> VolumeMetrics:
        """
        Analyze volume metrics at event time.

        Args:
            df: OHLC DataFrame with volume
            event_time: Time of event
            event_idx: Index of event in dataframe
            tick_volume_col: Column name for tick volume
            real_volume_col: Column name for real volume (if available)
            delta_col: Column name for delta (if available)

        Returns:
            VolumeMetrics object
        """
        try:
            # Get current values
            current_vol = df[tick_volume_col].iloc[event_idx]
            event_hour = event_time.hour

            # Calculate volume ratio (vs 20-day average same hour)
            prev_same_hour = df[df.index.hour == event_hour].tail(self.lookback_period)
            if len(prev_same_hour) > 0:
                avg_vol_same_hour = prev_same_hour[tick_volume_col].mean()
                vol_ratio = current_vol / avg_vol_same_hour if avg_vol_same_hour > 0 else 1.0
            else:
                avg_vol_same_hour = df[tick_volume_col].iloc[:event_idx].mean()
                vol_ratio = current_vol / avg_vol_same_hour if avg_vol_same_hour > 0 else 1.0

            # Real volume (if available)
            real_vol = None
            real_vol_ratio = None
            if real_volume_col and real_volume_col in df.columns:
                real_vol = df[real_volume_col].iloc[event_idx]
                if len(prev_same_hour) > 0:
                    avg_real_vol = prev_same_hour[real_volume_col].mean()
                    real_vol_ratio = real_vol / avg_real_vol if avg_real_vol > 0 else 1.0

            # Delta (if available)
            delta = None
            delta_ratio = None
            if delta_col and delta_col in df.columns:
                delta = df[delta_col].iloc[event_idx]
                delta_ratio = delta / current_vol if current_vol > 0 else 0.0

            # Volume trend (increasing/decreasing)
            vol_trend = "STABLE"
            if len(df) >= event_idx + 5:
                recent_vols = df[tick_volume_col].iloc[event_idx:event_idx+5]
                if recent_vols.mean() > current_vol * 1.2:
                    vol_trend = "INCREASING"
                elif recent_vols.mean() < current_vol * 0.8:
                    vol_trend = "DECREASING"

            metrics = VolumeMetrics(
                event_time=event_time,
                symbol=self.symbol,
                tick_volume=current_vol,
                tick_vol_ratio=vol_ratio,
                real_volume=real_vol,
                real_vol_ratio=real_vol_ratio,
                delta=delta,
                delta_ratio=delta_ratio,
                volume_trend=vol_trend,
            )

            logger.debug(f"Volume metrics: {metrics.summary()}")
            return metrics

        except Exception as e:
            logger.error(f"Error analyzing volume: {e}")
            return VolumeMetrics(
                event_time=event_time,
                symbol=self.symbol,
                tick_volume=0,
                tick_vol_ratio=1.0,
            )

    def detect_absorption(
        self,
        df: pd.DataFrame,
        event_idx: int,
        vol_ratio_threshold: float = 1.5,
        delta_threshold: float = 0.3,
    ) -> bool:
        """
        Detect volume absorption (H9 hypothesis).

        High relative volume + delta opposing price movement = absorption.

        Args:
            df: OHLC DataFrame
            event_idx: Index of sweep event
            vol_ratio_threshold: Threshold for "high" volume ratio
            delta_threshold: Threshold for delta magnitude

        Returns:
            True if absorption detected
        """
        if event_idx < 1 or "volume" not in df.columns:
            return False

        current_vol = df["volume"].iloc[event_idx]
        prev_vol = df["volume"].iloc[event_idx - 1]
        avg_vol = df["volume"].iloc[:event_idx].mean()
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 0

        # High relative volume?
        if vol_ratio < vol_ratio_threshold:
            return False

        # Delta available?
        if "delta_ratio" not in df.columns:
            # Estimate delta from price action
            close_prev = df["close"].iloc[event_idx - 1]
            close_curr = df["close"].iloc[event_idx]

            if close_curr > close_prev:  # Price up
                estimated_delta_ratio = 0.6  # Assume 60% buying
            else:  # Price down
                estimated_delta_ratio = 0.4  # Assume 40% buying
        else:
            estimated_delta_ratio = df["delta_ratio"].iloc[event_idx]

        # Absorption: high volume + delta opposing price direction
        high_vol = vol_ratio >= vol_ratio_threshold
        opposing_delta = (
            (close_curr > close_prev and estimated_delta_ratio < delta_threshold) or
            (close_curr <= close_prev and estimated_delta_ratio > (1 - delta_threshold))
        )

        return high_vol and opposing_delta

    def mean_reversion_score(
        self,
        current_price: float,
        poc_price: float,
        va_high: float,
        va_low: float,
    ) -> Tuple[float, str]:
        """
        Calculate mean reversion score (H28).

        Price outside Value Area → higher score for reversion to POC.

        Args:
            current_price: Current price
            poc_price: Point of Control
            va_high: Value Area high
            va_low: Value Area low

        Returns:
            (score 0-1, side: ABOVE/BELOW/INSIDE)
        """
        if va_low <= current_price <= va_high:
            return 0.0, "INSIDE"

        if current_price > va_high:
            distance = current_price - poc_price
            max_distance = current_price - va_low
            score = min(1.0, distance / max_distance) if max_distance > 0 else 0.5
            return score, "ABOVE"
        else:  # below va_low
            distance = poc_price - current_price
            max_distance = va_high - current_price
            score = min(1.0, distance / max_distance) if max_distance > 0 else 0.5
            return score, "BELOW"

    def lvn_hvn_analysis(
        self,
        df: pd.DataFrame,
        current_price: float,
        volume_bin_width: float = 1.0,
    ) -> Dict:
        """
        Detect High Volume Nodes vs Low Volume Nodes (H29).

        LVN = vacuum (price accelerates faster).
        HVN = resistance (price decelerates).

        Args:
            df: OHLC DataFrame
            current_price: Current price level
            volume_bin_width: Width of each price bin in $

        Returns:
            Dict with node type, density, and acceleration estimate
        """
        if "volume" not in df.columns or len(df) < 10:
            return {"node_type": "NEUTRAL", "density": 0.5}

        # Create price bins
        bins = np.arange(df["low"].min(), df["high"].max() + volume_bin_width, volume_bin_width)
        bin_idx = np.digitize(current_price, bins)

        # Calculate volume in each bin (simplified)
        volume_by_bin = {}
        for i, row in df.iterrows():
            bin_num = np.digitize(row["close"], bins)
            if bin_num not in volume_by_bin:
                volume_by_bin[bin_num] = 0
            volume_by_bin[bin_num] += row["volume"]

        # Current bin volume vs average
        current_bin_vol = volume_by_bin.get(bin_idx, 0)
        avg_vol = sum(volume_by_bin.values()) / len(volume_by_bin) if volume_by_bin else 1
        density = current_bin_vol / avg_vol if avg_vol > 0 else 0.5

        # Classify
        if density > 1.3:
            node_type = "HVN"
        elif density < 0.7:
            node_type = "LVN"
        else:
            node_type = "NEUTRAL"

        return {
            "node_type": node_type,
            "density": density,
            "current_bin_volume": current_bin_vol,
            "avg_volume": avg_vol,
        }

    def tick_vs_real_volume_agreement(
        self,
        df: pd.DataFrame,
        tick_vol_col: str = "volume",
        real_vol_col: Optional[str] = None,
    ) -> Dict:
        """
        Check robustness: do tick volume and real volume correlate? (H30).

        Returns:
            Dict with correlation and agreement assessment
        """
        if real_vol_col is None or real_vol_col not in df.columns:
            return {"status": "INCOMPLETE", "reason": "Real volume data not available"}

        try:
            tick_vol = df[tick_vol_col]
            real_vol = df[real_vol_col]

            # Correlation
            correlation = tick_vol.corr(real_vol)

            # Agreement: both increasing/decreasing together?
            tick_changes = tick_vol.pct_change().fillna(0) > 0
            real_changes = real_vol.pct_change().fillna(0) > 0
            agreement = (tick_changes == real_changes).sum() / len(tick_changes)

            return {
                "status": "CONFIRMED" if correlation > 0.7 and agreement > 0.7 else "PARTIAL",
                "correlation": correlation,
                "agreement": agreement,
                "tick_vs_real_robust": correlation > 0.7 and agreement > 0.7,
            }
        except Exception as e:
            return {
                "status": "ERROR",
                "reason": str(e),
            }
