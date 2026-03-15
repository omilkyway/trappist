#!/bin/bash
# Setup local environment for TRAPPIST CLI tools.
# Sources .env.local and configures AWS scaleway profile for S3 access.
#
# Usage:
#   source deploy/setup-local-env.sh
#   # Then you can run: python trading/executor.py account
#   # And: aws --profile scaleway s3 ls s3://trappist/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$PROJECT_DIR/.env.local"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found"
    return 1 2>/dev/null || exit 1
fi

# ─── Export all vars from .env.local ───
set -a
source "$ENV_FILE"
set +a

echo "Loaded env vars from .env.local"

# ─── Map SCALEWAY_* to SCW_S3_* if needed (naming compat) ───
if [[ -n "${SCALEWAY_ACCESS_KEY:-}" && -z "${SCW_S3_ACCESS_KEY:-}" ]]; then
    export SCW_S3_ACCESS_KEY="$SCALEWAY_ACCESS_KEY"
    echo "  Mapped SCALEWAY_ACCESS_KEY -> SCW_S3_ACCESS_KEY"
fi
if [[ -n "${SCALEWAY_SECRET_KEY:-}" && -z "${SCW_S3_SECRET_KEY:-}" ]]; then
    export SCW_S3_SECRET_KEY="$SCALEWAY_SECRET_KEY"
    echo "  Mapped SCALEWAY_SECRET_KEY -> SCW_S3_SECRET_KEY"
fi

# ─── Configure AWS CLI scaleway profile (for S3 access) ───
if [[ -n "${SCW_S3_ACCESS_KEY:-}" ]]; then
    mkdir -p ~/.aws

    if ! grep -q '^\[scaleway\]' ~/.aws/credentials 2>/dev/null; then
        cat >> ~/.aws/credentials << CREDS

[scaleway]
aws_access_key_id = ${SCW_S3_ACCESS_KEY}
aws_secret_access_key = ${SCW_S3_SECRET_KEY}
CREDS
        echo "  Created AWS credentials profile [scaleway]"
    else
        python3 -c "
import configparser, os
p = os.path.expanduser('~/.aws/credentials')
c = configparser.ConfigParser()
c.read(p)
c['scaleway'] = {
    'aws_access_key_id': os.environ['SCW_S3_ACCESS_KEY'],
    'aws_secret_access_key': os.environ['SCW_S3_SECRET_KEY'],
}
with open(p, 'w') as f:
    c.write(f)
"
        echo "  Updated AWS credentials profile [scaleway]"
    fi

    if ! grep -q '^\[profile scaleway\]' ~/.aws/config 2>/dev/null; then
        cat >> ~/.aws/config << CONF

[profile scaleway]
region = fr-par
endpoint_url = https://s3.fr-par.scw.cloud
CONF
        echo "  Created AWS config profile [profile scaleway]"
    else
        echo "  AWS config profile [profile scaleway] already exists"
    fi
else
    echo "  WARNING: No S3 credentials found (SCALEWAY_ACCESS_KEY or SCW_S3_ACCESS_KEY)"
fi

# ─── Verify ───
echo ""
echo "Environment ready:"
echo "  BINANCE_KEY_API:   ${BINANCE_KEY_API:+SET (${#BINANCE_KEY_API} chars)}"
echo "  BINANCE_KEY_SECRET: ${BINANCE_KEY_SECRET:+SET}"
echo "  ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:+SET}"
echo "  SCW_S3_ACCESS_KEY: ${SCW_S3_ACCESS_KEY:+SET}"
echo "  AWS profile:       scaleway"
echo ""
echo "Test commands:"
echo "  python trading/executor.py account"
echo "  aws --profile scaleway s3 ls s3://trappist/"
