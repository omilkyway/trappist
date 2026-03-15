#!/bin/bash
# TRAPPIST — Setup local
set -euo pipefail
cd "$(dirname "$0")"

echo "🪐 TRAPPIST Setup"
echo "=================="
echo ""

# --- Check Python3 ---
if ! command -v python3 &>/dev/null; then
  echo "❌ Python3 not installed."
  exit 1
fi
echo "✅ Python3 $(python3 --version | cut -d' ' -f2)"

# --- Create venv ---
if [ ! -d .venv ]; then
  echo "📦 Creating virtual environment..."
  python3 -m venv .venv
fi
echo "✅ Virtual environment (.venv)"

# --- Install dependencies ---
echo "📦 Installing dependencies..."
.venv/bin/pip install -q -e ".[dev]"
echo "✅ Dependencies installed (ccxt, pandas, numpy)"

# --- Check jq ---
if ! command -v jq &>/dev/null; then
  echo "⚠️  jq not found (optional). Install: sudo apt install jq"
else
  echo "✅ jq $(jq --version)"
fi

# --- Setup .env ---
if [ ! -f .env ]; then
  cp .env.sample .env
  echo ""
  echo "📝 .env created from .env.sample"
  echo "   → Edit: nano .env"
fi

# --- Setup keys ---
if grep -q "REPLACE\|your_testnet" keys.local.json 2>/dev/null; then
  echo ""
  echo "🔑 keys.local.json has placeholders."
  echo "   → Create testnet account: https://testnet.binancefuture.com"
  echo "   → Copy API Key + Secret → Edit: nano keys.local.json"
fi

# --- Create directories ---
mkdir -p logs reports

# --- Permissions ---
chmod +x run.sh deploy.sh 2>/dev/null || true

# --- Test CCXT ---
echo ""
echo "🧪 Testing CCXT connection..."
.venv/bin/python -c "
import ccxt
e = ccxt.binance({'options': {'defaultType': 'future'}})
e.set_sandbox_mode(True)
t = e.fetch_ticker('BTC/USDT:USDT')
print(f'✅ CCXT OK — BTC/USDT: \${t[\"last\"]:,.2f} (testnet)')
" 2>/dev/null || echo "⚠️  CCXT test failed. Check internet connection."

# --- Test executor ---
echo ""
echo "🧪 Testing executor..."
.venv/bin/python trading/executor.py account 2>/dev/null && echo "✅ Executor OK" || echo "⚠️  Executor test failed. Check keys.local.json"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. nano keys.local.json    (Binance Futures testnet API keys)"
echo "  2. nano .env               (Discord webhook, etc.)"
echo "  3. source .venv/bin/activate"
echo "  4. python trading/executor.py status"
echo "  5. /market-scan            (scan crypto market)"
echo "  6. /run-trading-cycle      (run a trading cycle)"
echo ""
echo "🪐 TRAPPIST ready."
