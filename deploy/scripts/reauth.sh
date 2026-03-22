#!/bin/bash
set -euo pipefail

# ==================================================================
# reauth.sh — Re-authenticate Claude Code and push to Scaleway jobs
#
# Two modes:
#   bash deploy/scripts/reauth.sh              # setup-token (recommended)
#   bash deploy/scripts/reauth.sh --legacy     # legacy OAuth (8h tokens)
#   bash deploy/scripts/reauth.sh --test       # also trigger a test run
# ==================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
ENV_FILE="$PROJECT_DIR/.env.local"
SCW=~/.local/bin/scw

log() { echo "[reauth] $*"; }

MODE="setup-token"
TRIGGER_TEST=false
for arg in "$@"; do
    case "$arg" in
        --legacy) MODE="legacy" ;;
        --test)   TRIGGER_TEST=true ;;
    esac
done

# Read job IDs
source ~/.trappist-jobs 2>/dev/null || true
CYCLE_ID="${CYCLE_ID:-20c225c4-05f1-4e79-8b0b-a6ed052acb31}"
PROTECT_ID="${PROTECT_ID:-e00d3f69-1f47-4cc6-b497-3f8d228bee8f}"

if [[ "$MODE" == "setup-token" ]]; then
    # ─── SETUP TOKEN MODE (recommended) ───
    log "=== Setup Token Mode (1 year, no refresh needed) ==="

    log "Step 1: Generating setup token..."
    log "Run this command and paste the token:"
    echo ""
    echo "  claude setup-token"
    echo ""
    read -rp "Paste the token (sk-ant-oat01-...): " SETUP_TOKEN

    if [[ ! "$SETUP_TOKEN" =~ ^sk-ant- ]]; then
        log "ERROR: Invalid token format. Expected sk-ant-oat01-..."
        exit 1
    fi

    log "Step 2: Verifying token..."
    RESULT=$(CLAUDE_CODE_OAUTH_TOKEN="$SETUP_TOKEN" claude -p "Say OK" --output-format text 2>&1) || {
        log "ERROR: Token verification failed: $RESULT"
        exit 1
    }
    log "Verified: $RESULT"

    log "Step 3: Updating .env.local..."
    if [[ -f "$ENV_FILE" ]]; then
        if grep -q "^CLAUDE_CODE_OAUTH_TOKEN=" "$ENV_FILE"; then
            sed -i "s|^CLAUDE_CODE_OAUTH_TOKEN=.*|CLAUDE_CODE_OAUTH_TOKEN=$SETUP_TOKEN|" "$ENV_FILE"
        else
            echo "CLAUDE_CODE_OAUTH_TOKEN=$SETUP_TOKEN" >> "$ENV_FILE"
        fi
    else
        echo "CLAUDE_CODE_OAUTH_TOKEN=$SETUP_TOKEN" >> "$ENV_FILE"
    fi
    log ".env.local updated"

    log "Step 4: Pushing token to Scaleway jobs..."
    for JOB_ID in "$CYCLE_ID" "$PROTECT_ID"; do
        JOB_NAME=$($SCW jobs definition get "$JOB_ID" region=fr-par -o json 2>/dev/null | jq -r '.name // "unknown"')
        $SCW jobs definition update "$JOB_ID" \
            region=fr-par \
            environment-variables.CLAUDE_CODE_OAUTH_TOKEN="$SETUP_TOKEN" \
            -o human 2>&1 && log "Updated job $JOB_NAME ($JOB_ID)" || log "WARNING: Failed to update $JOB_ID"
    done

else
    # ─── LEGACY OAUTH MODE ───
    log "=== Legacy OAuth Mode (8h tokens, needs refresh) ==="

    log "Step 1: Opening Claude Code auth flow..."
    claude auth login
    log "Auth flow completed."

    log "Step 2: Verifying auth..."
    RESULT=$(claude -p "Say OK" --output-format text 2>&1) || {
        log "ERROR: claude -p failed after re-auth."
        exit 1
    }
    log "Verified: $RESULT"

    log "Step 3: Encoding credentials..."
    CREDS_FILE="$HOME/.claude/.credentials.json"
    CONFIG_FILE="$HOME/.claude.json"

    if [[ ! -f "$CREDS_FILE" ]]; then
        log "ERROR: $CREDS_FILE not found after auth"
        exit 1
    fi

    CREDS_B64=$(base64 -w 0 < "$CREDS_FILE")
    CONFIG_B64=""
    [[ -f "$CONFIG_FILE" ]] && CONFIG_B64=$(base64 -w 0 < "$CONFIG_FILE")
    log "Encoded: credentials ($(echo -n "$CREDS_B64" | wc -c) chars)"

    log "Step 4: Pushing to Scaleway Secret Manager..."
    if [[ -f "$ENV_FILE" ]]; then
        CREDENTIALS_SECRET_ID=$(bash -c "source '$ENV_FILE' 2>/dev/null; echo \"\${CREDENTIALS_SECRET_ID:-}\"")
    fi

    if [[ -n "${CREDENTIALS_SECRET_ID:-}" ]]; then
        $SCW secret version create \
            secret-id="$CREDENTIALS_SECRET_ID" \
            data="$CREDS_B64" \
            region=fr-par \
            -o human 2>&1 && log "Secret Manager updated" || log "ERROR: Failed to update secret"
    fi

    log "Step 5: Updating .env.local..."
    if [[ -f "$ENV_FILE" ]]; then
        if grep -q "^CLAUDE_CREDENTIALS_B64=" "$ENV_FILE"; then
            sed -i "s|^CLAUDE_CREDENTIALS_B64=.*|CLAUDE_CREDENTIALS_B64=$CREDS_B64|" "$ENV_FILE"
        else
            echo "CLAUDE_CREDENTIALS_B64=$CREDS_B64" >> "$ENV_FILE"
        fi
        if [[ -n "$CONFIG_B64" ]]; then
            if grep -q "^CLAUDE_CONFIG_B64=" "$ENV_FILE"; then
                sed -i "s|^CLAUDE_CONFIG_B64=.*|CLAUDE_CONFIG_B64=$CONFIG_B64|" "$ENV_FILE"
            else
                echo "CLAUDE_CONFIG_B64=$CONFIG_B64" >> "$ENV_FILE"
            fi
        fi
    fi
fi

# ─── Optional test run ───
if [[ "$TRIGGER_TEST" == true ]]; then
    log "Triggering test run..."
    $SCW jobs definition start "$CYCLE_ID" region=fr-par -o json | jq '{job_runs: [.job_runs[] | {id, state}]}'
    log "Test run triggered. Check Discord in ~5 minutes."
fi

log ""
log "Re-auth complete ($MODE). Next CRON run will use the new auth."
