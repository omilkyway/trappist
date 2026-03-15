# ── TRAPPIST — Scaleway Serverless ──
# Source: source ~/MILKY-WAY/DEV/cc/TRAPPIST/deploy/aliases.sh
# Add to .zshrc: source ~/MILKY-WAY/DEV/cc/TRAPPIST/deploy/aliases.sh

source ~/.trappist-jobs 2>/dev/null
SCW=~/.local/bin/scw

# ── Monitoring ──
alias bot-jobs="$SCW jobs definition list region=fr-par"
alias bot-runs="$SCW jobs run list region=fr-par -o json | jq -r '.[] | \"\(.id | .[0:8]) \(.state) \(.created_at) \(.job_definition_id | .[0:8])\"' | head -20"

bot-status() {
    echo "=== Job Definitions ==="
    $SCW jobs definition list region=fr-par
    echo ""
    echo "=== Recent Runs ==="
    $SCW jobs run list region=fr-par -o json | jq -r '.[] | "\(.id | .[0:8])... \(.state | if . == "succeeded" then "\u001b[32m" + . + "\u001b[0m" elif . == "failed" then "\u001b[31m" + . + "\u001b[0m" elif . == "running" then "\u001b[33m" + . + "\u001b[0m" else . end)  \(.created_at)"' | head -10
}

bot-run-log() {
    local RUN_ID="${1:?Usage: bot-run-log <run-id>}"
    $SCW jobs run get "$RUN_ID" region=fr-par -o json | jq '{state, started_at, run_duration, exit_code, error_message}'
}

# ── Trigger manual ──
alias bot-run-cycle="$SCW jobs definition start \$CYCLE_ID region=fr-par -o json | jq '{job_runs: [.job_runs[] | {id, state}]}'"
alias bot-run-protect="$SCW jobs definition start \$PROTECT_ID region=fr-par -o json | jq '{job_runs: [.job_runs[] | {id, state}]}'"
alias bot-run-review="$SCW jobs definition start \$REVIEW_ID region=fr-par -o json | jq '{job_runs: [.job_runs[] | {id, state}]}'"

# ── Deploy ──
bot-deploy() {
    local IMAGE="rg.fr-par.scw.cloud/trappist/crypto-bot:latest"
    local PROJECT_DIR=~/MILKY-WAY/DEV/cc/TRAPPIST
    echo "Building image..."
    DOCKER_BUILDKIT=0 docker build -f "$PROJECT_DIR/deploy/Dockerfile" -t "$IMAGE" "$PROJECT_DIR/"
    echo "Pushing image..."
    docker push "$IMAGE"
    echo "Done. Next cron run will use the new image."
}

# ── OAuth re-auth (when Discord alerts AUTH FAIL) ──
bot-reauth() {
    bash ~/MILKY-WAY/DEV/cc/TRAPPIST/deploy/scripts/reauth.sh "$@"
}

# ── S3 state ──
alias bot-s3-ls="aws --profile scaleway s3 ls s3://trappist/ --recursive"
alias bot-s3-progress="aws --profile scaleway s3 cp s3://trappist/progress.md -"

# ── Check OAuth token status ──
bot-auth-status() {
    local CREDS="$HOME/.claude/.credentials.json"
    if [[ ! -f "$CREDS" ]]; then
        echo "No credentials file found at $CREDS"
        return 1
    fi
    local EXPIRES_AT=$(jq -r '.claudeAiOauth.expiresAt // 0' "$CREDS")
    local NOW_MS=$(($(date +%s) * 1000))
    local HAS_REFRESH=$(jq -r '.claudeAiOauth.refreshToken // ""' "$CREDS" | wc -c)
    if [[ "$EXPIRES_AT" -gt "$NOW_MS" ]]; then
        local REMAINING=$(( (EXPIRES_AT - NOW_MS) / 1000 ))
        echo "Access token: VALID (expires in ${REMAINING}s)"
    else
        echo "Access token: EXPIRED"
    fi
    if [[ "$HAS_REFRESH" -gt 5 ]]; then
        echo "Refresh token: PRESENT"
    else
        echo "Refresh token: MISSING"
    fi
}
