#!/bin/bash
#
# Start Live Paper Trading with QLAB Discovered Strategies
#
# Usage:
#   bash start_live_trading_qlab.sh [api_key] [mt5_password] [duration_min] [symbols]
#
# Examples:
#   bash start_live_trading_qlab.sh sk-proj-xxx your-password 1440 XAUUSD,EURUSD,GBPUSD
#   bash start_live_trading_qlab.sh sk-proj-xxx your-password        # Defaults: 1440 min, XAUUSD

set -e

# Configuration
API_KEY="${1:-}"
MT5_PASSWORD="${2:-}"
DURATION_MIN="${3:-1440}"
SYMBOLS="${4:-XAUUSD,EURUSD,GBPUSD}"
LOG_DIR="/root/tradingo_logs"
SESSION_LOG="${LOG_DIR}/paper_trading_session_$(date +%Y%m%d_%H%M%S).log"

# Validation
if [ -z "$API_KEY" ] || [ -z "$MT5_PASSWORD" ]; then
    echo "❌ Usage: bash start_live_trading_qlab.sh <api_key> <mt5_password> [duration_min] [symbols]"
    echo ""
    echo "Example:"
    echo "  bash start_live_trading_qlab.sh sk-proj-abc123 your-password 1440 XAUUSD,EURUSD"
    exit 1
fi

# Setup
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  🚀 LIVE PAPER TRADING - QLAB 🚀             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

echo "📋 Configuration:"
echo "  API Key: ${API_KEY:0:10}... ✅"
echo "  MT5 Password: ****** ✅"
echo "  Duration: ${DURATION_MIN} minutes"
echo "  Symbols: ${SYMBOLS}"
echo "  Log: ${SESSION_LOG}"
echo ""

# Create logs directory
mkdir -p "$LOG_DIR"
touch "$SESSION_LOG"

# Set environment
export ANTHROPIC_API_KEY="$API_KEY"
export EXNESS_MT5_PASSWORD_480578231="$MT5_PASSWORD"
export PYTHONPATH="/root/tradingo_system/agents_trading:$PYTHONPATH"

echo "🟢 Environment ready"
echo ""

# Launch trading
echo "🚀 Starting trading orchestrator..."
echo "   Strategies: QLAB_MeanReversion_V1, QLAB_SweepConfluence_V1, QLAB_VolumeAbsorption_V1"
echo "   Mode: LIVE (real MT5 connection)"
echo ""

cd /root/tradingo_system/agents_trading

python launch_paper_trading.py \
    --mode live \
    --duration "$DURATION_MIN" \
    --symbols "$SYMBOLS" \
    --cycle 30 \
    2>&1 | tee -a "$SESSION_LOG"

EXIT_CODE=$?

# Summary
echo ""
echo "════════════════════════════════════════════════════════════════"
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Session completed successfully"
else
    echo "⚠️  Session ended with exit code: $EXIT_CODE"
fi
echo ""
echo "📊 Session log: $SESSION_LOG"
echo "📈 Monitor P&L: grep 'Daily P&L\|Max Drawdown\|Win Rate' $SESSION_LOG"
echo ""

exit $EXIT_CODE
