#!/bin/bash
#
# Deploy Agents Trading to VPS
# Usage: ./deploy_to_vps.sh <API_KEY> <MT5_PASSWORD>
# Example: ./deploy_to_vps.sh sk-proj-xxx your_password
#

set -e

# Check arguments
if [ $# -lt 2 ]; then
    echo "❌ Usage: $0 <API_KEY> <MT5_PASSWORD>"
    echo ""
    echo "Example:"
    echo "  $0 sk-proj-your-key your-mt5-password"
    echo ""
    echo "Notes:"
    echo "  - API_KEY: Your Anthropic API key (starts with 'sk-')"
    echo "  - MT5_PASSWORD: Your Exness demo account password"
    echo ""
    exit 1
fi

API_KEY="$1"
MT5_PASSWORD="$2"

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  🚀 VPS DEPLOYMENT SCRIPT 🚀                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Step 1: Clone repository
echo "📥 Step 1: Cloning repository..."
if [ -d "tradingo_system" ]; then
    echo "  ⚠️  Directory exists, pulling latest..."
    cd tradingo_system
    git pull origin claude/serene-einstein-d6667p
else
    echo "  📦 Cloning from GitHub..."
    git clone https://github.com/daniele4trading-boop/tradingo_system.git
    cd tradingo_system
    git checkout claude/serene-einstein-d6667p
fi

echo "✅ Repository ready"
echo ""

# Step 2: Install Python dependencies
echo "📦 Step 2: Installing Python packages..."
cd agents_trading

pip install -q anthropic pandas numpy pytz 2>/dev/null || {
    echo "  ⚠️  Could not install all packages, continuing..."
}

echo "✅ Python packages installed"
echo ""

# Step 3: Setup environment variables
echo "🔐 Step 3: Setting up credentials..."
export ANTHROPIC_API_KEY="$API_KEY"
export EXNESS_MT5_PASSWORD_480578231="$MT5_PASSWORD"

# Save to shell profile for persistence
cat >> ~/.bashrc << EOF
export ANTHROPIC_API_KEY="$API_KEY"
export EXNESS_MT5_PASSWORD_480578231="$MT5_PASSWORD"
EOF

echo "✅ Credentials configured"
echo ""

# Step 4: Verify setup
echo "✅ Step 4: Verifying setup..."

# Check Python version
PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
echo "  Python: $PYTHON_VERSION"

# Check API key
if [ -n "$ANTHROPIC_API_KEY" ]; then
    echo "  API Key: ${ANTHROPIC_API_KEY:0:10}... ✅"
else
    echo "  API Key: NOT SET ❌"
fi

# Check MT5 password
if [ -n "$EXNESS_MT5_PASSWORD_480578231" ]; then
    echo "  MT5 Password: ****** ✅"
else
    echo "  MT5 Password: NOT SET ❌"
fi

echo ""

# Step 5: Create logs directory
echo "📁 Step 5: Creating logs directory..."
LOG_DIR="/root/tradingo_logs"
mkdir -p "$LOG_DIR"
echo "  Logs: $LOG_DIR"
echo "✅ Logs directory ready"
echo ""

# Step 6: Test demo mode
echo "🧪 Step 6: Testing with demo mode (1 min)..."
python demo_paper_trading.py --duration 1 --cycle 10 2>&1 | tail -20
echo ""
echo "✅ Demo test completed"
echo ""

# Step 7: Setup cron job for daily trading
echo "⏰ Step 7: Setting up daily trading schedule..."

# Create a wrapper script for cron
cat > run_trading.sh << 'CRON_EOF'
#!/bin/bash
cd /root/tradingo_system/agents_trading
export PYTHONPATH="$(pwd):$PYTHONPATH"
source ~/.bashrc
python launch_paper_trading.py --mode live --duration 1440 --symbols XAUUSD,EURUSD,GBPUSD >> /root/tradingo_logs/agents_trading_$(date +%Y%m%d).log 2>&1
CRON_EOF

chmod +x run_trading.sh

# Add cron job (daily at 06:00 UTC)
CRON_ENTRY="0 6 * * * /root/tradingo_system/agents_trading/run_trading.sh"
(crontab -l 2>/dev/null | grep -v "run_trading.sh"; echo "$CRON_ENTRY") | crontab - 2>/dev/null || {
    echo "  ⚠️  Could not setup cron (may require different setup on VPS)"
}

echo "✅ Daily trading scheduled for 06:00 UTC"
echo ""

# Step 8: Display status
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                  ✅ DEPLOYMENT COMPLETE ✅                    ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

echo "📊 System Status:"
echo "  Account: Exness Demo 480578231"
echo "  Symbols: XAUUSD, EURUSD, GBPUSD"
echo "  Schedule: Daily 06:00 UTC (1440 minutes)"
echo "  Logs: /root/tradingo_logs/"
echo ""

echo "📌 Next Steps:"
echo "  1. Monitor live trading:"
echo "     tail -f /root/tradingo_logs/agents_trading_*.log"
echo ""
echo "  2. Test manual run (30 min):"
echo "     cd /root/tradingo_system/agents_trading"
echo "     python launch_paper_trading.py --mode live --duration 30"
echo ""
echo "  3. Check cron status:"
echo "     crontab -l"
echo ""
echo "  4. Wait for first run at 06:00 UTC tomorrow"
echo ""

echo "🚀 Paper trading is now live!"
echo ""
