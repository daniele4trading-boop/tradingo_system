# VPS Deployment Guide - STEP 5: Paper Trading

## Overview

Deploy orchestrator su **GameHosting Windows VPS** per paper trading 24/7 su demo Exness.

**VPS Details:**
- IP: `144.91.76.28`
- SSH Port: `2222`
- User: `Administrator`
- Path: `C:\StatArb\tradingo_system\`

## Prerequisiti

1. **VPS Access**: SSH key configured per port 2222
2. **MT5 Terminal**: Installato a `C:\Program Files\MT5`
3. **Python 3.11+**: Installato e nel PATH
4. **Exness Demo Account**: Account 480578231, password in DPAPI

## Deployment Steps

### 1. SSH into VPS

```bash
ssh -p 2222 Administrator@144.91.76.28
```

### 2. Clone Repository

```powershell
# PowerShell
cd C:\StatArb\
git clone https://github.com/daniele4trading-boop/tradingo_system.git
cd tradingo_system
git checkout claude/serene-einstein-d6667p
```

### 3. Install Python Dependencies

```powershell
# Install required packages
pip install anthropic pandas numpy pytz

# Optional: For real MetaTrader5 connection
pip install MetaTrader5
```

### 4. Setup Credentials (DPAPI)

Use the provided PowerShell scripts to encrypt credentials:

```powershell
# Set API Key
.\deployment\set_secrets.ps1 -ApiKey "sk-your-api-key" -Account 480578231 -Password "your-mt5-password"
```

This stores encrypted credentials in:
- `%APPDATA%\agents_trading\secrets.json` (DPAPI encrypted)

### 5. Verify MT5 Connection

```powershell
cd C:\StatArb\tradingo_system\agents_trading

# Test mock mode (no MT5 needed)
python -c "from exness.mt5_connector import MT5Connector; mt5 = MT5Connector(); print('Mock mode ready')"

# Or test real connection
python -c "from exness.mt5_connector import MT5Connector; mt5 = MT5Connector(); mt5.connect('password'); print(mt5.connected)"
```

### 6. Run Initial Test

```powershell
# 30-minute dry run
python main.py --duration 30 --symbols XAUUSD --dry-run
```

Expected output:
```
🚀 Trading Orchestrator initialized
   Symbols: XAUUSD
   Account: 480578231
✅ Connected successfully
🟢 Starting trading loop for 30 minutes
  Analysis cycle: every 30s
```

### 7. Setup Task Scheduler for 24/7 Trading

Run the deployment script to auto-create Windows task:

```powershell
.\deployment\setup_task_scheduler.ps1
```

This creates a task that:
- Runs daily at **06:00 UTC** (Forex opens)
- Duration: **1440 minutes** (24 hours)
- Symbols: **XAUUSD, EURUSD, GBPUSD**
- Logs to: `C:\StatArb\logs\agents_trading.log`
- Auto-restarts on failure

### 8. Monitor Live P&L

Check logs in real-time:

```powershell
# Watch log file updates
Get-Content C:\StatArb\logs\agents_trading.log -Tail 50 -Wait

# Or use the monitoring script
.\deployment\monitor_trading.ps1
```

Expected log output:
```
2025-11-09 14:30:00 - TradingOrchestrator - INFO - 🟢 Starting trading loop for 1440 minutes
2025-11-09 14:30:30 - StrategyExecutor - INFO - 📊 Signal: BUY (confidence: 0.72)
2025-11-09 14:30:35 - MT5Connector - INFO - ✅ Order placed: BUY 0.1L XAUUSD @ 2000.50
2025-11-09 14:31:00 - RiskManager - INFO - Position closed: P&L +45.50$ | Reason: TP_HIT
```

### 9. Setup Monitoring Dashboard (Optional)

For web-based monitoring:

```powershell
# Start web server (runs on localhost:8080)
python deployment/monitor_server.py
```

Then access from your machine:
```
http://144.91.76.28:8080/dashboard
```

Shows real-time:
- Current P&L
- Open positions
- Trade history
- Hypothesis accuracy

## Configuration

Edit `agents_trading/config.yaml` before running:

```yaml
# Symbols to trade
symbols:
  - XAUUSD
  - EURUSD
  - GBPUSD

# Risk settings
risk_per_trade_pct: 1.0        # 1% per trade
max_positions: 3                # Max 3 open
daily_loss_limit_pct: 5.0      # Stop if -5% daily

# Timing
cycle_seconds: 30               # Analysis every 30s
holding_time_minutes: 15-240    # Typical holding time
```

## Monitoring Checklist

**Daily (06:00 UTC):**
- [ ] Check task scheduler status: `Get-ScheduledTask -TaskName "AgentsTrading"`
- [ ] Verify process running: `Get-Process python`
- [ ] Check log file for errors
- [ ] Monitor P&L: `tail -f C:\StatArb\logs\agents_trading.log`

**Weekly:**
- [ ] Review hypothesis accuracy (CSV exports)
- [ ] Check max drawdown
- [ ] Verify no daily loss limit hits
- [ ] Review trade history quality

**Monthly:**
- [ ] Pull latest code: `git pull origin claude/serene-einstein-d6667p`
- [ ] Test paper trading → live migration checklist
- [ ] Validate P&L matches expectations

## Troubleshooting

### Task doesn't start
```powershell
# Check task history
Get-ScheduledTaskInfo -TaskName "AgentsTrading"

# Run manually to debug
cd C:\StatArb\tradingo_system\agents_trading
python main.py --duration 60 --symbols XAUUSD
```

### MT5 connection fails
```powershell
# Verify MT5 running
Get-Process terminal*

# Check account login
python -c "
from exness.mt5_connector import MT5Connector
mt5 = MT5Connector()
if mt5.connect('password'):
    print('Connected OK')
else:
    print('Connection failed')
"
```

### No trades executing
- Check log for RiskManager validation failures
- Verify confidence threshold (min 0.55)
- Check daily loss limit not hit (5%)
- Verify current_price within support/resistance range

### High slippage or no fills
- Demo account may have less liquidity
- Try increasing `cycle_seconds` to allow better execution
- Check quote freshness in MT5 terminal

## Credential Security

Credentials stored via DPAPI:
- Encrypted at rest: ✅ Yes
- User-specific: ✅ Yes
- Machine-specific: ✅ Yes
- Survives reboot: ✅ Yes
- Readable in code: ❌ No

Never hardcode passwords or API keys in code!

## Next Steps

1. **Run for 1 week** on demo to validate
2. **Collect results**: P&L, win rate, max drawdown
3. **Compare to backtest expectations** (55-65% win rate, 1.5-2.0 R:R)
4. **If validated**: Proceed to real account setup
5. **If issues**: Debug and re-test (stay on demo)

## Support

- Logs: `C:\StatArb\logs\agents_trading_*.log`
- Session reports: `C:\StatArb\logs\agents_trading_session_*.json`
- Error logs: Check Windows Event Viewer → Application

Questions? Check `agents_trading/SETUP.md` for component details.
