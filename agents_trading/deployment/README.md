# Deployment Scripts - STEP 5: Paper Trading

Scripts PowerShell per deploy su GameHosting VPS e setup di trading 24/7.

## Files

| File | Purpose |
|------|---------|
| `DEPLOY.md` | Complete deployment guide (read first) |
| `set_secrets.ps1` | Encrypt and store MT5 password + API key using DPAPI |
| `setup_task_scheduler.ps1` | Create Windows Task Scheduler job for daily 06:00 UTC start |
| `monitor_trading.ps1` | Real-time monitoring of P&L and trading activity |

## Quick Start

### 1. SSH to VPS

```bash
ssh -p 2222 Administrator@144.91.76.28
```

### 2. Clone and checkout branch

```powershell
cd C:\StatArb\
git clone https://github.com/daniele4trading-boop/tradingo_system.git
cd tradingo_system
git checkout claude/serene-einstein-d6667p
```

### 3. Setup credentials (DPAPI encrypted)

```powershell
cd C:\StatArb\tradingo_system\agents_trading
.\deployment\set_secrets.ps1 -ApiKey "sk-your-key" -Account 480578231 -Password "your-password"
```

### 4. Install Python dependencies

```powershell
pip install anthropic pandas numpy pytz
```

### 5. Create Task Scheduler job (runs daily at 06:00 UTC)

```powershell
# Must run as Administrator
.\deployment\setup_task_scheduler.ps1
```

### 6. Monitor live trading

```powershell
.\deployment\monitor_trading.ps1
```

## Configuration

Edit these before running:

**Main entry point:**
- `C:\StatArb\tradingo_system\agents_trading\main.py`
- Change symbols, duration, cycle time

**Risk settings:**
- `agents_trading/exness/risk_manager.py` - Line ~50
- `max_positions`, `daily_loss_limit_pct`, `risk_per_trade_pct`

## Log Files

- **Main log**: `C:\StatArb\logs\agents_trading.log`
- **Session reports**: `C:\StatArb\logs\agents_trading_session_*.json`
- **Backtest results**: `C:\StatArb\tradingo_system\agents_trading\backtest_*.csv`

## Troubleshooting

**Credentials error:**
```powershell
# Re-setup credentials
.\set_secrets.ps1 -ApiKey "sk-..." -Account 480578231 -Password "pwd"
```

**Task won't start:**
```powershell
# Check task status
Get-ScheduledTask -TaskName "AgentsTrading" | Select-Object State
Get-ScheduledTaskInfo -TaskName "AgentsTrading"

# View last run result
Get-EventLog -LogName Application -InstanceId 1000 | Select-Object -Last 5
```

**Process hanging:**
```powershell
# Kill process
Stop-Process -Name python -Force

# Re-start manually
cd C:\StatArb\tradingo_system\agents_trading
python main.py --duration 60 --symbols XAUUSD
```

## Safety Checks

Before running on live:

- [ ] Dry-run works (30 min): `python main.py --duration 30 --symbols XAUUSD --dry-run`
- [ ] Demo account connected
- [ ] Daily loss limit set (5%)
- [ ] Max positions limited (3)
- [ ] Risk per trade set (1%)
- [ ] Logs accessible and readable
- [ ] Task scheduler configured

## Running Paper Trading

Paper trading runs **1440 minutes (24 hours)** per day, starting at **06:00 UTC**.

Timeline:
```
06:00 UTC  - Task starts, Python launches
06:00-30:30 - 30s analysis cycles running
Throughout day - Trades executed, P&L tracked
23:00 UTC  - Task stops (1440 min elapsed)
00:00 UTC  - Wait 6 hours
06:00 UTC  - Task starts again next day
```

Monitor with: `.\monitor_trading.ps1`

## Credentials Storage

Credentials encrypted with **DPAPI**:
- User-specific ✅
- Machine-specific ✅
- Survives reboot ✅
- Cannot be moved to other machine ❌

Location: `%APPDATA%\agents_trading\secrets.json`

Never hardcode passwords in code!

## Next Steps

1. **Run for 1 week**: Validate P&L against backtest expectations
2. **Review results**: Win rate, max drawdown, Sharpe ratio
3. **If valid**: Proceed to real account (STEP 6)
4. **If issues**: Debug and re-test on demo

See `DEPLOY.md` for full details.
