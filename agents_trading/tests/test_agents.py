#!/usr/bin/env python3
"""
Unit Tests for Trading Agents

Tests core functionality of each agent before paper trading.
"""

import sys
import os
import unittest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from exness.chart_analyst import ChartAnalyst, ChartAnalysis
from exness.strategy_executor import StrategyExecutor, TradeSignal, TradeAction
from exness.risk_manager import RiskManager, RiskValidation
from exness.mt5_connector import MT5Connector, OrderResult


class TestChartAnalyst(unittest.TestCase):
    """Test chart analysis."""

    def setUp(self):
        """Initialize analyst."""
        self.analyst = ChartAnalyst(api_key=None)  # Mock mode

    def test_mock_analysis(self):
        """Test mock chart analysis."""
        analysis = self.analyst.analyze_chart(
            chart_description="Test chart",
            symbol="XAUUSD",
            timeframe="H1",
        )

        self.assertIsNotNone(analysis)
        self.assertIn(analysis.sentiment, ["BULLISH", "BEARISH", "NEUTRAL"])
        self.assertGreaterEqual(analysis.confidence, 0.5)
        self.assertLessEqual(analysis.confidence, 1.0)
        self.assertIn("support", analysis.support_resistance)
        self.assertIn("resistance", analysis.support_resistance)

    def test_support_resistance_detected(self):
        """Test that support/resistance are in analysis."""
        analysis = self.analyst.analyze_chart(
            chart_description="XAUUSD chart",
            symbol="XAUUSD",
            timeframe="H1",
        )

        support_values = analysis.support_resistance.get("support", [])
        resistance_values = analysis.support_resistance.get("resistance", [])

        self.assertGreater(len(support_values), 0)
        self.assertGreater(len(resistance_values), 0)
        self.assertLess(support_values[0], resistance_values[0])


class TestStrategyExecutor(unittest.TestCase):
    """Test strategy execution."""

    def setUp(self):
        """Initialize executor."""
        self.executor = StrategyExecutor()

    def test_bullish_signal_generation(self):
        """Test BUY signal from bullish chart."""
        chart_analysis = {
            "sentiment": "BULLISH",
            "confidence": 0.70,
            "patterns": [{"type": "breakout", "reliability": "high"}],
            "support_resistance": {
                "support": [1990.0],
                "resistance": [2050.0],  # Wider range for better R:R
            },
            "trading_setup": {"type": "breakout", "probability": 0.70},
            "risks": [],
        }

        signal = self.executor.execute_strategy(
            chart_analysis=chart_analysis,
            symbol="XAUUSD",
            timeframe="H1",
            current_price={"bid": 2000.0, "ask": 2000.1},
            open_positions=0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, TradeAction.BUY)
        self.assertGreaterEqual(signal.confidence, 0.70)
        self.assertGreaterEqual(signal.risk_reward_ratio, 1.5)

    def test_bearish_signal_generation(self):
        """Test SELL signal from bearish chart."""
        chart_analysis = {
            "sentiment": "BEARISH",
            "confidence": 0.65,
            "patterns": [{"type": "breakdown", "reliability": "medium"}],
            "support_resistance": {
                "support": [1950.0],  # Wider range for better R:R
                "resistance": [2010.0],
            },
            "trading_setup": {"type": "breakdown", "probability": 0.65},
            "risks": [],
        }

        signal = self.executor.execute_strategy(
            chart_analysis=chart_analysis,
            symbol="XAUUSD",
            timeframe="H1",
            current_price={"bid": 2000.0, "ask": 2000.1},
            open_positions=0,
        )

        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, TradeAction.SELL)

    def test_neutral_no_signal(self):
        """Test no signal from neutral chart."""
        chart_analysis = {
            "sentiment": "NEUTRAL",
            "confidence": 0.50,
            "patterns": [],
            "support_resistance": {},
            "trading_setup": {},
            "risks": [],
        }

        signal = self.executor.execute_strategy(
            chart_analysis=chart_analysis,
            symbol="XAUUSD",
            timeframe="H1",
            current_price={"bid": 2000.0, "ask": 2000.1},
            open_positions=0,
        )

        self.assertIsNone(signal)

    def test_low_confidence_rejected(self):
        """Test signal rejected due to low confidence."""
        chart_analysis = {
            "sentiment": "BULLISH",
            "confidence": 0.40,  # Below threshold 0.55
            "patterns": [],
            "support_resistance": {},
            "trading_setup": {},
            "risks": [],
        }

        signal = self.executor.execute_strategy(
            chart_analysis=chart_analysis,
            symbol="XAUUSD",
            timeframe="H1",
            current_price={"bid": 2000.0, "ask": 2000.1},
            open_positions=0,
        )

        self.assertIsNone(signal)


class TestRiskManager(unittest.TestCase):
    """Test risk management."""

    def setUp(self):
        """Initialize risk manager."""
        self.rm = RiskManager(
            account_balance=1000.0,
            risk_per_trade_pct=1.0,
            max_open_positions=3,
            daily_loss_limit_pct=5.0,
        )

    def test_valid_signal(self):
        """Test valid signal passes risk checks."""
        # Create a valid trade signal
        class MockSignal:
            def __init__(self):
                self.entry_price = 2000.0
                self.stop_loss = 1990.0
                self.position_size = 0.1

        signal = MockSignal()

        validation = self.rm.validate_signal(signal, num_open_positions=0)

        self.assertTrue(validation.valid)
        self.assertEqual(validation.reason, "OK")
        self.assertGreater(validation.adjusted_size, 0)

    def test_max_positions_limit(self):
        """Test that max position limit is enforced."""
        class MockSignal:
            def __init__(self):
                self.entry_price = 2000.0
                self.stop_loss = 1990.0
                self.position_size = 0.1

        signal = MockSignal()

        validation = self.rm.validate_signal(signal, num_open_positions=3)

        self.assertFalse(validation.valid)
        self.assertIn("Max open positions", validation.reason)

    def test_daily_loss_limit(self):
        """Test that daily loss limit is enforced."""
        # Simulate large loss
        self.rm.daily_pnl = -60.0  # -6% of $1000

        class MockSignal:
            def __init__(self):
                self.entry_price = 2000.0
                self.stop_loss = 1990.0
                self.position_size = 0.1

        signal = MockSignal()

        validation = self.rm.validate_signal(signal, num_open_positions=0)

        self.assertFalse(validation.valid)
        self.assertIn("Daily loss limit", validation.reason)

    def test_min_stop_loss(self):
        """Test that minimum stop loss is enforced."""
        class MockSignal:
            def __init__(self):
                self.entry_price = 2000.0
                self.stop_loss = 1999.8  # Only 2 pips, below 20 pips minimum
                self.position_size = 0.1

        signal = MockSignal()

        validation = self.rm.validate_signal(signal, num_open_positions=0)

        self.assertFalse(validation.valid)
        self.assertIn("Stop loss too tight", validation.reason)


class TestMT5Connector(unittest.TestCase):
    """Test MT5 connector."""

    def setUp(self):
        """Initialize connector in mock mode."""
        self.mt5 = MT5Connector()

    def test_mock_mode_initialization(self):
        """Test that mock mode initializes without errors."""
        self.assertFalse(self.mt5.connected)
        self.assertTrue(self.mt5.mock_mode)

    def test_mock_connection(self):
        """Test mock connection."""
        result = self.mt5.connect("demo_password")

        self.assertTrue(result)
        self.assertTrue(self.mt5.connected)

    def test_mock_place_order(self):
        """Test mock order placement."""
        self.mt5.connect("demo_password")

        result = self.mt5.place_order(
            symbol="XAUUSD",
            action="BUY",
            entry_price=2000.0,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2010.0,
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.ticket)
        self.assertEqual(result.entry_price, 2000.0)

    def test_mock_get_open_positions(self):
        """Test getting open positions."""
        self.mt5.connect("demo_password")

        # Place order
        self.mt5.place_order(
            symbol="XAUUSD",
            action="BUY",
            entry_price=2000.0,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2010.0,
        )

        # Get positions
        positions = self.mt5.get_open_positions()

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["symbol"], "XAUUSD")
        self.assertEqual(positions[0]["volume"], 0.1)

    def test_mock_close_order(self):
        """Test order closing."""
        self.mt5.connect("demo_password")

        # Place order
        result = self.mt5.place_order(
            symbol="XAUUSD",
            action="BUY",
            entry_price=2000.0,
            volume=0.1,
            stop_loss=1990.0,
            take_profit=2010.0,
        )

        # Close order
        close_result = self.mt5.close_order(result.ticket, exit_price=2005.0)

        self.assertTrue(close_result.success)

        # Verify position closed
        positions = self.mt5.get_open_positions()
        self.assertEqual(len(positions), 0)


def run_tests():
    """Run all tests."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add test classes
    suite.addTests(loader.loadTestsFromTestCase(TestChartAnalyst))
    suite.addTests(loader.loadTestsFromTestCase(TestStrategyExecutor))
    suite.addTests(loader.loadTestsFromTestCase(TestRiskManager))
    suite.addTests(loader.loadTestsFromTestCase(TestMT5Connector))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
