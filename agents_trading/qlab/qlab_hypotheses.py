"""
QLAB Hypotheses Implementation - Map H0-H30 into trading rules.

Per SPECIFICA_QLAB.md Section 8, verify 30 hypotheses about price reactions to liquidity.
This module scores price action against each hypothesis to generate trading signals.

Key hypotheses mapped:
- H0-H3: Mean reversion (price outside levels → reacts to POC)
- H4-H8: Momentum patterns (breakout hold, level break + retest)
- H9: Volume absorption (volume + opposite delta = reacting sweep)
- H10-H15: Multi-timeframe confluence
- H16-H20: Order flow and volume profile
- H21-H25: Temporal patterns (sessions, times, news)
- H26-H30: Advanced patterns (LVN vacuum, HVN slowdown, robustness)
"""

import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class HypothesisType(Enum):
    """Hypothesis categories."""
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM = "momentum"
    VOLUME = "volume"
    CONFLUENCE = "confluence"
    TEMPORAL = "temporal"
    ADVANCED = "advanced"


@dataclass
class HypothesisScore:
    """Score for a single hypothesis."""
    hypothesis_id: str  # H0, H1, ..., H30
    name: str
    category: HypothesisType
    score: float  # 0-1, confidence in hypothesis being true
    details: Dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class QLabHypotheses:
    """Evaluate price action against QLAB hypotheses H0-H30."""

    def __init__(self):
        """Initialize hypothesis tester."""
        self.thresholds = {
            "mean_reversion_min_distance": 1.0,  # ATR multiple
            "momentum_min_volume_ratio": 1.5,
            "volume_absorption_threshold": 1.8,
            "confluence_min_levels": 2,
            "temporal_session_weight": 0.15,
        }

    def evaluate_all_hypotheses(
        self,
        current_price: float,
        poc_price: float,
        va_high: float,
        va_low: float,
        support: float,
        resistance: float,
        volume_metrics: Dict,
        levels_nearby: List,
        timeframe_bias: str,  # "BULLISH", "BEARISH", "NEUTRAL"
        hour_of_day: int,
    ) -> List[HypothesisScore]:
        """
        Evaluate all 30 hypotheses on current price action.

        Args:
            current_price: Current price
            poc_price: Point of Control
            va_high/va_low: Value Area boundaries
            support/resistance: Key structural levels
            volume_metrics: VolumeMetrics object
            levels_nearby: List of Level objects near price
            timeframe_bias: MTF sentiment (BULLISH/BEARISH/NEUTRAL)
            hour_of_day: Current hour (0-23 UTC)

        Returns:
            List of HypothesisScore objects, sorted by score DESC
        """
        scores: List[HypothesisScore] = []

        # H0-H3: Mean reversion hypotheses
        scores.extend(self._evaluate_mean_reversion(
            current_price, poc_price, va_high, va_low, support, resistance
        ))

        # H4-H8: Momentum hypotheses
        scores.extend(self._evaluate_momentum(
            current_price, resistance, support, volume_metrics, levels_nearby
        ))

        # H9: Volume absorption
        scores.append(self._evaluate_volume_absorption(volume_metrics))

        # H10-H15: Multi-timeframe confluence
        scores.extend(self._evaluate_confluence(
            current_price, levels_nearby, timeframe_bias, support, resistance
        ))

        # H16-H20: Order flow
        scores.extend(self._evaluate_order_flow(
            current_price, levels_nearby, volume_metrics
        ))

        # H21-H25: Temporal
        scores.extend(self._evaluate_temporal(hour_of_day, timeframe_bias))

        # H26-H30: Advanced
        scores.extend(self._evaluate_advanced(
            current_price, poc_price, levels_nearby, volume_metrics
        ))

        # Sort by score DESC
        scores.sort(key=lambda s: s.score, reverse=True)

        logger.info(f"Evaluated 30 hypotheses. Top 3:")
        for s in scores[:3]:
            logger.info(f"  {s.hypothesis_id} {s.name}: {s.score:.2f}")

        return scores

    def _evaluate_mean_reversion(
        self,
        current_price: float,
        poc_price: float,
        va_high: float,
        va_low: float,
        support: float,
        resistance: float,
    ) -> List[HypothesisScore]:
        """H0-H3: Mean reversion patterns."""
        scores = []

        # H0: Price above Value Area
        if current_price > va_high:
            distance_pct = (current_price - poc_price) / poc_price * 100
            h0_score = min(1.0, distance_pct / 5.0)  # 5% = max score
            scores.append(HypothesisScore(
                hypothesis_id="H0",
                name="Price Above VA - Mean Reversion Pull",
                category=HypothesisType.MEAN_REVERSION,
                score=h0_score,
                details={"distance_from_poc_pct": distance_pct}
            ))

        # H1: Price below Value Area
        if current_price < va_low:
            distance_pct = (poc_price - current_price) / poc_price * 100
            h1_score = min(1.0, distance_pct / 5.0)
            scores.append(HypothesisScore(
                hypothesis_id="H1",
                name="Price Below VA - Mean Reversion Pull",
                category=HypothesisType.MEAN_REVERSION,
                score=h1_score,
                details={"distance_from_poc_pct": distance_pct}
            ))

        # H2: Price near resistance (structural)
        if abs(current_price - resistance) / resistance < 0.01:  # Within 1%
            h2_score = 0.7
            scores.append(HypothesisScore(
                hypothesis_id="H2",
                name="Price at Resistance - Potential Rejection",
                category=HypothesisType.MEAN_REVERSION,
                score=h2_score,
                details={"distance_from_resistance_pct": 0.5}
            ))

        # H3: Price near support (structural)
        if abs(current_price - support) / support < 0.01:
            h3_score = 0.7
            scores.append(HypothesisScore(
                hypothesis_id="H3",
                name="Price at Support - Potential Bounce",
                category=HypothesisType.MEAN_REVERSION,
                score=h3_score,
                details={"distance_from_support_pct": 0.5}
            ))

        return scores

    def _evaluate_momentum(
        self,
        current_price: float,
        resistance: float,
        support: float,
        volume_metrics: Dict,
        levels_nearby: List,
    ) -> List[HypothesisScore]:
        """H4-H8: Momentum patterns."""
        scores = []

        # H4: Breakout above resistance with volume
        vol_ratio = volume_metrics.get("tick_vol_ratio", 1.0)
        if current_price > resistance and vol_ratio > self.thresholds["momentum_min_volume_ratio"]:
            h4_score = min(1.0, vol_ratio / 3.0)
            scores.append(HypothesisScore(
                hypothesis_id="H4",
                name="Bullish Breakout with Volume",
                category=HypothesisType.MOMENTUM,
                score=h4_score,
                details={"volume_ratio": vol_ratio, "above_resistance": current_price - resistance}
            ))

        # H5: Breakdown below support with volume
        if current_price < support and vol_ratio > self.thresholds["momentum_min_volume_ratio"]:
            h5_score = min(1.0, vol_ratio / 3.0)
            scores.append(HypothesisScore(
                hypothesis_id="H5",
                name="Bearish Breakdown with Volume",
                category=HypothesisType.MOMENTUM,
                score=h5_score,
                details={"volume_ratio": vol_ratio, "below_support": support - current_price}
            ))

        # H6: False breakout (high volume, short duration)
        # Simplified: check if many levels nearby (potential rejection area)
        if len(levels_nearby) >= 3:
            h6_score = 0.6
            scores.append(HypothesisScore(
                hypothesis_id="H6",
                name="False Breakout Setup - Confluence Zone",
                category=HypothesisType.MOMENTUM,
                score=h6_score,
                details={"levels_in_area": len(levels_nearby)}
            ))

        # H7-H8: (Simplified - would need multi-bar tracking)
        # H7: Breakout hold (price stays above level after break)
        # H8: Failed breakout (pullback into level)

        return scores

    def _evaluate_volume_absorption(
        self,
        volume_metrics: Dict,
    ) -> HypothesisScore:
        """H9: Volume absorption (high vol + opposite delta)."""
        vol_ratio = volume_metrics.get("tick_vol_ratio", 1.0)
        delta_ratio = volume_metrics.get("delta_ratio", 0.5)

        # Check for absorption: high volume + balanced delta (close to 0.5)
        is_high_vol = vol_ratio > self.thresholds["volume_absorption_threshold"]
        is_balanced_delta = 0.35 < delta_ratio < 0.65  # Opposing delta

        h9_score = 0.0
        if is_high_vol and is_balanced_delta:
            h9_score = min(1.0, (vol_ratio / 3.0) * 0.8)

        return HypothesisScore(
            hypothesis_id="H9",
            name="Volume Absorption - Sweep Reaction Expected",
            category=HypothesisType.VOLUME,
            score=h9_score,
            details={
                "volume_ratio": vol_ratio,
                "delta_ratio": delta_ratio,
                "is_absorption": h9_score > 0.5
            }
        )

    def _evaluate_confluence(
        self,
        current_price: float,
        levels_nearby: List,
        timeframe_bias: str,
        support: float,
        resistance: float,
    ) -> List[HypothesisScore]:
        """H10-H15: Multi-timeframe confluence."""
        scores = []

        # H10: Multiple levels at same price (confluent)
        if len(levels_nearby) >= self.thresholds["confluence_min_levels"]:
            confluence_score = min(1.0, len(levels_nearby) / 5.0)
            scores.append(HypothesisScore(
                hypothesis_id="H10",
                name="Strong Confluence Zone",
                category=HypothesisType.CONFLUENCE,
                score=confluence_score,
                details={"levels_in_area": len(levels_nearby)}
            ))

        # H11: MTF bias bullish + price near support
        if timeframe_bias == "BULLISH" and abs(current_price - support) / support < 0.02:
            h11_score = 0.75
            scores.append(HypothesisScore(
                hypothesis_id="H11",
                name="MTF Bullish Bias + Support - Long Setup",
                category=HypothesisType.CONFLUENCE,
                score=h11_score,
                details={"mtf_bias": timeframe_bias}
            ))

        # H12: MTF bias bearish + price near resistance
        if timeframe_bias == "BEARISH" and abs(current_price - resistance) / resistance < 0.02:
            h12_score = 0.75
            scores.append(HypothesisScore(
                hypothesis_id="H12",
                name="MTF Bearish Bias + Resistance - Short Setup",
                category=HypothesisType.CONFLUENCE,
                score=h12_score,
                details={"mtf_bias": timeframe_bias}
            ))

        # H13-H15: (Would require multi-TF data)
        # H13: H4 confirms H1 signal
        # H14: D1 trend + H1 structure confluence
        # H15: EMA alignment across TFs

        return scores

    def _evaluate_order_flow(
        self,
        current_price: float,
        levels_nearby: List,
        volume_metrics: Dict,
    ) -> List[HypothesisScore]:
        """H16-H20: Order flow patterns."""
        scores = []

        # H16: FVG (Fair Value Gap) nearby + volume increase
        fvg_nearby = [l for l in levels_nearby if l.type_name == "fvg"]
        if fvg_nearby and volume_metrics.get("tick_vol_ratio", 1.0) > 1.3:
            h16_score = 0.65
            scores.append(HypothesisScore(
                hypothesis_id="H16",
                name="FVG Nearby + Increasing Volume - Likely Fill",
                category=HypothesisType.VOLUME,
                score=h16_score,
                details={"fvg_distance": len(fvg_nearby)}
            ))

        # H17: Order block sweep pattern
        ob_nearby = [l for l in levels_nearby if l.type_name == "order_block"]
        if ob_nearby and len(ob_nearby) >= 2:
            h17_score = 0.6
            scores.append(HypothesisScore(
                hypothesis_id="H17",
                name="Multiple Order Blocks - High Activity Zone",
                category=HypothesisType.VOLUME,
                score=h17_score,
                details={"order_blocks": len(ob_nearby)}
            ))

        # H18-H20: (Would require delta tracking, order imbalance)

        return scores

    def _evaluate_temporal(
        self,
        hour_of_day: int,
        timeframe_bias: str,
    ) -> List[HypothesisScore]:
        """H21-H25: Temporal patterns (sessions, times)."""
        scores = []

        # H21: Session opening (Asia open 0-2h UTC)
        if 0 <= hour_of_day <= 2:
            h21_score = 0.5
            scores.append(HypothesisScore(
                hypothesis_id="H21",
                name="Asia Session Opening - Increased Volatility",
                category=HypothesisType.TEMPORAL,
                score=h21_score,
                details={"session": "ASIA", "hour": hour_of_day}
            ))

        # H22: London open (8-10h UTC)
        if 8 <= hour_of_day <= 10:
            h22_score = 0.6
            scores.append(HypothesisScore(
                hypothesis_id="H22",
                name="London Session Opening - Trend Continuation",
                category=HypothesisType.TEMPORAL,
                score=h22_score,
                details={"session": "LONDON", "hour": hour_of_day}
            ))

        # H23: NY open (13-15h UTC)
        if 13 <= hour_of_day <= 15:
            h23_score = 0.65
            scores.append(HypothesisScore(
                hypothesis_id="H23",
                name="NY Session Opening - Highest Volume",
                category=HypothesisType.TEMPORAL,
                score=h23_score,
                details={"session": "NY", "hour": hour_of_day}
            ))

        # H24-H25: (Would require news calendar, specific times)

        return scores

    def _evaluate_advanced(
        self,
        current_price: float,
        poc_price: float,
        levels_nearby: List,
        volume_metrics: Dict,
    ) -> List[HypothesisScore]:
        """H26-H30: Advanced patterns."""
        scores = []

        # H26: LVN vacuum (low volume nodes)
        node_type = volume_metrics.get("node_type", "NEUTRAL")
        if node_type == "LVN":
            h26_score = 0.55
            scores.append(HypothesisScore(
                hypothesis_id="H26",
                name="LVN Vacuum - Price Acceleration Expected",
                category=HypothesisType.ADVANCED,
                score=h26_score,
                details={"node_type": "LVN"}
            ))

        # H27: HVN resistance
        if node_type == "HVN":
            h27_score = 0.55
            scores.append(HypothesisScore(
                hypothesis_id="H27",
                name="HVN Resistance - Deceleration/Reversal Expected",
                category=HypothesisType.ADVANCED,
                score=h27_score,
                details={"node_type": "HVN"}
            ))

        # H28: Mean reversion to POC (already covered in H0-H1, but advanced scoring)
        distance_from_poc = abs(current_price - poc_price) / poc_price * 100
        if distance_from_poc > 3.0:
            h28_score = min(1.0, distance_from_poc / 10.0)
            scores.append(HypothesisScore(
                hypothesis_id="H28",
                name="Extreme Distance from POC - Strong MR Signal",
                category=HypothesisType.ADVANCED,
                score=h28_score,
                details={"distance_from_poc_pct": distance_from_poc}
            ))

        # H29: Volume profile stability
        # Would track volume changes across sessions
        h29_score = 0.5  # Placeholder
        scores.append(HypothesisScore(
            hypothesis_id="H29",
            name="Volume Profile Stability - Continuation Likely",
            category=HypothesisType.ADVANCED,
            score=h29_score,
            details={"status": "NEUTRAL"}
        ))

        # H30: Tick vs real volume agreement
        # Would require both data sources
        h30_score = 0.5  # Placeholder
        scores.append(HypothesisScore(
            hypothesis_id="H30",
            name="Tick/Real Volume Agreement - Robust Signal",
            category=HypothesisType.ADVANCED,
            score=h30_score,
            details={"status": "INCOMPLETE"}
        ))

        return scores

    def generate_signal_from_hypotheses(
        self,
        hypothesis_scores: List[HypothesisScore],
        confidence_threshold: float = 0.55,
    ) -> Tuple[str, float]:
        """
        Convert top hypothesis scores into entry signal + confidence.

        Args:
            hypothesis_scores: List of HypothesisScore from evaluate_all_hypotheses()
            confidence_threshold: Min score to generate signal

        Returns:
            (action: "BUY" | "SELL" | "HOLD", confidence: 0-1)
        """
        if not hypothesis_scores:
            return "HOLD", 0.0

        # Filter by threshold
        strong_hypotheses = [h for h in hypothesis_scores if h.score >= confidence_threshold]

        if not strong_hypotheses:
            return "HOLD", 0.0

        # Count bullish vs bearish hypotheses
        bullish_ids = {"H0", "H1", "H4", "H11", "H21", "H22", "H23"}
        bearish_ids = {"H2", "H3", "H5", "H12"}

        bullish_count = sum(1 for h in strong_hypotheses if h.hypothesis_id in bullish_ids)
        bearish_count = sum(1 for h in strong_hypotheses if h.hypothesis_id in bearish_ids)

        # Calculate average score
        avg_score = sum(h.score for h in strong_hypotheses) / len(strong_hypotheses)

        # Determine action
        if bullish_count > bearish_count:
            action = "BUY"
        elif bearish_count > bullish_count:
            action = "SELL"
        else:
            action = "HOLD"

        logger.info(f"Signal: {action} (confidence: {avg_score:.2f})")
        logger.info(f"  Bullish hypotheses: {bullish_count}, Bearish: {bearish_count}")

        return action, avg_score
