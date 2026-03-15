#!/bin/bash
set -euo pipefail

# ─── Load secrets from .env.local (in subshell to avoid polluting scw CLI env) ───
ENV_FILE="$(dirname "$0")/../.env.local"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found. Create it with required env vars."
    exit 1
fi

# Read specific vars only — avoid exporting everything (conflicts with scw CLI)
_read_var() { bash -c "source '$ENV_FILE' 2>/dev/null; echo \"\${$1:-}\""; }
APCA_API_KEY_ID=$(_read_var APCA_API_KEY_ID)
APCA_API_SECRET_KEY=$(_read_var APCA_API_SECRET_KEY)
DISCORD_WEBHOOK_URL=$(_read_var DISCORD_WEBHOOK_URL)
SCW_S3_ACCESS_KEY=$(_read_var SCW_S3_ACCESS_KEY)
SCW_S3_SECRET_KEY=$(_read_var SCW_S3_SECRET_KEY)

# OAuth credentials (primary auth — from Secret Manager)
CLAUDE_CREDENTIALS_B64=$(_read_var CLAUDE_CREDENTIALS_B64)
CLAUDE_CONFIG_B64=$(_read_var CLAUDE_CONFIG_B64)
CREDENTIALS_SECRET_ID=$(_read_var CREDENTIALS_SECRET_ID)
SCW_SECRET_KEY=$(_read_var SCW_SECRET_KEY)

# API key fallback (Plan B — only used if OAuth not configured)
ANTHROPIC_API_KEY=$(_read_var ANTHROPIC_API_KEY)

# Validate Alpaca (always required)
for var in APCA_API_KEY_ID APCA_API_SECRET_KEY; do
    if [[ -z "${!var}" ]]; then
        echo "ERROR: $var not found in $ENV_FILE"
        exit 1
    fi
done

# Validate auth: need either OAuth credentials or API key
if [[ -z "$CLAUDE_CREDENTIALS_B64" ]] && [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "ERROR: Neither CLAUDE_CREDENTIALS_B64 nor ANTHROPIC_API_KEY found in $ENV_FILE"
    echo "  OAuth auth: set CLAUDE_CREDENTIALS_B64, CLAUDE_CONFIG_B64, CREDENTIALS_SECRET_ID, SCW_SECRET_KEY"
    echo "  API key fallback: set ANTHROPIC_API_KEY"
    exit 1
fi

if [[ -n "$CLAUDE_CREDENTIALS_B64" ]]; then
    echo "Auth mode: OAuth (subscription-based, \$0/month)"
else
    echo "Auth mode: API key fallback (~\$0.60/month)"
fi

SCW=~/.local/bin/scw
IMAGE="rg.fr-par.scw.cloud/claude-trading/claude-bot:latest"

# Common env vars for all jobs (Alpaca + S3 + Discord)
ENV_COMMON=(
    environment-variables.APCA_API_KEY_ID="$APCA_API_KEY_ID"
    environment-variables.APCA_API_SECRET_KEY="$APCA_API_SECRET_KEY"
    environment-variables.ALPACA_PAPER_TRADE="true"
    environment-variables.ALPACA_BASE_URL="https://paper-api.alpaca.markets"
    environment-variables.DISCORD_WEBHOOK_URL="${DISCORD_WEBHOOK_URL:-}"
    environment-variables.TRADING_MODE="paper"
    environment-variables.SCW_S3_ACCESS_KEY="${SCW_S3_ACCESS_KEY:-}"
    environment-variables.SCW_S3_SECRET_KEY="${SCW_S3_SECRET_KEY:-}"
)

# OAuth credentials (primary auth)
if [[ -n "$CLAUDE_CREDENTIALS_B64" ]]; then
    ENV_COMMON+=(
        environment-variables.CLAUDE_CREDENTIALS_B64="$CLAUDE_CREDENTIALS_B64"
        environment-variables.CLAUDE_CONFIG_B64="${CLAUDE_CONFIG_B64:-}"
        environment-variables.CREDENTIALS_SECRET_ID="${CREDENTIALS_SECRET_ID:-}"
        environment-variables.SCW_SECRET_KEY="${SCW_SECRET_KEY:-}"
        environment-variables.SCW_DEFAULT_REGION="fr-par"
    )
fi

# API key fallback (Plan B)
if [[ -n "$ANTHROPIC_API_KEY" ]]; then
    ENV_COMMON+=(
        environment-variables.ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"
    )
fi

echo "=== Creating Scaleway Serverless Jobs ==="
echo "Image: $IMAGE"
echo ""

# ─── Job 1: Market Open — Full trading pipeline ───
# 9:35 AM ET (Mon-Fri)
echo "Creating job: trading-market-open..."
OPEN_ID=$($SCW jobs definition create \
    name=trading-market-open \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="open" \
    "${ENV_COMMON[@]}" \
    job-timeout=2700s \
    cron-schedule.schedule="35 9 * * 1-5" \
    cron-schedule.timezone="America/New_York" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Market Open Job ID: $OPEN_ID"

# ─── Job 2: Post-Open Protection — OCO orders after OPG fills ───
# 9:33 AM ET (Mon-Fri) — lightweight, no LLM
echo "Creating job: trading-post-open-protect..."
PROTECT_ID=$($SCW jobs definition create \
    name=trading-post-open-protect \
    cpu-limit=1000 \
    memory-limit=512 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="protect" \
    "${ENV_COMMON[@]}" \
    job-timeout=300s \
    cron-schedule.schedule="33 9 * * 1-5" \
    cron-schedule.timezone="America/New_York" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Post-Open Protect Job ID: $PROTECT_ID"

# ─── Job 3: Market Close — EOD review & position management ───
# 3:45 PM ET (Mon-Fri)
echo "Creating job: trading-market-close..."
CLOSE_ID=$($SCW jobs definition create \
    name=trading-market-close \
    cpu-limit=2000 \
    memory-limit=2048 \
    image-uri="$IMAGE" \
    command="/app/entrypoint.sh" \
    environment-variables.RUN_TYPE="close" \
    "${ENV_COMMON[@]}" \
    job-timeout=1200s \
    cron-schedule.schedule="45 15 * * 1-5" \
    cron-schedule.timezone="America/New_York" \
    region=fr-par \
    -o json | jq -r '.id')

echo "  Market Close Job ID: $CLOSE_ID"

# ─── Save job IDs ───
cat > ~/.claude-trading-jobs << EOF
OPEN_ID=$OPEN_ID
PROTECT_ID=$PROTECT_ID
CLOSE_ID=$CLOSE_ID
EOF

echo ""
echo "=== Jobs created successfully ==="
echo "Job IDs saved to ~/.claude-trading-jobs"
echo ""
echo "Cron schedules (America/New_York):"
echo "  Post-Open Protect: 33 9 * * 1-5  (9:33 AM ET) — OCO after OPG fills, no LLM"
echo "  Market Open:       35 9 * * 1-5  (9:35 AM ET) — full pipeline"
echo "  Market Close:      45 15 * * 1-5 (3:45 PM ET) — EOD review"
echo ""
echo "Manual trigger:"
echo "  $SCW jobs definition start $OPEN_ID region=fr-par"
echo "  $SCW jobs definition start $PROTECT_ID region=fr-par"
echo "  $SCW jobs definition start $CLOSE_ID region=fr-par"
