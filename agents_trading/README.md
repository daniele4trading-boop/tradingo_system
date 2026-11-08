# Trading Agents - Exness Demo

**Status:** 🚀 In Development  
**Branch:** `claude/tradingo-agents-exness`  
**Broker:** Exness Demo (480578231)  
**Created:** 2026-11-08  

---

## 📋 Overview

Autonomous trading agents powered by Claude AI that:

1. **Chart Analyst** - Analyzes technical charts with vision + identifies patterns
2. **Strategy Executor** - Makes BUY/SELL/HOLD decisions based on chart analysis + Qlab strategies
3. **Risk Manager** - Manages position sizing, stops, and portfolio risk
4. **MT5 Connector** - Direct execution on MetaTrader 5 via Python

---

## 🏗️ Architecture

```
┌─────────────────────────────────┐
│   Chart Data (MT5 Terminal)     │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│   Chart Analyst (Claude Vision) │  ← Analyzes patterns, support/resistance
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│  Strategy Executor (Claude)     │  ← Decides entry, SL, TP, position size
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│   Risk Manager                  │  ← Validates risk, max positions, daily loss
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│   MT5 Connector (Direct Python) │  ← Executes orders on Exness
└─────────────────────────────────┘
```

---

## 📂 Structure

```
agents_trading/
├── config/
│   └── agents_config.yaml          # Configuration (broker, symbols, rules)
├── connectors/
│   ├── mt5_direct.py               # Direct MT5 connection (fast, <30ms)
│   └── metaapi_fallback.py         # Cloud fallback (WIP)
├── exness/
│   ├── chart_analyst.py            # Vision + pattern detection agent
│   ├── strategy_executor.py        # Trade decision making
│   └── risk_manager.py             # Position + portfolio risk (WIP)
├── backtest/
│   ├── engine.py                   # Backtest framework (WIP)
│   └── metrics.py                  # Performance metrics (WIP)
├── docs/
│   ├── SETUP.md                    # Installation steps
│   ├── STRATEGY.md                 # Qlab strategy mapping
│   └── API_REFERENCE.md            # Agent API docs
└── README.md                        # This file
```

---

## 🚀 Quick Start

### 1. Setup Exness Account

Follow `EXNESS_SETUP_STEPS.md`:
- [ ] Save password to DPAPI: `python C:\TG_TradinGo\secrets_store.py set EXNESS_MT5_PASSWORD_480578231`
- [ ] Add MT5 instance to config
- [ ] Connect MT5 terminal
- [ ] Create signals folder

### 2. Install Python Dependencies

```bash
cd /home/user/tradingo_system
pip install -r agents_trading/requirements.txt
```

(Create if needed:)
```
anthropic>=0.25.0
MetaTrader5>=5.0.45
pyyaml>=6.0
```

### 3. Test Connection

```python
from agents_trading.connectors.mt5_direct import MT5Connector

mt5 = MT5Connector(login=480578231, server="Exness-MT5Trial11")
mt5.connect(password="<from secrets>")
mt5.get_symbol_info("XAUUSD")
```

### 4. Run Chart Analyst

```python
from agents_trading.exness.chart_analyst import ChartAnalyst

analyst = ChartAnalyst()
analysis = analyst.analyze_chart(
    chart_image="path/to/xauusd_1h.png",
    symbol="XAUUSD",
    timeframe="1H"
)
```

### 5. Execute Strategy

```python
from agents_trading.exness.strategy_executor import StrategyExecutor

executor = StrategyExecutor()
signal = executor.execute_strategy(
    chart_analysis=analysis,
    symbol="XAUUSD",
    timeframe="1H",
    current_price={"bid": 2045.50, "ask": 2045.60}
)

if signal and signal.action != "HOLD":
    # Execute on MT5
    order = mt5.place_order(
        symbol=signal.symbol,
        action=signal.action.value,
        volume=signal.position_size,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
    )
```

---

## 🎯 Supported Symbols

### Gold
- **XAUUSD** (spot gold, XAU = 1 ounce)

### Major Forex
- **EURUSD** (EUR/USD)
- **GBPUSD** (GBP/USD)
- **USDJPY** (USD/JPY)

### Indices
- **SPX500** (S&P 500)
- **FTSE100** (FTSE 100)
- **DAX40** (German DAX)

---

## ⚙️ Configuration

Edit `config/agents_config.yaml`:

```yaml
trading_rules:
  entry_conditions:
    - mean_reversion_zscore: 2.0      # 2-sigma from 20-period mean
    - momentum_breakout: true         # Breakout of resistance with volume
    - volume_confirmation: true       # Volume must confirm move
    
  holding_period_minutes:
    min: 15                           # Min 15 min holding
    max: 240                          # Max 4 hours
    
risk_manager:
  max_open_positions: 3               # Max 3 trades at once
  max_daily_loss_pct: 5.0             # Max 5% daily loss
  risk_per_trade_pct: 1.0             # 1% per trade
```

---

## 📊 Trading Rules (Qlab-inspired)

### Entry Rules

1. **Mean Reversion**: Price 2-sigma from 20-period moving average
   - **Long**: Price at 2σ below mean → expect bounce up
   - **Short**: Price at 2σ above mean → expect pullback

2. **Momentum Breakout**: Break of resistance with volume confirmation
   - **Long**: Close above resistance + volume > average
   - **Short**: Close below support + volume > average

### Risk Management

- **Position Sizing**: Risk-based (1% per trade, 3% max exposure)
- **Stop Loss**: 20-40 pips (tight in demo)
- **Take Profit**: 2.5x risk-reward ratio
- **Max Daily Loss**: 5% of account

### Holding Periods

- **Min**: 15 minutes (avoid scalp noise)
- **Max**: 4 hours (intraday focus)
- **Typical**: 30-120 minutes

---

## 🔒 Security & Secrets

**Password Storage:**
- Stored in DPAPI archive: `C:\TG_TradinGo\secrets\store.dpapi`
- Retrieved at runtime from `secrets_store.py`
- Never logged or committed to git

**API Keys:**
- `ANTHROPIC_API_KEY` from environment or Devin Secrets panel
- Injected at runtime via `with_secrets.ps1`

---

## 📝 Logging

Logs to: `C:\TG_TradinGo\logs\agents.log`

```
2026-11-08 12:34:56 - Chart analyzed: XAUUSD 1H | Sentiment: BULLISH
2026-11-08 12:35:12 - 🎯 Trade Signal: BUY XAUUSD @ 2045.60 | SL: 2040.50, TP: 2055.60 | Conf: 0.75
2026-11-08 12:35:45 - ✅ Order placed: BUY 0.1 XAUUSD @ 2045.61 | Ticket: 112233
```

---

## 🧪 Testing

### Paper Trading (Demo Account)
- All trades are on demo account (no real money)
- Use for testing agent decisions, patterns, execution flow

### Backtest
```bash
python agents_trading/backtest/engine.py \
  --symbol XAUUSD \
  --timeframe 1H \
  --start 2026-09-01 \
  --end 2026-11-08 \
  --initial_capital 1000
```

---

## ⚠️ Demo Constraints

- ✅ **Allowed**: Test entry/exit logic, pattern recognition, MT5 execution
- ⚠️ **Limited**: 0.1 lot size (small position)
- ❌ **Not Allowed**: Real money until proven 30+ days of profitable paper trading

---

## 🔧 Development Roadmap

- [x] MT5 Direct Connector
- [x] Chart Analyst (Claude Vision)
- [x] Strategy Executor
- [ ] Risk Manager (position validation)
- [ ] Backtest Engine
- [ ] Live Agent Loop (continuous trading)
- [ ] Metrics Dashboard
- [ ] Performance Report Generator

---

## 📚 References

- **Strategy Base**: Qlab mean reversion + momentum breakout framework
- **Charts**: TradingView screenshots → Claude vision → JSON analysis
- **Broker API**: MetaTrader 5 Python package (native, <30ms latency)
- **Models**: Claude Opus 5.5 (vision + reasoning for decisions)

---

## 👤 Contact

**Repo**: `github.com/daniele4trading-boop/tradingo_system`  
**Branch**: `claude/tradingo-agents-exness`  
**VPS**: Gamehosting Italy (Tailscale 100.74.9.8)  

---

Generated by Claude Code | 2026-11-08
