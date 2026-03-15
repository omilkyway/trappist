#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.8"
# dependencies = ["ccxt"]
# ///

"""
Circuit breaker hook — runs before any Binance Futures trade execution.
Exit code 0 = ALLOW the tool call.
Exit code 2 = BLOCK the tool call (deterministic guarantee).

Checks account balance via CCXT. If equity has dropped more than
5% from initial balance, all order-placing tools are blocked.
Also intercepts Bash calls to trading/executor.py that place orders.
"""

import json
import os
import re
import sys
from pathlib import Path

# Read-only tools that never need blocking
READONLY_TOOLS = {
    "mcp__binance-futures__get_account_summary",
    "mcp__binance-futures__get_balance",
    "mcp__binance-futures__get_positions",
    "mcp__binance-futures__get_open_orders",
    "mcp__binance-futures__get_order",
    "mcp__binance-futures__get_klines",
    "mcp__binance-futures__get_ticker",
    "mcp__binance-futures__get_order_book",
    "mcp__binance-futures__get_recent_trades",
    "mcp__binance-futures__get_symbol_info",
    "mcp__binance-futures__get_leverage_brackets",
    "mcp__binance-futures__get_trade_history",
    "mcp__binance-futures__get_order_history",
    "mcp__binance-futures__get_position_mode",
    "mcp__binance-futures__ping",
    # Other read-only MCP tools
    "mcp__fear-greed__get_current_fng_tool",
    "mcp__fear-greed__get_historical_fng_tool",
    "mcp__fear-greed__analyze_fng_trend",
    "mcp__gloria-news__get_latest_news",
    "mcp__gloria-news__get_enriched_news",
    "mcp__gloria-news__search_news",
    "mcp__gloria-news__get_news_recap",
    "mcp__gloria-news__get_news_item",
    "mcp__gloria-news__get_ticker_summary",
    "mcp__gloria-news__get_categories",
    "mcp__tradingview__get_historical_data",
    "mcp__tradingview__get_indicators",
    "mcp__tradingview__get_specific_indicators",
}

# Patterns in Bash commands that indicate order placement
BASH_ORDER_PATTERNS = [
    r"executor\.py\s+(bracket|close|cancel|cancel-all|set-leverage|set-margin)\b",
    r"ccxt\s+binance\s+create",
]

DRAWDOWN_LIMIT = -0.05  # -5% for crypto (more volatile)


def _load_dotenv():
    """Load .env file from project root if it exists."""
    for env_name in (".env.local", ".env"):
        env_path = Path(__file__).resolve().parent.parent.parent / env_name
        if not env_path.exists():
            continue
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


def get_balance():
    """Fetch balance directly from Binance Futures API via CCXT."""
    _load_dotenv()
    import ccxt

    key = os.environ.get("BINANCE_API_KEY") or os.environ.get("BINANCE_KEY_API") or ""
    secret = os.environ.get("BINANCE_API_SECRET") or os.environ.get("BINANCE_SECRET") or os.environ.get("BINANCE_KEY_SECRET") or ""
    sandbox = os.environ.get("LIVE_MODE", "").lower() not in ("true", "1", "yes")

    # Fall back to keys.local.json
    if not key or not secret:
        keys_path = Path(__file__).resolve().parent.parent.parent / "keys.local.json"
        if keys_path.exists():
            try:
                with open(keys_path) as f:
                    keys = json.load(f)
                binance_keys = keys.get("binance", {})
                key = key or binance_keys.get("apiKey", "")
                secret = secret or binance_keys.get("secret", "")
            except Exception:
                pass

    exchange = ccxt.binance({
        "apiKey": key,
        "secret": secret,
        "options": {"defaultType": "future"},
        "enableRateLimit": True,
    })
    if sandbox:
        exchange.set_sandbox_mode(True)

    balance = exchange.fetch_balance()
    usdt = balance.get("USDT", {})
    return float(usdt.get("total", 0) or 0)


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
    if len(log_data) > MAX_LOG_ENTRIES:
        log_data = log_data[-MAX_LOG_ENTRIES:]

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
                sys.exit(0)

        # For order-affecting tools, check balance against initial
        try:
            current_balance = get_balance()

            # Load initial balance from state.json
            state_path = Path.cwd() / "state.json"
            initial_balance = 0
            if state_path.exists():
                try:
                    state = json.load(open(state_path))
                    initial_balance = float(state.get("initial_balance", 0))
                except Exception:
                    pass

            # If no initial balance recorded, allow (first run)
            if initial_balance <= 0:
                log_event({
                    "tool_name": tool_name,
                    "balance": current_balance,
                    "initial_balance": initial_balance,
                    "decision": "allow (no initial balance recorded)",
                })
                sys.exit(0)

            drawdown = (current_balance - initial_balance) / initial_balance

            log_event({
                "tool_name": tool_name,
                "balance": current_balance,
                "initial_balance": initial_balance,
                "drawdown_pct": round(drawdown * 100, 2),
                "decision": "block" if drawdown < DRAWDOWN_LIMIT else "allow",
            })

            if drawdown < DRAWDOWN_LIMIT:
                msg = (
                    f"CIRCUIT BREAKER: Drawdown {drawdown:.2%} "
                    f"exceeds {DRAWDOWN_LIMIT:.0%} limit. "
                    f"Balance: ${current_balance:,.2f}, Initial: ${initial_balance:,.2f}. "
                    f"Tool blocked: {tool_name}"
                )
                print(msg, file=sys.stderr)
                sys.exit(2)  # BLOCK

        except Exception as e:
            # FAIL-CLOSED: if we can't verify, block order-placing tools
            log_event({
                "tool_name": tool_name,
                "error": str(e),
                "decision": "block (api error - fail-closed)",
            })
            print(
                f"CIRCUIT BREAKER: Cannot verify balance ({e}). "
                f"Blocking {tool_name} for safety.",
                file=sys.stderr,
            )
            sys.exit(2)  # BLOCK

        sys.exit(0)  # ALLOW

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
