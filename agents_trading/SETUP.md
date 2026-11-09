# Trading Agents Setup Guide

## Architecture

```
MT5 Market Data
    ↓
ChartAnalyst (Claude vision: candlestick analysis)
    ↓
StrategyExecutor (Trade signal generation)
    ↓
RiskManager (Position sizing, validation)
    ↓
MT5Connector (Order execution)
    ↓
Orchestrator (Main loop, coordination)
```

## Quick Start (Demo Exness)

```bash
export ANTHROPIC_API_KEY="sk-..."
export EXNESS_MT5_PASSWORD_480578231="your_password"

python agents_trading/main.py \
    --duration 120 \
    --symbols XAUUSD,EURUSD \
    --dry-run
```

## Components

### 1. ChartAnalyst
Claude Opus 5.5 vision model analyzes candlestick charts.

**Input**: Chart screenshot (previous 50 bars)
**Output**: 
```python
{
    "sentiment": "BULLISH",  # or BEARISH, NEUTRAL
    "confidence": 0.65,      # 0-1
    "patterns": [{"type": "head_shoulders", "reliability": "medium"}],
    "support_resistance": {"resistance": [2005.00], "support": [1995.00]},
    "trading_setup": {"type": "breakout", "probability": 0.65},
    "risks": ["pullback", "news"]
}
```

### 2. StrategyExecutor
Converts chart analysis → trading signal.

**Input**: ChartAnalysis + current price + account state
**Output**:
```python
TradeSignal(
    action="BUY",                # or SELL, HOLD
    entry_price=2000.50,
    stop_loss=1995.00,
    take_profit=2005.00,
    position_size=0.1,           # lots
    confidence=0.72,
    risk_reward_ratio=2.5
)
```

### 3. RiskManager
Validates signal against risk limits.

**Checks**:
- Max open positions: 3
- Daily loss limit: 5% of balance
- Min stop loss: 20 pips
- Position sizing: 1% risk per trade

**Output**:
```python
RiskValidation(
    valid=True,
    reason="OK",
    max_size=0.1,
    adjusted_size=0.1,
    risk_breakdown={"daily_used": 0.5, "max_open": 1}
)
```

### 4. MT5Connector
Low-latency MT5 execution (<30ms).

**Methods**:
- `connect(broker_path, account, password)`
- `place_order(symbol, action, price, volume, sl, tp)`
- `close_order(ticket)`
- `get_open_positions()`

### 5. Orchestrator
Main trading loop coordinator.

**Cycle**: 30 seconds
1. Get chart screenshot
2. Run ChartAnalyst
3. Run StrategyExecutor
4. Run RiskManager
5. Execute if valid
6. Log results

## Configuration

```yaml
# Account
account: 480578231
broker_server: "Exness-MT5Trial11"  # Demo
symbols: [XAUUSD, EURUSD, GBPUSD, USDJPY, SPX500, FTSE100]

# Risk
risk_per_trade_pct: 1.0
max_positions: 3
daily_loss_limit_pct: 5.0
min_stop_loss_pips: 20

# Trading
entry_rules:
  mean_reversion_zscore: 2.0
  momentum_breakout: true
  volume_confirmation: true
typical_holding_minutes: 15-240

# Environment
broker_path: "C:\\Program Files\\MT5"
mt5_terminal_allowed: true
logging_path: "C:\\TG_TradinGo\\logs\\agents_trading.log"
```

## Deployment

### VPS (GameHosting)
```
IP: 144.91.76.28
Port: 2222 (SSH)
User: Administrator
```

**Setup Steps**:
1. Clone repo to `C:\StatArb\tradingo_system\`
2. Install Python 3.11+
3. Set environment variables (DPAPI encrypted)
4. Create task scheduler job:
   - Run `python agents_trading/main.py` at startup
   - Keep process alive via monitor script

**Deploy Script**:
```powershell
# C:\StatArb\scripts\deploy_agents.ps1
cd C:\StatArb\tradingo_system
python agents_trading/main.py --duration 1440 --symbols XAUUSD,EURUSD
```

## Monitoring

**Log File**: `C:\TG_TradinGo\logs\agents_trading.log`

**Session Report**: `agents_trading_session_*.json`
```json
{
    "timestamp": "2025-11-09T14:30:00Z",
    "duration_minutes": 120,
    "trades_executed": 5,
    "trades_won": 3,
    "trades_lost": 2,
    "daily_pnl": 45.50,
    "max_drawdown": 2.1,
    "account_balance": 1045.50,
    "signals_generated": 12
}
```

## Security

**Credential Storage**: DPAPI (Windows Data Protection API)
- `secrets_store.py`: Encrypt/decrypt credentials
- No hardcoding in code
- Runtime retrieval via `with_secrets.ps1`

**Never commit**:
- `tradingo_config.json`
- `accounts.json`
- `.env` files
- API keys in code

## Testing

### Unit Tests
```bash
pytest agents_trading/tests/ -v
```

### Backtest
```bash
python agents_trading/backtest/qlab_backtest_runner.py \
    --symbol XAUUSD \
    --timeframe H1 \
    --start-date 2025-01-01 \
    --end-date 2025-12-31
```

### Paper Trading (Demo)
```bash
python agents_trading/main.py \
    --duration 1440 \
    --symbols XAUUSD \
    --mt5-path "C:\\Program Files\\MT5"
```

## Troubleshooting

### MT5 Connection Failed
- Verify MT5 terminal running at `C:\Program Files\MT5\terminal.exe`
- Check broker server: "Exness-MT5Trial11" for demo
- Verify credentials correct

### No API Key
- Set: `export ANTHROPIC_API_KEY="sk-..."`
- Or: `with_secrets.ps1 -SetAPIKey`

### Trades Not Executing
- Check RiskManager validation logs
- Verify account has sufficient margin
- Check daily loss limit not hit (5% of balance)

### Chart Analysis Fails
- Need previous 50+ bars of data
- Check OHLC data integrity
- Verify chart screenshot generation

## Performance Baseline

Expected results (from backtest):
- Win rate: 55-65%
- Avg win/loss ratio: 1.5-2.0
- Sharpe ratio: 1.0-1.5
- Max drawdown: <15%

After 2-3 weeks paper trading → validate live readiness.

## Next Steps

1. ✅ STEP 1: Agents framework (this file)
2. ✅ STEP 2: Backtest framework
3. ✅ STEP 3: QLAB integration
4. 🟡 STEP 4: Unit testing + sample backtest
5. 🟡 STEP 5: Paper trading (demo Exness)
6. ⬜ STEP 6: Web monitoring dashboard
7. ⬜ STEP 7: Live account migration
