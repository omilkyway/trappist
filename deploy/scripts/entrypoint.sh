#!/bin/bash
set -euo pipefail

RUN_TYPE="${1:-${RUN_TYPE:-cycle}}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
JOB_START_TS=$(date +%s)
S3_BUCKET="s3://trappist"
S3_ENABLED="${SCW_S3_ACCESS_KEY:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ==================================================================
# 0. AUTH — Setup token > OAuth credentials > API key (with fallback)
# ==================================================================
# Priority:
#   1. CLAUDE_CODE_OAUTH_TOKEN (setup-token, 1 year, no refresh needed)
#   2. CLAUDE_CREDENTIALS_B64 (legacy OAuth, 8h tokens, needs refresh)
#   3. ANTHROPIC_API_KEY (pay-per-use, no expiry)
CLAUDE_DIR="$HOME/.claude"
CREDENTIALS_FILE="$CLAUDE_DIR/.credentials.json"
CLAUDE_JSON="$HOME/.claude.json"
BACKUP_FILE="/tmp/credentials_backup.json"
AUTH_MODE="none"

# Helper: send Discord alert (auth or billing)
send_discord_alert() {
    local TITLE="${1:-Auth Alert}"
    local DESC="${2:-Unknown error}"
    local COLOR="${3:-16750848}"  # orange default
    if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
        curl -sf -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"${TITLE}\",\"description\":\"${DESC}\",\"color\":${COLOR},\"footer\":{\"text\":\"trappist v3.0\"}}]}" \
            "$DISCORD_WEBHOOK_URL" || true
    fi
}

send_auth_alert() {
    local REASON="${1:-OAuth token needs refresh}"
    send_discord_alert \
        "Auth Failed — Action Required" \
        "**Raison:** ${REASON}\\n\\nPour fix :\\n\\n**1.** Sur ta machine :\\n\\\`\\\`\\\`\\nclaude setup-token\\n\\\`\\\`\\\`\\n**2.** Puis update le job Scaleway :\\n\\\`\\\`\\\`\\nbash deploy/scripts/reauth.sh\\n\\\`\\\`\\\`\\n\\n**Fallback:** Le job essaie ANTHROPIC_API_KEY si disponible." \
        16750848
}

send_billing_alert() {
    local AUTH="${1:-api_key}"
    send_discord_alert \
        "BILLING ERROR — Bot stopped" \
        "**Credit balance is too low** (mode: ${AUTH})\\n\\nLe bot ne peut plus trader.\\n\\n**Pour fix :**\\n- Recharger les credits sur [console.anthropic.com](https://console.anthropic.com)\\n- Ou passer en setup-token (subscription Max) :\\n\\\`\\\`\\\`\\nclaude setup-token\\n\\\`\\\`\\\`" \
        15158332
}

# Ensure onboarding is skipped
setup_claude_json() {
    mkdir -p "$CLAUDE_DIR"
    if [[ -n "${CLAUDE_CONFIG_B64:-}" ]]; then
        echo "$CLAUDE_CONFIG_B64" | base64 -d > "$CLAUDE_JSON"
    elif [[ ! -f "$CLAUDE_JSON" ]]; then
        cat > "$CLAUDE_JSON" << 'MINCONFIG'
{
  "completedOnboarding": true,
  "hasCompletedFirstRun": true
}
MINCONFIG
    fi
}

# --- Priority 1: Setup token (1 year, no refresh, subscription billing) ---
if [[ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
    AUTH_MODE="setup_token"
    log "AUTH: Using setup-token (1-year, subscription billing)"
    setup_claude_json

# --- Priority 2: Legacy OAuth credentials (8h tokens, refresh needed) ---
elif [[ -n "${CLAUDE_CREDENTIALS_B64:-}" ]]; then
    AUTH_MODE="oauth"
    log "AUTH: Injecting OAuth credentials..."
    setup_claude_json

    echo "$CLAUDE_CREDENTIALS_B64" | base64 -d > "$CREDENTIALS_FILE"
    chmod 600 "$CREDENTIALS_FILE"

    # Backup credentials before execution (issue #29896 — wipe protection)
    cp "$CREDENTIALS_FILE" "$BACKUP_FILE"

    # Validate: check refreshToken exists
    REFRESH_TOKEN=$(jq -r '.claudeAiOauth.refreshToken // ""' "$CREDENTIALS_FILE" 2>/dev/null || echo "")
    if [[ -z "$REFRESH_TOKEN" ]]; then
        log "AUTH: No refreshToken — falling back"
        AUTH_MODE="api_key"
        send_auth_alert "refreshToken manquant dans les credentials"
    else
        EXPIRES_AT=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CREDENTIALS_FILE" 2>/dev/null || echo "0")
        NOW_MS=$(($(date +%s) * 1000))
        if [[ "$EXPIRES_AT" -gt "$NOW_MS" ]]; then
            log "AUTH: OAuth ready (token valid $(( (EXPIRES_AT - NOW_MS) / 1000 ))s)"
        else
            log "AUTH: Access token expired — relying on auto-refresh"
        fi
    fi

# --- Priority 3: API key (pay-per-use) ---
elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    AUTH_MODE="api_key"
    log "AUTH: Using API key (pay-per-use billing)"

# --- No auth at all ---
else
    log "AUTH: FATAL — No auth configured"
    send_discord_alert \
        "FATAL — No auth configured" \
        "Neither setup-token, OAuth, nor API key found. Job aborted." \
        15158332
    exit 1
fi

log "AUTH: Mode = $AUTH_MODE"

# ==================================================================
# 1. SYNC STATE FROM S3
# ==================================================================
mkdir -p /app/reports /app/logs
S3_EP="--endpoint-url https://s3.fr-par.scw.cloud"
export AWS_ENDPOINT_URL="https://s3.fr-par.scw.cloud"
if [[ -n "$S3_ENABLED" ]]; then
    export AWS_ACCESS_KEY_ID="${SCW_S3_ACCESS_KEY}"
    export AWS_SECRET_ACCESS_KEY="${SCW_S3_SECRET_KEY}"
    export AWS_DEFAULT_REGION="fr-par"

    # Dedup lock: skip if another run of same type started < 5 min ago
    LOCK_KEY="${S3_BUCKET}/.lock_${RUN_TYPE}"
    LOCK_CONTENT=$(aws s3 cp $S3_EP "$LOCK_KEY" - 2>/dev/null || echo "")
    if [[ -n "$LOCK_CONTENT" ]]; then
        LOCK_TS=$(echo "$LOCK_CONTENT" | head -1)
        NOW_TS=$(date +%s)
        LOCK_AGE=$(( NOW_TS - LOCK_TS ))
        if [[ $LOCK_AGE -lt 3600 ]]; then
            log "DEDUP: Another $RUN_TYPE run started ${LOCK_AGE}s ago (< 3600s). Skipping."
            exit 0
        fi
        log "DEDUP: Stale lock found (${LOCK_AGE}s old). Proceeding."
    fi
    echo "$(date +%s)" | aws s3 cp $S3_EP - "$LOCK_KEY" 2>/dev/null || true

    log "Downloading state from S3..."
    aws s3 cp $S3_EP "$S3_BUCKET/progress.md" /app/progress.md 2>/dev/null || echo "# Trading Progress" > /app/progress.md
    aws s3 cp $S3_EP "$S3_BUCKET/pending_protections.json" /app/pending_protections.json 2>/dev/null || echo "[]" > /app/pending_protections.json
    aws s3 cp $S3_EP "$S3_BUCKET/state.json" /app/state.json 2>/dev/null || echo '{"initial_balance":0,"killed":false,"trades":[]}' > /app/state.json
    aws s3 cp $S3_EP "$S3_BUCKET/scan_history.json" /app/scan_history.json 2>/dev/null || echo '[]' > /app/scan_history.json
    aws s3 sync $S3_EP "$S3_BUCKET/reports/" /app/reports/ 2>/dev/null || true
else
    log "S3 not configured — using ephemeral storage"
    [[ -f /app/progress.md ]] || echo "# Trading Progress" > /app/progress.md
    [[ -f /app/pending_protections.json ]] || echo "[]" > /app/pending_protections.json
fi

# ==================================================================
# 2. RECONCILE (prevents phantom position blocking)
# ==================================================================
if [[ "$RUN_TYPE" == "cycle" ]]; then
    log "Reconciling: fix protection + trail stops + sync state..."
    /app/.venv/bin/python /app/trading/executor.py protect --trail 2>&1 || log "WARNING: Reconciliation failed (non-fatal)"
fi

# ==================================================================
# 3. ROUTE TO CORRECT RUN TYPE
# ==================================================================
case "$RUN_TYPE" in
  cycle)
    log "=== TRADING CYCLE — Full crypto pipeline (24/7) ==="
    PROMPT="/trade"
    MAX_TURNS=100
    ;;
  review)
    log "=== PORTFOLIO REVIEW — Position management ==="
    PROMPT="Run portfolio review: source .venv/bin/activate && python trading/executor.py protect --trail --max-days 10 && python trading/executor.py status"
    MAX_TURNS=50
    ;;
  protect)
    log "=== PROTECTION CHECK ==="
    ;;
  *)
    log "Unknown run type: $RUN_TYPE (expected: cycle, review, protect)"
    exit 1
    ;;
esac

# ==================================================================
# 4. EXECUTE (with automatic OAuth→API key fallback)
# ==================================================================
cd /app

run_claude() {
    # Run claude -p with current auth mode. Returns exit code.
    local PROMPT="$1"
    local MAX_TURNS="$2"
    local LOG_FILE="$3"

    # CRITICAL: Claude CLI prioritizes ANTHROPIC_API_KEY over CLAUDE_CODE_OAUTH_TOKEN.
    # When using setup-token or oauth, hide the API key so Claude uses the right auth.
    local SAVED_API_KEY="${ANTHROPIC_API_KEY:-}"
    if [[ "$AUTH_MODE" == "setup_token" || "$AUTH_MODE" == "oauth" ]]; then
        unset ANTHROPIC_API_KEY
        log "AUTH: Hidden ANTHROPIC_API_KEY — Claude will use $AUTH_MODE"
    fi

    # Build plugin-dir flags
    local PLUGIN_ARGS=""
    for pdir in /app/plugins/*/; do
        if [[ -d "$pdir/.claude-plugin" ]]; then
            PLUGIN_ARGS="$PLUGIN_ARGS --plugin-dir $pdir"
        fi
    done

    set +e
    claude -p "$PROMPT" \
        --dangerously-skip-permissions \
        --model 'claude-opus-4-6[1m]' \
        --effort high \
        --max-turns "$MAX_TURNS" \
        --output-format stream-json \
        --verbose \
        $PLUGIN_ARGS \
        2>&1 | tee "$LOG_FILE"
    local CODE=${PIPESTATUS[0]}
    set -e

    # Restore API key for potential fallback retry
    if [[ -n "$SAVED_API_KEY" ]]; then
        export ANTHROPIC_API_KEY="$SAVED_API_KEY"
    fi

    return $CODE
}

is_auth_error() {
    local LOG_FILE="$1"
    [[ -f "$LOG_FILE" ]] && grep -qi "authentication_error\|401\|OAuth token has expired\|login required\|unauthorized\|token expired\|EAUTH\|invalid_grant\|refresh_token" "$LOG_FILE" 2>/dev/null
}

is_billing_error() {
    local LOG_FILE="$1"
    [[ -f "$LOG_FILE" ]] && grep -qi "billing_error\|Credit balance is too low\|insufficient_credits\|quota_exceeded\|rate_limit_error" "$LOG_FILE" 2>/dev/null
}

if [[ "$RUN_TYPE" == "protect" ]]; then
    # Lightweight — no LLM needed.
    set +e
    log "Phase 1: Check + fix protection on all positions..."
    /app/.venv/bin/python /app/trading/executor.py protect \
        2>&1 | tee "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    EXIT_CODE=${PIPESTATUS[0]}

    log "Phase 2: Trail profitable stops..."
    /app/.venv/bin/python /app/trading/executor.py protect --trail \
        2>&1 | tee -a "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    set -e
else
    LOG_FILE="/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    log "Starting Claude Code ($RUN_TYPE, auth=$AUTH_MODE, max_turns=$MAX_TURNS)..."

    run_claude "$PROMPT" "$MAX_TURNS" "$LOG_FILE"
    EXIT_CODE=$?

    # ── Billing error? Alert immediately, no retry ──
    if [[ $EXIT_CODE -ne 0 ]] && is_billing_error "$LOG_FILE"; then
        log "BILLING: Credit balance too low (auth=$AUTH_MODE)"
        send_billing_alert "$AUTH_MODE"
        # Don't retry — billing won't fix itself

    # ── Auth error? Try fallback to API key ──
    elif [[ $EXIT_CODE -ne 0 ]] && [[ "$AUTH_MODE" == "oauth" || "$AUTH_MODE" == "setup_token" ]] && [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        if is_auth_error "$LOG_FILE"; then
            log "AUTH: $AUTH_MODE failed — switching to API key and retrying..."
            AUTH_MODE="api_key_fallback"

            # Clear OAuth env so claude uses ANTHROPIC_API_KEY
            rm -f "$CREDENTIALS_FILE" 2>/dev/null || true
            unset CLAUDE_CODE_OAUTH_TOKEN 2>/dev/null || true

            send_auth_alert "$AUTH_MODE 401/expired — retry avec API key"

            RETRY_LOG="/app/logs/${RUN_TYPE}-${TIMESTAMP}-retry.log"
            log "AUTH: Retrying with ANTHROPIC_API_KEY..."
            run_claude "$PROMPT" "$MAX_TURNS" "$RETRY_LOG"
            EXIT_CODE=$?

            cat "$RETRY_LOG" >> "$LOG_FILE" 2>/dev/null || true
            log "AUTH: Retry completed (exit=$EXIT_CODE)"

            # Check if API key also has billing issues
            if [[ $EXIT_CODE -ne 0 ]] && is_billing_error "$RETRY_LOG"; then
                log "BILLING: API key also out of credits"
                send_billing_alert "api_key_fallback"
            fi
        fi

    # ── Auth error but no API key fallback available ──
    elif [[ $EXIT_CODE -ne 0 ]] && is_auth_error "$LOG_FILE"; then
        send_auth_alert "$AUTH_MODE failed — pas de fallback API key disponible"
    fi
fi

log "Session completed (exit: $EXIT_CODE, auth: $AUTH_MODE)"

# ==================================================================
# 5. OAUTH: PERSIST REFRESHED TOKENS (only for legacy oauth mode)
# ==================================================================
# setup_token doesn't need persistence — it's static for 1 year.
if [[ "$AUTH_MODE" == "oauth" ]]; then
    if [[ -f "$CREDENTIALS_FILE" ]] && [[ -s "$CREDENTIALS_FILE" ]]; then
        NEW_EXPIRES=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CREDENTIALS_FILE" 2>/dev/null || echo "0")
        OLD_EXPIRES=$(jq -r '.claudeAiOauth.expiresAt // 0' "$BACKUP_FILE" 2>/dev/null || echo "0")

        if [[ "$NEW_EXPIRES" != "$OLD_EXPIRES" ]]; then
            log "AUTH: Tokens refreshed — persisting to Secret Manager"
            NEW_CREDS_B64=$(base64 -w 0 < "$CREDENTIALS_FILE")

            if [[ -n "${SCW_SECRET_KEY:-}" ]] && [[ -n "${CREDENTIALS_SECRET_ID:-}" ]]; then
                HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
                    "https://api.scaleway.com/secret-manager/v1beta1/regions/${SCW_DEFAULT_REGION:-fr-par}/secrets/${CREDENTIALS_SECRET_ID}/versions" \
                    -H "X-Auth-Token: $SCW_SECRET_KEY" \
                    -H "Content-Type: application/json" \
                    -d "{\"data\": \"$NEW_CREDS_B64\"}")

                if [[ "$HTTP_CODE" == "200" ]] || [[ "$HTTP_CODE" == "201" ]]; then
                    log "AUTH: Secret Manager updated (HTTP $HTTP_CODE)"
                else
                    log "AUTH: WARNING — Secret Manager update failed (HTTP $HTTP_CODE)"
                fi
            fi
        else
            log "AUTH: Tokens unchanged"
        fi
    elif [[ -f "$BACKUP_FILE" ]] && [[ ! -s "$CREDENTIALS_FILE" ]]; then
        log "AUTH: Credentials wiped — restoring backup"
        cp "$BACKUP_FILE" "$CREDENTIALS_FILE"
    fi
fi

# ==================================================================
# 6. EXPORT SESSION METRICS
# ==================================================================
METRICS_FILE="/app/logs/session_metrics_${RUN_TYPE}_${TIMESTAMP}.json"
LOG_FILE="/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"

TOTAL_TOKENS=0
TOTAL_COST=0
TOTAL_TURNS=0
SESSION_MODEL="unknown"
SESSION_DURATION=0
if [[ -f "$LOG_FILE" ]]; then
    TOTAL_TURNS=$(grep -c '"type":"assistant"' "$LOG_FILE" 2>/dev/null || echo "0")
    # Extract model from first assistant message, tokens+cost from result line
    read SESSION_MODEL TOTAL_TOKENS TOTAL_COST <<< $(python3 -c "
import json, sys
model = 'unknown'
tokens = 0
cost = 0
with open('$LOG_FILE') as f:
    for line in f:
        try:
            d = json.loads(line.strip())
            if d.get('type') == 'assistant' and model == 'unknown':
                m = d.get('message', {}).get('model', '')
                if m: model = m
            if d.get('type') == 'result':
                cost = d.get('total_cost_usd', 0)
                u = d.get('usage', {})
                tokens = u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0) + u.get('output_tokens', 0)
                # Get context window from modelUsage
                mu = d.get('modelUsage', {})
                for mname, mdata in mu.items():
                    if mdata.get('contextWindow', 0) >= 1000000:
                        model = mname + '[1m]'
                        break
                    elif model == 'unknown' and mname:
                        model = mname
        except: pass
print(f'{model} {tokens} {cost:.4f}')
" 2>/dev/null || echo "unknown 0 0")
    # Duration in seconds since job started
    if [[ -n "${JOB_START_TS:-}" ]]; then
        SESSION_DURATION=$(( $(date +%s) - JOB_START_TS ))
    else
        SESSION_DURATION=$(( $(date +%s) - $(date -d "${TIMESTAMP:0:8} ${TIMESTAMP:9:2}:${TIMESTAMP:11:2}:${TIMESTAMP:13:2}" +%s 2>/dev/null || echo "$(date +%s)") ))
    fi
fi

python3 -c "
import json, os
from datetime import datetime
metrics = {
    'run_type': '$RUN_TYPE',
    'timestamp': '$TIMESTAMP',
    'exit_code': $EXIT_CODE,
    'auth_mode': '$AUTH_MODE',
    'total_turns': int('$TOTAL_TURNS' or 0),
    'total_tokens': int('$TOTAL_TOKENS' or 0),
    'total_cost_usd': float('$TOTAL_COST' or 0),
    'log_file': '$LOG_FILE',
    'log_size_bytes': os.path.getsize('$LOG_FILE') if os.path.exists('$LOG_FILE') else 0,
    'reports_generated': len([f for f in os.listdir('/app/reports') if f.endswith('.md')]) if os.path.isdir('/app/reports') else 0,
}
with open('$METRICS_FILE', 'w') as f:
    json.dump(metrics, f, indent=2)
print(f'Session metrics: {json.dumps(metrics)}')
" 2>/dev/null || log "WARNING: Failed to generate session metrics"

# ==================================================================
# 7. SYNC STATE BACK TO S3
# ==================================================================
if [[ -n "$S3_ENABLED" ]]; then
    log "Uploading state to S3..."
    aws s3 cp $S3_EP /app/progress.md "$S3_BUCKET/progress.md" || log "WARNING: S3 upload progress.md failed"
    aws s3 cp $S3_EP /app/pending_protections.json "$S3_BUCKET/pending_protections.json" || log "WARNING: S3 upload pending_protections.json failed"
    aws s3 cp $S3_EP /app/state.json "$S3_BUCKET/state.json" || log "WARNING: S3 upload state.json failed"
    aws s3 cp $S3_EP /app/scan_history.json "$S3_BUCKET/scan_history.json" || log "WARNING: S3 upload scan_history.json failed"
    aws s3 sync $S3_EP /app/reports/ "$S3_BUCKET/reports/" || log "WARNING: S3 sync reports failed"
    aws s3 sync $S3_EP /app/logs/ "$S3_BUCKET/logs/" --exclude "*.log" --include "${RUN_TYPE}-${TIMESTAMP}.log" --include "session_metrics_*.json" || log "WARNING: S3 sync logs failed"
    aws s3 rm $S3_EP "${S3_BUCKET}/.lock_${RUN_TYPE}" 2>/dev/null || true
else
    log "S3 not configured — state stays ephemeral"
    log "Reports generated:"
    ls -la /app/reports/ 2>/dev/null || true
fi

# ==================================================================
# 8. DISCORD NOTIFICATION
# ==================================================================
if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
    log "Sending Discord notification..."
    /app/.venv/bin/python /app/trading/discord.py \
        --run-type "$RUN_TYPE" \
        --exit-code "$EXIT_CODE" \
        --cost "$TOTAL_COST" \
        --turns "$TOTAL_TURNS" \
        --model "$SESSION_MODEL" \
        --tokens "$TOTAL_TOKENS" \
        --duration "$SESSION_DURATION" \
        2>&1 || log "Discord notification failed (non-fatal)"
fi

# Exit 0 if job ran (even with non-zero exit) — S3 sync is what matters
if [[ $EXIT_CODE -ne 0 ]]; then
    log "WARNING: Exited with $EXIT_CODE (non-fatal, S3 synced)"
fi
exit 0
