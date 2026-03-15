#!/bin/bash
# TRAPPIST — Claude Code launcher
# Called by cron every 15 min (crypto 24/7)
# Usage: ./run.sh [command]
# Default: /run-trading-cycle

set -euo pipefail
cd "$(dirname "$0")"

# Load .env
[ -f .env ] && set -a && source .env && set +a

COMMAND="${1:-/run-trading-cycle}"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
LOG="logs/run_${TIMESTAMP}.log"

mkdir -p logs

echo "=== TRAPPIST | $COMMAND | $(date -u) ===" | tee "$LOG"

claude --print \
  --allowedTools "Bash(python trading/*),Bash(source .venv/*),Bash(curl*),Bash(jq*),Bash(cat*),Read,Write,mcp__binance-futures__*,mcp__fear-greed__*,mcp__cryptopanic__*,mcp__gloria-news__*,mcp__tradingview__*,mcp__crypto-price__*" \
  "$COMMAND" \
  2>&1 | tee -a "$LOG"

echo "=== END ===" | tee -a "$LOG"
