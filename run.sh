#!/bin/bash
# TRAPPIST — Claude Code launcher (cron every 15 min, 24/7)
# Usage: ./run.sh [command]
# Default: /trade

set -euo pipefail
cd "$(dirname "$0")"

[ -f .env ] && set -a && source .env && set +a
[ -f .env.local ] && set -a && source .env.local && set +a

COMMAND="${1:-/trade}"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
LOG="logs/run_${TIMESTAMP}.log"

mkdir -p logs

echo "=== TRAPPIST | $COMMAND | $(date -u) ===" | tee "$LOG"

claude --print \
  --allowedTools "Bash(python trading/*),Bash(source .venv/*),Bash(curl*),Read,Write,WebSearch,mcp__binance-futures__*,mcp__fear-greed__*,mcp__gloria-news__*,mcp__tradingview__*,mcp__cryptopanic__*,mcp__crypto-price__*" \
  "$COMMAND" \
  2>&1 | tee -a "$LOG"

echo "=== END ===" | tee -a "$LOG"
