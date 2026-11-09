#!/bin/bash
# Setup environment variables for paper trading

echo "📋 Setting up environment for paper trading..."
echo ""

# Check if API key provided
if [ -z "$1" ]; then
    echo "⚠️  Usage: source setup_env.sh <API_KEY>"
    echo "Example: source setup_env.sh sk-proj-xxx"
    echo ""
    echo "Without API key, will use MOCK mode (no Claude analysis)"
    echo ""
else
    export ANTHROPIC_API_KEY="$1"
    echo "✅ Set ANTHROPIC_API_KEY=$($1 | cut -c1-10)..."
fi

# MT5 credentials (demo account)
# Replace with real password when ready for live
export EXNESS_MT5_PASSWORD_480578231="demo_password"
echo "✅ Set EXNESS_MT5_PASSWORD_480578231=***"

# Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
echo "✅ Set PYTHONPATH"

echo ""
echo "✅ Environment ready!"
echo ""
echo "Launch paper trading (demo mode):"
echo "  python launch_paper_trading.py --mode demo --duration 60"
echo ""
echo "Or with all options:"
echo "  python launch_paper_trading.py --mode demo --duration 1440 --symbols XAUUSD,EURUSD,GBPUSD --cycle 30"
echo ""
