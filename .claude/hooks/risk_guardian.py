#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# ///

"""
Circuit breaker hook — runs before any Alpaca MCP tool call or Bash trade execution.
Exit code 0 = ALLOW the tool call.
Exit code 2 = BLOCK the tool call (deterministic guarantee).

Checks daily drawdown via Alpaca API. If equity has dropped more than
2% from last_equity (previous close), all order-placing tools are blocked.
Also intercepts Bash calls to trading/executor.py that place orders.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

# Read-only tools that never need blocking
READONLY_TOOLS = {
    "mcp__alpaca__get_account_info",
    "mcp__alpaca__get_positions",
    "mcp__alpaca__get_open_position",
    "mcp__alpaca__get_orders",
    "mcp__alpaca__get_stock_quote",
    "mcp__alpaca__get_stock_bars",
    "mcp__alpaca__get_stock_latest_trade",
    "mcp__alpaca__get_stock_latest_bar",
    "mcp__alpaca__get_stock_snapshot",
    "mcp__alpaca__get_asset_info",
    "mcp__alpaca__get_all_assets",
    "mcp__alpaca__get_market_clock",
}

# Patterns in Bash commands that indicate order placement
BASH_ORDER_PATTERNS = [
    r"executor\.py\s+(bracket|opg|oco|close)\b",
    r"curl.*alpaca\.markets.*/v2/orders",
]

DRAWDOWN_LIMIT = -0.02  # -2%


def _load_dotenv():
    """Load .env file from project root if it exists."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key not in os.environ:
                os.environ[key] = value


def get_account():
    """Fetch account info directly from Alpaca REST API."""
    _load_dotenv()
    # Support both APCA_* (SDK standard) and ALPACA_* (project .env) formats
    base_url = os.environ.get(
        "APCA_API_BASE_URL",
        os.environ.get("ALPACA_API_BASE_URL", "https://paper-api.alpaca.markets"),
    )
    key = os.environ.get(
        "APCA_API_KEY_ID", os.environ.get("ALPACA_API_KEY", "")
    )
    secret = os.environ.get(
        "APCA_API_SECRET_KEY", os.environ.get("ALPACA_SECRET_KEY", "")
    )

    req = urllib.request.Request(
        f"{base_url}/v2/account",
        headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        },
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


MAX_LOG_ENTRIES = 500


def log_event(event_data):
    """Append event to logs/risk_guardian.json with rotation and atomic write."""
    import tempfile

    log_dir = Path.cwd() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "risk_guardian.json"

    if log_path.exists():
        with open(log_path, "r") as f:
            try:
                log_data = json.load(f)
            except (json.JSONDecodeError, ValueError):
                log_data = []
    else:
        log_data = []

    log_data.append(event_data)

    # Rotate: keep only the most recent entries
    if len(log_data) > MAX_LOG_ENTRIES:
        log_data = log_data[-MAX_LOG_ENTRIES:]

    # Atomic write
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=log_dir, prefix=".risk_guardian_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(log_data, f, indent=2)
        os.replace(tmp_path, str(log_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main():
    try:
        input_data = json.loads(sys.stdin.read())
        tool_name = input_data.get("tool_name", "")

        # Allow read-only tools without any check
        if tool_name in READONLY_TOOLS:
            sys.exit(0)

        # For Bash tool, check if the command places orders
        if tool_name == "Bash":
            command = input_data.get("tool_input", {}).get("command", "")
            is_order_cmd = any(
                re.search(pat, command) for pat in BASH_ORDER_PATTERNS
            )
            if not is_order_cmd:
                sys.exit(0)  # Non-order Bash commands are always allowed

        # For order-affecting tools, check daily drawdown
        try:
            account = get_account()
            equity = float(account.get("equity", 0))
            last_equity = float(account.get("last_equity", equity))

            daily_change = (
                (equity - last_equity) / last_equity if last_equity > 0 else 0
            )

            log_event(
                {
                    "tool_name": tool_name,
                    "equity": equity,
                    "last_equity": last_equity,
                    "daily_change_pct": round(daily_change * 100, 2),
                    "decision": "block" if daily_change < DRAWDOWN_LIMIT else "allow",
                }
            )

            if daily_change < DRAWDOWN_LIMIT:
                msg = (
                    f"CIRCUIT BREAKER: Daily drawdown {daily_change:.2%} "
                    f"exceeds {DRAWDOWN_LIMIT:.0%} limit. "
                    f"Equity: ${equity:,.2f}, Last close: ${last_equity:,.2f}. "
                    f"Tool blocked: {tool_name}"
                )
                print(msg, file=sys.stderr)
                sys.exit(2)  # BLOCK

        except Exception as e:
            # FAIL-CLOSED: if we can't verify drawdown, block order-placing tools
            # This is safer than allowing trades without circuit breaker protection
            log_event(
                {
                    "tool_name": tool_name,
                    "error": str(e),
                    "decision": "block (api error - fail-closed)",
                }
            )
            print(
                f"CIRCUIT BREAKER: Cannot verify drawdown ({e}). "
                f"Blocking {tool_name} for safety. Fix API connectivity to resume trading.",
                file=sys.stderr,
            )
            sys.exit(2)  # BLOCK — fail-closed

        sys.exit(0)  # ALLOW

    except json.JSONDecodeError:
        # Bad stdin input — allow (not an order-related issue)
        sys.exit(0)
    except Exception:
        # Unexpected error parsing hook input — allow
        sys.exit(0)


if __name__ == "__main__":
    main()
