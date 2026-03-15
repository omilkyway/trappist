#!/bin/bash
set -euo pipefail

# ==================================================================
# reauth.sh — Re-authenticate Claude Code OAuth and push to Scaleway
#
# Run this on your local machine when you get a Discord alert:
#   "AUTH FAIL — Re-auth required"
#
# Prerequisites:
#   - Claude Code installed (npm install -g @anthropic-ai/claude-code)
#   - Scaleway CLI configured (scw init)
#   - .env.local with CREDENTIALS_SECRET_ID and CONFIG_SECRET_ID
#
# Usage:
#   bash deploy/scripts/reauth.sh
#   bash deploy/scripts/reauth.sh --test  # also trigger a test run
# ==================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
ENV_FILE="$PROJECT_DIR/.env.local"
SCW=~/.local/bin/scw

log() { echo "[reauth] $*"; }

# ─── Step 1: Re-authenticate ───
log "Step 1: Opening Claude Code auth flow..."
claude auth login
log "Auth flow completed."

# ─── Step 2: Verify auth works ───
log "Step 2: Verifying auth..."
RESULT=$(claude -p "Say OK" --output-format text 2>&1) || {
    log "ERROR: claude -p failed after re-auth. Check manually."
    exit 1
}
log "Verified: claude -p works ($RESULT)"

# ─── Step 3: Encode credentials ───
log "Step 3: Encoding credentials..."

CREDS_FILE="$HOME/.claude/.credentials.json"
CONFIG_FILE="$HOME/.claude.json"

if [[ ! -f "$CREDS_FILE" ]]; then
    log "ERROR: $CREDS_FILE not found after auth"
    exit 1
fi

CREDS_B64=$(base64 -w 0 < "$CREDS_FILE")
CONFIG_B64=""
if [[ -f "$CONFIG_FILE" ]]; then
    CONFIG_B64=$(base64 -w 0 < "$CONFIG_FILE")
fi

log "Encoded: credentials ($(echo -n "$CREDS_B64" | wc -c) chars)"

# ─── Step 4: Push to Scaleway Secret Manager ───
log "Step 4: Pushing to Scaleway Secret Manager..."

# Read secret IDs from .env.local
if [[ -f "$ENV_FILE" ]]; then
    CREDENTIALS_SECRET_ID=$(bash -c "source '$ENV_FILE' 2>/dev/null; echo \"\${CREDENTIALS_SECRET_ID:-}\"")
    CONFIG_SECRET_ID=$(bash -c "source '$ENV_FILE' 2>/dev/null; echo \"\${CONFIG_SECRET_ID:-}\"")
else
    log "WARNING: $ENV_FILE not found — reading secret IDs from env"
    CREDENTIALS_SECRET_ID="${CREDENTIALS_SECRET_ID:-}"
    CONFIG_SECRET_ID="${CONFIG_SECRET_ID:-}"
fi

if [[ -n "$CREDENTIALS_SECRET_ID" ]]; then
    $SCW secret version create \
        secret-id="$CREDENTIALS_SECRET_ID" \
        data="$CREDS_B64" \
        region=fr-par \
        -o human 2>&1 && log "Credentials secret updated" || log "ERROR: Failed to update credentials secret"
else
    log "WARNING: CREDENTIALS_SECRET_ID not set — skipping Secret Manager push"
    log "Encoded credentials (set manually):"
    log "  CLAUDE_CREDENTIALS_B64=$CREDS_B64"
fi

if [[ -n "$CONFIG_B64" ]] && [[ -n "${CONFIG_SECRET_ID:-}" ]]; then
    $SCW secret version create \
        secret-id="$CONFIG_SECRET_ID" \
        data="$CONFIG_B64" \
        region=fr-par \
        -o human 2>&1 && log "Config secret updated" || log "ERROR: Failed to update config secret"
fi

# ─── Step 5: Update .env.local ───
log "Step 5: Updating .env.local..."
if [[ -f "$ENV_FILE" ]]; then
    # Update or add CLAUDE_CREDENTIALS_B64
    if grep -q "^CLAUDE_CREDENTIALS_B64=" "$ENV_FILE"; then
        sed -i "s|^CLAUDE_CREDENTIALS_B64=.*|CLAUDE_CREDENTIALS_B64=$CREDS_B64|" "$ENV_FILE"
    else
        echo "CLAUDE_CREDENTIALS_B64=$CREDS_B64" >> "$ENV_FILE"
    fi
    # Update or add CLAUDE_CONFIG_B64
    if [[ -n "$CONFIG_B64" ]]; then
        if grep -q "^CLAUDE_CONFIG_B64=" "$ENV_FILE"; then
            sed -i "s|^CLAUDE_CONFIG_B64=.*|CLAUDE_CONFIG_B64=$CONFIG_B64|" "$ENV_FILE"
        else
            echo "CLAUDE_CONFIG_B64=$CONFIG_B64" >> "$ENV_FILE"
        fi
    fi
    log ".env.local updated with fresh credentials"
fi

# ─── Step 6 (optional): Trigger test run ───
if [[ "${1:-}" == "--test" ]]; then
    log "Step 6: Triggering test run..."
    source ~/.claude-trading-jobs 2>/dev/null || true
    if [[ -n "${OPEN_ID:-}" ]]; then
        $SCW jobs definition start "$OPEN_ID" region=fr-par -o json | jq '{job_runs: [.job_runs[] | {id, state}]}'
        log "Test run triggered. Check bot-status in a few minutes."
    else
        log "WARNING: No job IDs found in ~/.claude-trading-jobs — skip test run"
    fi
fi

log ""
log "Re-auth complete. Next CRON run will use the new tokens."
log "Time: ~2 minutes. Good job."
