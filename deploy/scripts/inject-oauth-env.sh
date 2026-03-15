#!/bin/bash
set -euo pipefail

# Inject CLAUDE_CREDENTIALS_B64 and CLAUDE_CONFIG_B64 into .env.local
# Run once: bash deploy/scripts/inject-oauth-env.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
ENV_FILE="$PROJECT_DIR/.env.local"

CREDS_FILE="$HOME/.claude/.credentials.json"
CONFIG_FILE="$HOME/.claude.json"

if [[ ! -f "$CREDS_FILE" ]]; then
    echo "ERROR: $CREDS_FILE not found. Run: claude auth login"
    exit 1
fi

CREDS_B64=$(base64 -w 0 < "$CREDS_FILE")
CONFIG_B64=""
if [[ -f "$CONFIG_FILE" ]]; then
    CONFIG_B64=$(base64 -w 0 < "$CONFIG_FILE")
fi

# Add or update in .env.local
add_or_update() {
    local KEY="$1"
    local VALUE="$2"
    local FILE="$3"
    if grep -q "^${KEY}=" "$FILE" 2>/dev/null; then
        sed -i "s|^${KEY}=.*|${KEY}=${VALUE}|" "$FILE"
        echo "  Updated $KEY"
    else
        echo "${KEY}=${VALUE}" >> "$FILE"
        echo "  Added $KEY"
    fi
}

echo "Injecting OAuth credentials into $ENV_FILE..."
add_or_update "CLAUDE_CREDENTIALS_B64" "$CREDS_B64" "$ENV_FILE"
if [[ -n "$CONFIG_B64" ]]; then
    add_or_update "CLAUDE_CONFIG_B64" "$CONFIG_B64" "$ENV_FILE"
fi

echo ""
echo "Done. Credentials encoded:"
echo "  CLAUDE_CREDENTIALS_B64: $(echo -n "$CREDS_B64" | wc -c) chars"
echo "  CLAUDE_CONFIG_B64: $(echo -n "$CONFIG_B64" | wc -c) chars"
echo ""
echo "Next: bot-deploy to rebuild the Docker image"
