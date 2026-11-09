# Trading Agents Setup Guide

**Status:** ✅ Ready for Paper Trading  
**Updated:** 2026-11-09  
**Branch:** `claude/tradingo-agents-exness`  

---

## ✅ Prerequisites (Completed)

- [x] VPS Gamehosting Windows (Tailscale 100.74.9.8)
- [x] MetaTrader 5 Exness installed (`C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`)
- [x] Exness demo account connected (480578231)
- [x] Password saved in DPAPI: `EXNESS_MT5_PASSWORD_480578231`
- [x] Python 3.11+ (venv: `C:\TG_TradinGo\.venv`)
- [x] `secrets_store.py` deployed (`C:\TG_TradinGo\secrets_store.py`)

---

## 🚀 Running the Agents

### From VPS Command Line

```powershell
# 1. SSH to VPS
ssh Administrator@100.74.9.8

# 2. Go to repo
cd C:\TG_TradinGo

# 3. Activate venv and run agents (60 minutes, XAUUSD/EURUSD)
C:\TG_TradinGo\.venv\Scripts\python.exe agents_trading/main.py --duration 60

# Or with API keys via secrets script
powershell -ExecutionPolicy Bypass -File scripts/with_secrets.ps1 \
    python C:\TG_TradinGo\agents_trading\main.py --duration 120 --symbols XAUUSD,EURUSD,GBPUSD
```

### From Devin Agent

```bash
# Clone latest code
cd /tmp && git clone https://github.com/daniele4trading-boop/tradingo_system.git --depth 1 -b claude/tradingo-agents-exness

# SSH and run
ssh Administrator@100.74.9.8 "cd C:\TG_TradinGo && C:\TG_TradinGo\.venv\Scripts\python.exe agents_trading/main.py --duration 120"

# Or with secrets script
ssh Administrator@100.74.9.8 "powershell -ExecutionPolicy Bypass -File scripts/with_secrets.ps1 python C:\TG_TradinGo\agents_trading\main.py --duration 120"
```

---

## 📋 Command Line Options

```
usage: main.py [-h] [--duration DURATION] [--symbols SYMBOLS] [--dry-run] [--mt5-path MT5_PATH]

options:
  -h, --help            show this help message and exit
  --duration DURATION   Duration to run in minutes (default: 60)
  --symbols SYMBOLS     Comma-separated symbols to trade (default: XAUUSD,EURUSD,GBPUSD)
  --dry-run             Run analysis only, no actual trades
  --mt5-path MT5_PATH   Path to MT5 terminal (default: C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe)

Examples:
  # Run for 2 hours on XAUUSD only
  python main.py --duration 120 --symbols XAUUSD

  # Dry run (analysis only, no trades)
  python main.py --dry-run --symbols XAUUSD,EURUSD

  # Run on all major pairs for 4 hours
  python main.py --duration 240 --symbols XAUUSD,EURUSD,GBPUSD,USDJPY
```

---

## 🏗️ System Architecture

```
Chart Data (MT5 Terminal)
        ↓
Chart Analyst (Claude Vision)
        ↓
Strategy Executor (Claude Reasoning)
        ↓
Risk Manager (Validation)
        ↓
MT5 Connector (Direct Python)
        ↓
Order Execution (MetaTrader 5)
```

### Agent Responsibilities

| Agent | Role | Model | Output |
|-------|------|-------|--------|
| **Chart Analyst** | Pattern detection + technical analysis | Claude Opus 5.5 (Vision) | Sentiment, patterns, entry zones |
| **Strategy Executor** | Trade decision making | Claude Opus 5.5 (Reasoning) | BUY/SELL/HOLD signal with entry, SL, TP |
| **Risk Manager** | Position validation | Local rules | Accept/Reject with reason |
| **MT5 Connector** | Order execution | Direct Python API | OrderResult (success/fail) |

---

## 🔐 Security

### Secrets Management

**Password Storage:**
```powershell
# Save Exness password to DPAPI
python C:\TG_TradinGo\secrets_store.py set EXNESS_MT5_PASSWORD_480578231
# When prompted: Scimmia.03 (or paste actual password)

# List all secrets
python C:\TG_TradinGo\secrets_store.py list

# Read specific secret (reveals value)
python C:\TG_TradinGo\secrets_store.py get EXNESS_MT5_PASSWORD_480578231 --reveal
```

**API Keys:**
- `ANTHROPIC_API_KEY` injected via `with_secrets.ps1` from Devin Secrets panel
- Never stored in config files or logs
- Read at runtime only

### Logging Security

All logs sanitized:
```
✅ No passwords logged
✅ No API keys logged
✅ No sensitive data in session files
✅ Logs only: trades, decisions, errors
```

---

## 📊 Monitoring

### Live Logs

```bash
# Watch logs in real-time
tail -f C:\TG_TradinGo\logs\agents_trading.log

# Or from local machine via SSH
ssh Administrator@100.74.9.8 "tail -f C:\TG_TradinGo\logs\agents_trading.log"
```

### Session Reports

Session data saved to:
```
C:\TG_TradinGo\logs\session_YYYYMMDD_HHMMSS.json
```

Contains:
- Timestamp and duration
- All trade signals
- Statistics (wins, losses, PnL)
- Account balance changes

### Status Check

```python
# From Python REPL
from agents_trading.exness.orchestrator import TradingOrchestrator

orch = TradingOrchestrator(480578231, "Exness-MT5Trial11", password="...")
orch.connect(password)
status = orch.get_status()
print(status)
```

---

## ⚠️ Demo Account Constraints

- ✅ **Paper trading only** - no real money
- ✅ **Max 0.1 lot per trade** (can adjust in config)
- ✅ **Max 3 concurrent positions**
- ✅ **Max 5% daily loss limit**
- ✅ **24/5 forex market hours** (Mon-Fri 21:00 UTC - Fri 21:00 UTC)

---

## 🧪 Testing Modes

### Dry Run (Analysis Only)

```bash
# Analyze XAUUSD for 1 hour, no trades
python main.py --duration 60 --symbols XAUUSD --dry-run

# Output: Chart analysis, strategy decisions, but no order execution
```

### Single Symbol

```bash
# Trade only XAUUSD
python main.py --duration 120 --symbols XAUUSD

# Or all majors
python main.py --duration 240 --symbols XAUUSD,EURUSD,GBPUSD,USDJPY,SPX500
```

---

## 📈 Performance Tracking

### Daily Report

```bash
# Generate daily stats
python C:\TG_TradinGo\tools\report_today.py --days 1 --out report.md

# View latest session
more C:\TG_TradinGo\logs\session_*.json | python -m json.tool
```

### Key Metrics

- **Win Rate**: % of profitable trades
- **Risk:Reward Ratio**: Average TP/SL distance ratio
- **Daily PnL**: Profit/loss for the day
- **Max Drawdown**: Largest peak-to-trough decline
- **Sharpe Ratio**: Risk-adjusted returns

---

## 🔧 Troubleshooting

### Connection Issues

```
ERROR: Failed to connect to MT5
→ Check MT5 terminal is open and connected to Exness
→ Verify account 480578231 is logged in
→ Check C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe path
```

### Password Not Found

```
ERROR: EXNESS_MT5_PASSWORD_480578231 not set
→ Save password: python secrets_store.py set EXNESS_MT5_PASSWORD_480578231
→ Verify with: python secrets_store.py list | findstr EXNESS
```

### Import Errors

```
ERROR: ModuleNotFoundError: No module named 'MetaTrader5'
→ Make sure venv is activated
→ Install: pip install MetaTrader5 anthropic pyyaml
```

### Low Confidence Signals

```
No trade signal (confidence too low)
→ Reduce confidence threshold in config (default: 0.55)
→ Enable more chart analysis time (increase --duration)
→ Check market volatility and news calendar
```

---

## 📝 Configuration

Edit `C:\TG_TradinGo\agents_trading\config\agents_config.yaml`:

```yaml
# Risk limits
risk_manager:
  max_open_positions: 3      # Max concurrent trades
  max_daily_loss_pct: 5.0    # Daily loss limit
  risk_per_trade_pct: 1.0    # Risk per trade

# Entry rules
trading_rules:
  entry_conditions:
    - mean_reversion_zscore: 2.0
    - momentum_breakout: true

# Symbols
mt5:
  enabled_symbols:
    - XAUUSD
    - EURUSD
    - GBPUSD
    - USDJPY
```

---

## 🚀 Next Steps

1. **Paper trade for 30+ days** (minimum before live)
2. **Track performance metrics** (win rate, Sharpe ratio)
3. **Validate strategy on backtest data**
4. **Adjust risk limits based on results**
5. **Deploy to live account** (when ready)

---

## 📚 References

- **Config**: `agents_trading/config/agents_config.yaml`
- **Agents**: `agents_trading/exness/` (chart_analyst.py, strategy_executor.py, orchestrator.py)
- **Connectors**: `agents_trading/connectors/mt5_direct.py`
- **Main**: `agents_trading/main.py`
- **Repo**: github.com/daniele4trading-boop/tradingo_system (branch: claude/tradingo-agents-exness)

---

Generated by Claude Code | 2026-11-09
