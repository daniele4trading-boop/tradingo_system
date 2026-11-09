# QLAB Integration Setup

## Overview

STEP 3: Integrate Qlab analysis into trading agents.

Modules:
1. **liquidity_analyzer.py**: Detect liquidity levels from OHLC data
2. **volume_analyzer.py**: Analyze volume-price correlations  
3. **qlab_hypotheses.py**: Evaluate 30 hypotheses (H0-H30) on price action
4. **qlab_backtest_runner.py**: Full backtest runner with Qlab context

## Quick Start

### Run Qlab Backtest

```python
from agents_trading.backtest.qlab_backtest_runner import QLabBacktestRunner

runner = QLabBacktestRunner(
    symbol="XAUUSD",
    timeframe="H1",
    start_date="2025-01-01",
    end_date="2026-11-08",
)
runner.run(max_bars=500)
runner.print_results()
runner.export_trades_csv("trades_qlab.csv")
runner.export_hypothesis_analysis("hypothesis_analysis.csv")
```

## Architecture

### 1. Liquidity Analyzer

Detects 6 level families per SPECIFICA_QLAB.md:

**Structural Levels** (fractals N=2,3,5)
- Swing High: local maximum
- Swing Low: local minimum

**Zone Levels**
- Order Blocks: impulse/rejection zones
- FVG (Fair Value Gaps): gaps between candles

**Hot Levels**
- PDH/PDL: Previous Day High/Low
- Round Numbers: 5, 10, 25, 50, 100

**Dynamic**
- EMA 21: Moving average level

**Volumetric**
- POC (Point of Control): highest volume price
- Value Area: 70% volume range

Example:
```python
analyzer = LiquidityAnalyzer(symbol="XAUUSD")
levels = analyzer.detect_levels(df, timeframe="H1")
nearby = analyzer.get_levels_near_price(current_price=2000.50)
```

### 2. Volume Analyzer

Analyzes 4 hypotheses (H9, H28, H29, H30):

**H9: Volume Absorption**
- High relative volume (vs 20-day avg same hour)
- Balanced delta (opposing price direction)
- → Indicates sweep reaction expected

**H28: Mean Reversion**
- Price outside Value Area
- Distance from POC
- → Score for reversion probability

**H29: LVN/HVN Analysis**
- Low Volume Node (vacuum): price accelerates
- High Volume Node (resistance): price decelerates
- → Forecast price acceleration/deceleration

**H30: Tick vs Real Volume Agreement**
- Correlate tick volume (Dukascopy) vs real volume (CME)
- → Validate robustness of signals

Example:
```python
analyzer = VolumeAnalyzer(symbol="XAUUSD")
metrics = analyzer.analyze_event(df, timestamp, event_idx)

# Detect absorption
is_absorption = analyzer.detect_absorption(df, event_idx)

# Mean reversion score
mr_score, va_side = analyzer.mean_reversion_score(
    current_price, poc_price, va_high, va_low
)

# LVN/HVN analysis
hvn_data = analyzer.lvn_hvn_analysis(df, current_price)
```

### 3. QLAB Hypotheses

Evaluate all 30 hypotheses (H0-H30):

**Mean Reversion (H0-H3)**
- H0: Price above VA → pull to POC
- H1: Price below VA → pull to POC
- H2: Price at resistance → rejection
- H3: Price at support → bounce

**Momentum (H4-H8)**
- H4: Bullish breakout with volume
- H5: Bearish breakdown with volume
- H6: False breakout setup
- H7-H8: (Breakout hold / failed breakout)

**Volume (H9)**
- H9: Volume absorption pattern

**Confluence (H10-H15)**
- H10: Multiple levels at same price
- H11: MTF bullish + support
- H12: MTF bearish + resistance
- H13-H15: (Multi-TF agreement)

**Order Flow (H16-H20)**
- H16: FVG nearby + volume increase
- H17: Multiple order blocks
- H18-H20: (Delta, imbalance)

**Temporal (H21-H25)**
- H21: Asia session open
- H22: London session open
- H23: NY session open (highest volume)
- H24-H25: (News impact)

**Advanced (H26-H30)**
- H26: LVN vacuum → acceleration
- H27: HVN resistance → deceleration
- H28: Extreme distance from POC
- H29: Volume profile stability
- H30: Tick/real volume agreement

Example:
```python
hypotheses = QLabHypotheses()

# Evaluate all hypotheses
scores = hypotheses.evaluate_all_hypotheses(
    current_price=2000.50,
    poc_price=2000.00,
    va_high=2001.00,
    va_low=1999.00,
    support=1995.00,
    resistance=2005.00,
    volume_metrics={...},
    levels_nearby=[...],
    timeframe_bias="BULLISH",
    hour_of_day=14,
)

# Convert to signal
action, confidence = hypotheses.generate_signal_from_hypotheses(scores)
# → ("BUY", 0.72)
```

### 4. QLAB Backtest Runner

Full integration with trading agents:

Flow:
1. Load historical OHLC data
2. For each bar:
   - **Detect liquidity levels**: swings, order blocks, FVG, POC
   - **Analyze volume-price**: absorption, nodes, VAG
   - **Evaluate hypotheses**: score all H0-H30
   - **Run agents**: ChartAnalyst → StrategyExecutor → RiskManager
   - **Execute trades**: with Qlab context
3. Report results with hypothesis accuracy

Example:
```python
runner = QLabBacktestRunner(
    symbol="XAUUSD",
    timeframe="H1",
    start_date="2025-01-01",
    end_date="2025-12-31",
    initial_balance=1000.0,
)
runner.run(max_bars=5000, verbose=True)
runner.print_results()

# Export for analysis
runner.export_trades_csv("trades.csv")
runner.export_hypothesis_analysis("hypotheses.csv")
```

## Data Sources

### Dukascopy Tick Data
- Built-in: `df["volume"]` = tick volume
- Use for: Level detection, POC, absorption patterns

### CME Globex Real Volume (Optional)
- If available: `df["real_volume"]` = trade count volume
- Use for: H30 validation (tick vs real correlation)
- Get from: Databento API or manual import

### Forex Factory News Calendar
- Use for: H24-H25 temporal patterns
- Not yet integrated (placeholder in code)

## Integration with Agents

### ChartAnalyst Enhancement
Instead of:
```python
chart_analysis = {
    "sentiment": "BULLISH",
    "confidence": 0.65,
}
```

Now include Qlab context:
```python
chart_analysis = {
    "sentiment": "BULLISH",
    "confidence": 0.65,
    "qlab_context": {
        "action": "BUY",
        "hypothesis_scores": [
            ("H11", 0.75),  # MTF bullish + support
            ("H21", 0.60),  # NY session open
            ("H28", 0.68),  # Distance from POC
        ],
        "nearby_levels": 3,
        "volume_nodes": "LVN",  # Low Volume Node → acceleration
    },
}
```

### StrategyExecutor Enhancement
Entry rules now backed by Qlab:
- **Qlab-Bullish** = at least 2 strong hypotheses with avg score > 0.65
- **Qlab-Bearish** = at least 2 bearish hypotheses with avg score > 0.65
- **Qlab-Hold** = conflicting or low-confidence signals

### RiskManager Integration
Adjust position sizing based on Qlab confidence:
- High confidence (0.70+) → size_multiplier = 1.0
- Medium confidence (0.55-0.70) → size_multiplier = 0.7
- Low confidence (<0.55) → size_multiplier = 0.5

## Validation

### Hypothesis Accuracy Tracking

For each hypothesis, track:
- Average score when triggered
- Win rate when signal generated
- Average P&L per signal

Example output:
```
H0: avg_score=0.68, count=127, accuracy=62%
H1: avg_score=0.71, count=95, accuracy=65%
H9: avg_score=0.74, count=43, accuracy=74%
H11: avg_score=0.76, count=81, accuracy=71%
...
```

### Statistical Protocol (per SPECIFICA_QLAB.md)
- Minimum 80% year concordance for accepting hypothesis
- 5% minimum effect size
- FDR correction for multiple hypotheses

## Performance Expectations

Based on SPECIFICA_QLAB.md Section 9:

**Backtest Results Expected:**
- Win rate: 55-65% (mean reversion strategies)
- Avg win/loss ratio: 1.5-2.0
- Sharpe ratio: 1.0-1.5 (before slippage/commissions)

**Key Metrics:**
- Max drawdown: <15%
- Monthly positive months: >70%
- Best hypothesis set: H11 + H28 + H21 (timing + confluence + distance)

## Next Steps

1. **Add real volume data** from Databento (H30 validation)
2. **Add news calendar** from Forex Factory (H24-H25 temporal)
3. **Implement multi-timeframe** detection (H13-H15)
4. **Add order flow** analysis from market microstructure (H18-H20)
5. **Deploy to live paper trading** with 2-3 week validation

## Troubleshooting

### "No levels detected"
- Check: Do you have at least 50 bars of data?
- Check: Are OHLC values reasonable (high >= low)?

### "Hypothesis scores all zero"
- Check: Current price and level prices aligned? (same scale)
- Check: Volume data available in DataFrame?

### "Signal confidence too low"
- Increase lookback period (50→100 bars)
- Add more data sources (real volume, news)
- Adjust hypothesis thresholds in QLabHypotheses

## Files

```
agents_trading/qlab/
├── __init__.py                      # Module imports
├── SETUP_QLAB.md                    # This file
├── liquidity_analyzer.py            # Level detection
├── volume_analyzer.py               # Volume-price analysis
├── qlab_hypotheses.py               # H0-H30 hypothesis engine
├── backtest/
│   └── qlab_backtest_runner.py      # Full backtest runner
```

## References

- SPECIFICA_QLAB.md: Complete specification of levels, hypotheses, and protocol
- agents_trading/docs/SETUP.md: Main trading system setup
- agents_trading/backtest/: Standard backtest framework (extended by Qlab)
