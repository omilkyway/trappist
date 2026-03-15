#!/bin/bash
set -euo pipefail

# ─── Load secrets from .env.local ───
ENV_FILE="$(dirname "$0")/../.env.local"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Create it with required env vars."
    exit 1
fi

_read_var() { bash -c "source '$ENV_FILE' 2>/dev/null; echo \"\${$1:-}\""; }
BINANCE_API_KEY=$(_read_var BINANCE_API_KEY)
BINANCE_API_SECRET=$(_read_var BINANCE_API_SECRET)
DISCORD_WEBHOOK_URL=$(_read_var DISCORD_WEBHOOK_URL)
SCW_S3_ACCESS_KEY=$(_read_var SCW_S3_ACCESS_KEY)
SCW_S3_SECRET_KEY=$(_read_var SCW_S3_SECRET_KEY)

# OAuth credentials (primary auth — from Secret Manager)
CLAUDE_CREDENTIALS_B64=$(_read_var CLAUDE_CREDENTIALS_B64)
CLAUDE_CONFIG_B64=$(_read_var CLAUDE_CONFIG_B64)
CREDENTIALS_SECRET_ID=$(_read_var CREDENTIALS_SECRET_ID)
SCW_SECRET_KEY=$(_read_var SCW_SECRET_KEY)

# API key fallback
ANTHROPIC_API_KEY=$(_read_var ANTHROPIC_API_KEY)

# Validate Binance credentials
for var in BINANCE_API_KEY BINANCE_API_SECRET; do
    if [[ -z "${!var}" ]]; then
        echo "ERROR: $var not found in $ENV_FILE"
        exit 1
    fi
done

# Validate Claude auth
if [[ -z "$CLAUDE_CREDENTIALS_B64" ]] && [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "ERROR: Neither CLAUDE_CREDENTIALS_B64 nor ANTHROPIC_API_KEY found in $ENV_FILE"
    exit 1
fi

if [[ -n "$CLAUDE_CREDENTIALS_B64" ]]; then
    echo "Auth mode: OAuth (subscription-based)"
else
    echo "Auth mode: API key fallback"
fi

SCW=~/.local/bin/scw
IMAGE="rg.fr-par.scw.cloud/trappist/crypto-bot:latest"

# Common env vars for all jobs
ENV_COMMON=(
    environment-variables.BINANCE_API_KEY="$BINANCE_API_KEY"
    environment-variables.BINANCE_API_SECRET="$BINANCE_API_SECRET"
    environment-variables.DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"
    environment-variables.SCW_S3_ACCESS_KEY="${SCW_S3_ACCESS_KEY:-}"
    environment-variables.SCW_S3_SECRET_KEY="${SCW_S3_SECRET_KEY:-}"
)

# OAuth credentials
if [[ -n "$CLAUDE_CREDENTIALS_B64" ]]; then
    ENV_COMMON+=(
        environment-variables.CLAUDE_CREDENTIALS_B64="$CLAUDE_CREDENTIALS_B64"
        environment-variables.CLAUDE_CONFIG_B64="${CLAUDE_CONFIG_B64:-}"
        environment-variables.CREDENTIALS_SECRET_ID="${CREDENTIALS_SECRET_ID:-}"
        environment-variables.SCW_SECRET_KEY="${SCW_SECRET_KEY:-}"
        environment-variables.SCW_DEFAULT_REGION="fr-par"
    )
fi

# API key fallback
if [[ -n "$ANTHROPIC_API_KEY" ]]; then
    ENV_COMMON+=(
        environment-variables.ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
    )
fi

echo "=== Creating Scaleway Serverless Jobs (TRAPPIST Crypto) ==="
echo "Image: $IMAGE"
echo ""

# ─── Job 1: Trading Cycle — Full pipeline (every 15 min, 24/7) ───
echo "Creating job: trappist-trading-cycle..."
CYCLE_ID=$($SCW jobs definition create \
    name=trappist-trading-cycle \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="cycle" \
    "${ENV_COMMON[@]}" \
    job-timeout=2700s \
    cron-schedule.schedule="*/15 * * * *" \
    cron-schedule.timezone="UTC" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Trading Cycle Job ID: $CYCLE_ID"

# ─── Job 2: Protection Check (every 5 min, 24/7) — lightweight, no LLM ───
echo "Creating job: trappist-protection-check..."
PROTECT_ID=$($SCW jobs definition create \
    name=trappist-protection-check \
    cpu-limit=1000 \
    memory-limit=512 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="protect" \
    "${ENV_COMMON[@]}" \
    job-timeout=300s \
    cron-schedule.schedule="*/5 * * * *" \
    cron-schedule.timezone="UTC" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Protection Check Job ID: $PROTECT_ID"

# ─── Job 3: Portfolio Review (every 4 hours, 24/7) ───
echo "Creating job: trappist-portfolio-review..."
REVIEW_ID=$($SCW jobs definition create \
    name=trappist-portfolio-review \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="review" \
    "${ENV_COMMON[@]}" \
    job-timeout=1200s \
    cron-schedule.schedule="0 */4 * * *" \
    cron-schedule.timezone="UTC" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Portfolio Review Job ID: $REVIEW_ID"

# ─── Save job IDs ───
cat > ~/.trappist-jobs << EOF
CYCLE_ID=$CYCLE_ID
PROTECT_ID=$PROTECT_ID
REVIEW_ID=$REVIEW_ID
EOF

echo ""
echo "=== Jobs created successfully ==="
echo "Job IDs saved to ~/.trappist-jobs"
echo ""
echo "Cron schedules (UTC, 24/7):"
echo "  Trading Cycle:     */15 * * * *  (every 15 min)"
echo "  Protection Check:  */5 * * * *   (every 5 min, no LLM)"
echo "  Portfolio Review:  0 */4 * * *   (every 4 hours)"
echo ""
echo "Manual trigger:"
echo "  $SCW jobs definition start $CYCLE_ID region=fr-par"
echo "  $SCW jobs definition start $PROTECT_ID region=fr-par"
echo "  $SCW jobs definition start $REVIEW_ID region=fr-par"
