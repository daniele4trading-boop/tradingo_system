"""
Chart Analyst Agent - Analyzes trading charts with Claude vision + technical indicators.

Uses Claude Opus (vision model) to:
1. Analyze candlestick patterns (head&shoulders, flags, zigzag, etc)
2. Identify support/resistance levels
3. Assess volume and volatility
4. Provide context for price movements

Output: Structured analysis (JSON) for Strategy Executor to decide trades.
"""

import json
import base64
import logging
from typing import Dict, Optional, Any
from pathlib import Path
from datetime import datetime
import anthropic

logger = logging.getLogger(__name__)


class ChartAnalyst:
    """Claude-powered chart analysis agent."""

    def __init__(self, api_key: Optional[str] = None, model: str = "claude-opus-5-5"):
        """
        Initialize chart analyst.

        Args:
            api_key: Anthropic API key (from env or secrets)
            model: Claude model to use (should be vision-capable)
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.analysis_history = []

    def load_chart_screenshot(self, image_path: str) -> str:
        """Load and encode chart screenshot to base64."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Chart image not found: {image_path}")

        with open(path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        return image_data

    def analyze_chart(
        self,
        chart_image: str,  # base64 or path
        symbol: str,
        timeframe: str = "1H",
        additional_context: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Analyze chart using Claude vision + technical indicators.

        Args:
            chart_image: Base64 encoded image or file path
            symbol: Trading symbol (e.g. "XAUUSD")
            timeframe: Chart timeframe (e.g. "1H", "4H", "D1")
            additional_context: Extra market data (bid/ask, volume, etc)

        Returns:
            Structured analysis with patterns, levels, sentiment
        """
        try:
            # Load image if path given
            if chart_image.startswith("/") or chart_image.startswith("C:"):
                image_base64 = self.load_chart_screenshot(chart_image)
            else:
                image_base64 = chart_image

            # Build analysis prompt
            prompt = self._build_analysis_prompt(symbol, timeframe, additional_context)

            # Call Claude with vision
            message = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_base64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )

            # Parse response
            analysis_text = message.content[0].text
            analysis = self._parse_analysis(analysis_text)

            # Store in history
            self.analysis_history.append({
                "timestamp": datetime.utcnow().isoformat(),
                "symbol": symbol,
                "timeframe": timeframe,
                "analysis": analysis,
            })

            logger.info(f"✅ Chart analyzed: {symbol} {timeframe} | Sentiment: {analysis.get('sentiment')}")
            return analysis

        except Exception as e:
            logger.error(f"Chart analysis error: {e}")
            return {
                "error": str(e),
                "sentiment": "UNKNOWN",
                "patterns": [],
                "levels": {},
            }

    def _build_analysis_prompt(
        self,
        symbol: str,
        timeframe: str,
        context: Optional[Dict]
    ) -> str:
        """Build detailed analysis prompt for Claude."""

        context_str = ""
        if context:
            if "bid" in context and "ask" in context:
                context_str += f"\nCurrent price: Bid {context['bid']}, Ask {context['ask']}"
            if "volume" in context:
                context_str += f"\nVolume profile: {context['volume']}"

        return f"""
Analyze this {symbol} chart on {timeframe} timeframe.{context_str}

Provide a JSON response with these fields:

{{
  "sentiment": "BULLISH|BEARISH|NEUTRAL",
  "confidence": 0.0-1.0,
  "patterns": [
    {{
      "type": "head_shoulders|flag|triangle|zigzag|double_top|...",
      "location": "top|bottom|channel",
      "reliability": "high|medium|low"
    }}
  ],
  "support_resistance": {{
    "resistance": [price1, price2],
    "support": [price1, price2],
    "key_level": price
  }},
  "volatility": "high|medium|low",
  "volume_analysis": "increasing|decreasing|stable",
  "momentum": "strong|moderate|weak",
  "next_move": "up|down|range",
  "trading_setup": {{
    "type": "breakout|reversal|continuation|support_bounce",
    "probability": 0.0-1.0,
    "entry_zone": "price range or level"
  }},
  "risks": ["risk 1", "risk 2"],
  "notes": "short analysis summary"
}}

Focus on:
- Candlestick patterns and their reliability
- Support/resistance zones (not just single lines)
- Volume and volatility trends
- Entry signals that align with Qlab strategies (statistical mean reversion, momentum breakout)
- Risk factors (pullbacks, fake breakouts, news zones)

Keep response as valid JSON only, no extra text.
"""

    def _parse_analysis(self, response_text: str) -> Dict:
        """Parse Claude's JSON response."""
        try:
            # Extract JSON from response
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            json_str = response_text[start:end]
            analysis = json.loads(json_str)
            return analysis
        except json.JSONDecodeError:
            logger.warning("Could not parse analysis JSON, returning raw response")
            return {
                "sentiment": "UNKNOWN",
                "raw_response": response_text,
                "note": "Failed to parse structured response",
            }

    def get_analysis_summary(self) -> str:
        """Get summary of recent analyses."""
        if not self.analysis_history:
            return "No analyses yet"

        recent = self.analysis_history[-5:]
        summary = "Recent Chart Analyses:\n"
        for analysis in recent:
            summary += f"\n{analysis['symbol']} {analysis['timeframe']} - {analysis['timestamp']}\n"
            summary += f"  Sentiment: {analysis['analysis'].get('sentiment')}\n"
            summary += f"  Setup: {analysis['analysis'].get('trading_setup', {}).get('type')}\n"

        return summary
