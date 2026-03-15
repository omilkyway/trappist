#!/bin/bash
set -euo pipefail

RUN_TYPE="${1:-${RUN_TYPE:-open}}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
S3_BUCKET="s3://claude-trading"
S3_ENABLED="${SCW_S3_ACCESS_KEY:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# ==================================================================
# 0. AUTH — OAuth primary, API key automatic fallback
# ==================================================================
# Strategy: Try OAuth first. If it fails at any point, fall back to
# ANTHROPIC_API_KEY transparently and alert on Discord with auth link.
CLAUDE_DIR="$HOME/.claude"
CREDENTIALS_FILE="$CLAUDE_DIR/.credentials.json"
CLAUDE_JSON="$HOME/.claude.json"
BACKUP_FILE="/tmp/credentials_backup.json"
AUTH_MODE="api_key"  # default — always safe

# Helper: send Discord auth alert with re-login link
send_auth_alert() {
    local REASON="${1:-OAuth token needs refresh}"
    if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
        curl -sf -H "Content-Type: application/json" \
            -d "{\"embeds\":[{\"title\":\"OAuth Auth — Fallback API Key\",\"description\":\"**Raison:** ${REASON}\\n\\nLe job continue avec la cl\u00e9 API. Pour r\u00e9activer OAuth :\\n\\n**1.** Sur ta machine :\\n\\\`\\\`\\\`\\nclaude auth login\\n\\\`\\\`\\\`\\n**2.** Puis push les creds :\\n\\\`\\\`\\\`\\nbash deploy/scripts/reauth.sh\\n\\\`\\\`\\\`\\n\\n**Lien direct auth :** [claude.ai/settings](https://claude.ai/settings)\",\"color\":16750848,\"footer\":{\"text\":\"claude-trading v2.2 \u2022 job continue en API key\"}}]}" \
            "$DISCORD_WEBHOOK_URL" || true
    fi
}

if [[ -n "${CLAUDE_CREDENTIALS_B64:-}" ]]; then
    AUTH_MODE="oauth"
    log "AUTH: Injecting OAuth credentials..."

    mkdir -p "$CLAUDE_DIR"

    # Inject .credentials.json
    echo "$CLAUDE_CREDENTIALS_B64" | base64 -d > "$CREDENTIALS_FILE"
    chmod 600 "$CREDENTIALS_FILE"

    # Inject .claude.json (skip onboarding)
    if [[ -n "${CLAUDE_CONFIG_B64:-}" ]]; then
        echo "$CLAUDE_CONFIG_B64" | base64 -d > "$CLAUDE_JSON"
    else
        cat > "$CLAUDE_JSON" << 'MINCONFIG'
{
  "completedOnboarding": true,
  "hasCompletedFirstRun": true
}
MINCONFIG
    fi

    # Backup credentials before execution (issue #29896 — wipe protection)
    cp "$CREDENTIALS_FILE" "$BACKUP_FILE"

    # Validate: check refreshToken exists
    REFRESH_TOKEN=$(jq -r '.claudeAiOauth.refreshToken // ""' "$CREDENTIALS_FILE" 2>/dev/null || echo "")
    if [[ -z "$REFRESH_TOKEN" ]]; then
        log "AUTH: No refreshToken — OAuth won't work, falling back to API key"
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

elif [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    AUTH_MODE="api_key"
    log "AUTH: Using API key (no OAuth credentials configured)"
else
    log "AUTH: FATAL — No auth at all"
    if [[ -n "${DISCORD_WEBHOOK_URL:-}" ]]; then
        curl -sf -H "Content-Type: application/json" \
            -d '{"embeds":[{"title":"FATAL — No auth configured","description":"Neither OAuth nor API key found. Job aborted.","color":15158332}]}' \
            "$DISCORD_WEBHOOK_URL" || true
    fi
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
        if [[ $LOCK_AGE -lt 300 ]]; then
            log "DEDUP: Another $RUN_TYPE run started ${LOCK_AGE}s ago (< 300s). Skipping."
            exit 0
        fi
        log "DEDUP: Stale lock found (${LOCK_AGE}s old). Proceeding."
    fi
    echo "$(date +%s)" | aws s3 cp $S3_EP - "$LOCK_KEY" 2>/dev/null || true

    log "Downloading state from S3..."
    aws s3 cp $S3_EP "$S3_BUCKET/progress.md" /app/progress.md 2>/dev/null || echo "# Trading Progress" > /app/progress.md
    aws s3 cp $S3_EP "$S3_BUCKET/pending_protections.json" /app/pending_protections.json 2>/dev/null || echo "[]" > /app/pending_protections.json
    aws s3 sync $S3_EP "$S3_BUCKET/reports/" /app/reports/ 2>/dev/null || true
else
    log "S3 not configured — using ephemeral storage"
    [[ -f /app/progress.md ]] || echo "# Trading Progress" > /app/progress.md
    [[ -f /app/pending_protections.json ]] || echo "[]" > /app/pending_protections.json
fi

# ==================================================================
# 2. RECONCILE (prevents phantom position blocking)
# ==================================================================
if [[ "$RUN_TYPE" == "open" ]]; then
    log "Reconciling progress.md with live Alpaca positions..."
    /app/.venv/bin/python /app/trading/executor.py reconcile 2>&1 || log "WARNING: Reconciliation failed (non-fatal)"
fi

# ==================================================================
# 3. ROUTE TO CORRECT RUN TYPE
# ==================================================================
case "$RUN_TYPE" in
  open)
    log "=== MARKET OPEN — Full trading pipeline ==="
    PROMPT="/make-profitables-trades"
    MAX_TURNS=35
    ;;
  close)
    log "=== MARKET CLOSE — Position review & management ==="
    PROMPT="Execute end-of-day position review:
1. python trading/executor.py trail-stops — auto-tighten SL for profitable positions
2. python trading/executor.py account — get current equity, daily P&L
3. python trading/executor.py positions — all open positions with P&L
4. python trading/executor.py orders — check pending orders
5. python trading/executor.py check-protection — verify all positions have SL/TP
6. For each position:
   - Calculate days held (time stop: >10 days = close)
   - Check distance to stop-loss and take-profit
   - If daily drawdown > -1.5%: tighten stops to -3%
7. Close any positions that hit time stop (>10 trading days)
8. Generate EOD report in reports/eod-review-${TIMESTAMP}.md
9. Update progress.md with:
   - Current equity and daily P&L
   - All open positions with entry, current, SL, TP, days held
   - Risk flags (circuit breaker status, VIX level)
   - Watchlist for next session
   - Cumulative performance stats"
    MAX_TURNS=25
    ;;
  protect)
    log "=== POST-OPEN — OCO Protection ==="
    ;;
  *)
    log "Unknown run type: $RUN_TYPE (expected: open, close, protect)"
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
        --model claude-opus-4-6 \
        --max-turns "$MAX_TURNS" \
        --output-format stream-json \
        --verbose \
        $PLUGIN_ARGS \
        2>&1 | tee "$LOG_FILE"
    local CODE=${PIPESTATUS[0]}
    set -e
    return $CODE
}

is_auth_error() {
    # Check if a log file contains auth failure patterns
    local LOG_FILE="$1"
    [[ -f "$LOG_FILE" ]] && grep -qi "authentication_error\|401\|OAuth token has expired\|login required\|unauthorized\|token expired\|EAUTH\|invalid_grant\|refresh_token" "$LOG_FILE" 2>/dev/null
}

if [[ "$RUN_TYPE" == "protect" ]]; then
    # Lightweight — no LLM needed.
    log "Phase 1: Watching for OPG fills (5 min polling)..."
    set +e
    /app/.venv/bin/python /app/trading/executor.py watch-fills \
        --file /app/pending_protections.json \
        --timeout 300 \
        --interval 15 \
        2>&1 | tee "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    WATCH_CODE=${PIPESTATUS[0]}

    log "Phase 2: Fallback protector check..."
    /app/.venv/bin/python /app/trading/protector.py \
        --file /app/pending_protections.json \
        --retries 2 \
        --delay 15 \
        2>&1 | tee -a "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    EXIT_CODE=${PIPESTATUS[0]}

    log "Phase 3: Trailing stops adjustment..."
    /app/.venv/bin/python /app/trading/executor.py trail-stops \
        2>&1 | tee -a "/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    set -e
else
    LOG_FILE="/app/logs/${RUN_TYPE}-${TIMESTAMP}.log"
    log "Starting Claude Code ($RUN_TYPE, auth=$AUTH_MODE, max_turns=$MAX_TURNS)..."

    run_claude "$PROMPT" "$MAX_TURNS" "$LOG_FILE"
    EXIT_CODE=$?

    # ── OAuth fail? Automatic fallback to API key ──
    if [[ $EXIT_CODE -ne 0 ]] && [[ "$AUTH_MODE" == "oauth" ]] && [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
        if is_auth_error "$LOG_FILE"; then
            log "AUTH: OAuth failed — switching to API key and retrying..."
            AUTH_MODE="api_key_fallback"

            # Restore credential backup (issue #29896)
            if [[ -f "$BACKUP_FILE" ]]; then
                cp "$BACKUP_FILE" "$CREDENTIALS_FILE" 2>/dev/null || true
            fi

            # Clear OAuth env so claude uses ANTHROPIC_API_KEY
            rm -f "$CREDENTIALS_FILE" 2>/dev/null || true

            # Send Discord alert with auth link
            send_auth_alert "OAuth 401/expired — retry en cours avec API key"

            # Retry with API key
            RETRY_LOG="/app/logs/${RUN_TYPE}-${TIMESTAMP}-retry.log"
            log "AUTH: Retrying with ANTHROPIC_API_KEY..."
            run_claude "$PROMPT" "$MAX_TURNS" "$RETRY_LOG"
            EXIT_CODE=$?

            # Append retry log to main log
            cat "$RETRY_LOG" >> "$LOG_FILE" 2>/dev/null || true
            log "AUTH: Retry completed (exit=$EXIT_CODE)"
        fi
    fi
fi

log "Session completed (exit: $EXIT_CODE, auth: $AUTH_MODE)"

# ==================================================================
# 5. OAUTH: PERSIST REFRESHED TOKENS (only if OAuth succeeded)
# ==================================================================
if [[ "$AUTH_MODE" == "oauth" ]] && [[ -f "$CREDENTIALS_FILE" ]] && [[ -s "$CREDENTIALS_FILE" ]]; then
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
elif [[ "$AUTH_MODE" == "oauth" ]]; then
    # Credentials wiped (issue #29896) — restore backup
    if [[ -f "$BACKUP_FILE" ]] && [[ ! -s "$CREDENTIALS_FILE" ]]; then
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
if [[ -f "$LOG_FILE" ]]; then
    TOTAL_TURNS=$(grep -c '"type":"assistant"' "$LOG_FILE" 2>/dev/null || echo "0")
    TOTAL_TOKENS=$(grep '"type":"result"' "$LOG_FILE" 2>/dev/null | python3 -c "
import sys, json
total = 0
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
        total += d.get('total_input_tokens', 0) + d.get('total_output_tokens', 0)
    except: pass
print(total)
" 2>/dev/null || echo "0")
    TOTAL_COST=$(grep '"type":"result"' "$LOG_FILE" 2>/dev/null | python3 -c "
import sys, json
cost = 0
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
        cost = max(cost, d.get('total_cost_usd', 0))
    except: pass
print(f'{cost:.4f}')
" 2>/dev/null || echo "0")
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
        2>&1 || log "Discord notification failed (non-fatal)"
fi

# Exit 0 if job ran (even with non-zero exit) — S3 sync is what matters
if [[ $EXIT_CODE -ne 0 ]]; then
    log "WARNING: Exited with $EXIT_CODE (non-fatal, S3 synced)"
fi
exit 0
