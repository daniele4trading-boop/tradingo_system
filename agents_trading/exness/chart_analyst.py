"""
Chart Analyst Agent - Analyze candlestick charts with Claude vision.

Uses Claude Opus 5.5 vision capabilities to detect:
- Support/resistance levels
- Candlestick patterns (head-shoulders, flags, triangles)
- Trend direction and momentum
- Entry/exit setup probability
"""

import logging
import json
from typing import Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChartAnalysis:
    """Analysis result from chart reading."""
    sentiment: str  # BULLISH, BEARISH, NEUTRAL
    confidence: float  # 0-1
    patterns: list  # [{"type": "...", "reliability": "high/medium/low"}]
    support_resistance: Dict[str, list]
    trading_setup: Dict[str, Any]
    risks: list
    notes: str


class ChartAnalyst:
    """Analyze charts using Claude vision API."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize chart analyst.

        Args:
            api_key: Anthropic API key (default: $ANTHROPIC_API_KEY env var)
        """
        self.api_key = api_key
        self.model = "claude-opus-5-5-20250514"  # Latest vision model
        self.client = None

        # Try to initialize client if API key available
        if api_key:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
                logger.info("✅ Claude Opus 5.5 initialized for chart analysis")
            except Exception as e:
                logger.warning(f"⚠️  Claude API not available: {e}")
                logger.info("   Using mock analysis mode (test only)")

    def analyze_chart(
        self,
        chart_image_base64: Optional[str] = None,
        chart_description: Optional[str] = None,
        symbol: str = "XAUUSD",
        timeframe: str = "H1",
    ) -> ChartAnalysis:
        """
        Analyze chart and return trading analysis.

        Args:
            chart_image_base64: Base64-encoded chart image
            chart_description: Text description of chart (for testing)
            symbol: Symbol name
            timeframe: Timeframe

        Returns:
            ChartAnalysis object
        """
        # If we have a real chart image and API key, use Claude
        if chart_image_base64 and self.client:
            return self._analyze_with_claude(
                chart_image_base64, symbol, timeframe
            )

        # Otherwise use mock/technical analysis
        return self._analyze_mock(chart_description, symbol, timeframe)

    def _analyze_with_claude(
        self,
        chart_image_base64: str,
        symbol: str,
        timeframe: str,
    ) -> ChartAnalysis:
        """Analyze chart using Claude vision API."""
        try:
            prompt = f"""Analyze this {symbol} {timeframe} candlestick chart and provide trading analysis:

Please identify:
1. Current sentiment (BULLISH/BEARISH/NEUTRAL) with 0-1 confidence
2. Key candlestick patterns (head-shoulders, flags, triangles, pin bars)
3. Support and resistance levels
4. Trading setup type and probability
5. Key risks to watch

Return JSON format:
{{
    "sentiment": "BULLISH",
    "confidence": 0.65,
    "patterns": [
        {{"type": "pattern_name", "reliability": "high/medium/low"}}
    ],
    "support_resistance": {{
        "support": [level1, level2],
        "resistance": [level1, level2]
    }},
    "trading_setup": {{
        "type": "breakout",
        "probability": 0.65
    }},
    "risks": ["pullback", "false_breakout"],
    "notes": "Brief analysis"
}}"""

            message = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": chart_image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt
                            }
                        ],
                    }
                ],
            )

            # Parse response
            response_text = message.content[0].text
            analysis_json = json.loads(response_text)

            logger.info(f"✅ Chart analysis: {analysis_json['sentiment']} "
                       f"(confidence: {analysis_json['confidence']})")

            return ChartAnalysis(
                sentiment=analysis_json.get("sentiment", "NEUTRAL"),
                confidence=analysis_json.get("confidence", 0.5),
                patterns=analysis_json.get("patterns", []),
                support_resistance=analysis_json.get("support_resistance", {}),
                trading_setup=analysis_json.get("trading_setup", {}),
                risks=analysis_json.get("risks", []),
                notes=analysis_json.get("notes", ""),
            )

        except Exception as e:
            logger.error(f"Error analyzing chart: {e}")
            return self._analyze_mock(None, symbol, timeframe)

    def _analyze_mock(
        self,
        description: Optional[str],
        symbol: str,
        timeframe: str,
    ) -> ChartAnalysis:
        """Mock analysis for testing (no Claude API)."""
        logger.debug(f"Using mock chart analysis: {symbol} {timeframe}")

        # Simple mock: random sentiment
        import random
        sentiment = random.choice(["BULLISH", "BEARISH", "NEUTRAL"])
        confidence = 0.50 + random.random() * 0.25  # 0.50-0.75

        return ChartAnalysis(
            sentiment=sentiment,
            confidence=confidence,
            patterns=[
                {
                    "type": "breakout" if sentiment == "BULLISH" else "breakdown",
                    "reliability": "medium"
                }
            ],
            support_resistance={
                "support": [1990.0, 1985.0],
                "resistance": [2010.0, 2015.0],
            },
            trading_setup={
                "type": "breakout" if sentiment == "BULLISH" else "breakdown",
                "probability": confidence,
            },
            risks=["pullback", "news"],
            notes=f"Mock analysis for {symbol} {timeframe}",
        )
