#!/usr/bin/env python3
"""Discord webhook notifications for TRAPPIST crypto trading pipeline.

Sends rich embed messages adapted per run type.
Called by entrypoint.sh after each Scaleway job completes.

Usage:
  python trading/discord.py --run-type cycle --exit-code 0 --cost 7.78 --turns 188
  python trading/discord.py --run-type protect --exit-code 0
  python trading/discord.py --test
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import get_account, get_open_orders, get_positions, is_sandbox

# ---------------------------------------------------------------------------
# Colors & formatting
# ---------------------------------------------------------------------------

COLOR_GREEN = 0x2ECC71   # success / profit
COLOR_RED = 0xE74C3C     # error / loss
COLOR_ORANGE = 0xF39C12  # warning
COLOR_BLUE = 0x3498DB    # info
COLOR_PURPLE = 0x9B59B6  # special


def _pnl_emoji(value: float) -> str:
    if value > 0:
        return "\U0001f7e2"  # green circle
    elif value < 0:
        return "\U0001f534"  # red circle
    return "\u26aa"  # white circle


def _format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def _safe(func, default=None):
    """Call func and return result, or default on exception."""
    try:
        return func()
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def _load_trade_context() -> dict:
    """Load trade context written by Claude during STEP 4."""
    for path in ["trade_context.json", "/app/trade_context.json"]:
        try:
            with open(path) as f:
                import json
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return {}


def _build_cycle_embed(exit_code: int, cost: float = 0, turns: int = 0) -> dict:
    """Build single merged embed for trading cycle run."""
    acct = _safe(get_account, {})
    positions = _safe(get_positions, [])
    orders = _safe(get_open_orders, [])
    ctx = _load_trade_context()
    mode = "TESTNET" if is_sandbox() else "LIVE"

    color = COLOR_GREEN if exit_code == 0 else COLOR_RED
    equity = acct.get("equity", 0)
    unrealized_pnl = acct.get("unrealized_pnl", 0)
    exposure_pct = acct.get("exposure_pct", 0)

    # Row 1: mode, F&G, equity, exposure
    fields = [
        {"name": "Mode", "value": f"`{mode}`", "inline": True},
    ]
    if ctx.get("fng"):
        fields.append({"name": "F&G", "value": f"`{ctx['fng']}`", "inline": True})
    fields += [
        {"name": "Equity", "value": f"`{equity:,.2f} USDT`", "inline": True},
        {"name": "Exposure", "value": f"`{exposure_pct:.1f}%`", "inline": True},
    ]

    # Row 2: trades placed, drawdown
    if ctx.get("trades_placed"):
        fields.append({"name": "Trades Placed", "value": f"`{ctx['trades_placed']}`", "inline": True})
    if ctx.get("drawdown_pct") is not None:
        dd = ctx["drawdown_pct"]
        fields.append({"name": "Drawdown", "value": f"`{dd:+.2f}%`", "inline": True})

    # New trades detail
    if ctx.get("new_trades"):
        for i, trade in enumerate(ctx["new_trades"][:4]):
            label = "New Trade" if len(ctx["new_trades"]) == 1 else f"New Trade {i+1}"
            fields.append({"name": label, "value": trade, "inline": False})

    # Open positions
    if positions:
        pos_lines = []
        for p in positions[:8]:
            emoji = _pnl_emoji(p["unrealized_pnl"])
            side_emoji = "\U0001f4c8" if p["side"] == "long" else "\U0001f4c9"
            pos_lines.append(
                f"{side_emoji} **{p['symbol']}** {p['side'].upper()} "
                f"{p['contracts']} @ {p['entry_price']:,.2f} "
                f"{emoji} {_format_money(p['unrealized_pnl'])} ({p['pnl_pct']:+.2f}%)"
            )
        fields.append({"name": f"Positions ({len(positions)})", "value": "\n".join(pos_lines), "inline": False})
    else:
        fields.append({"name": "Positions", "value": "None", "inline": True})

    if unrealized_pnl != 0:
        fields.append({
            "name": "Unrealized PnL",
            "value": f"{_pnl_emoji(unrealized_pnl)} `{_format_money(unrealized_pnl)}`",
            "inline": True,
        })

    if orders:
        fields.append({"name": "Open Orders", "value": f"`{len(orders)}`", "inline": True})

    # Reasoning
    if ctx.get("reasoning"):
        fields.append({"name": "Reasoning", "value": ctx["reasoning"][:1024], "inline": False})

    # Session meta
    if cost > 0:
        fields.append({"name": "Session Cost", "value": f"`${cost:.2f}`", "inline": True})
    if turns > 0:
        fields.append({"name": "Turns", "value": f"`{turns}`", "inline": True})

    return {
        "title": f"\U0001fa90 TRAPPIST Trading Cycle {'Complete' if exit_code == 0 else 'FAILED'}",
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": f"trappist v2.0 • exit {exit_code}"},
    }


def _build_protect_embed(exit_code: int) -> dict:
    """Build embed for protection check."""
    positions = _safe(get_positions, [])
    orders = _safe(get_open_orders, [])

    if not positions:
        return {
            "title": "\U0001f6e1 Protection Check — No Positions",
            "color": COLOR_BLUE,
            "description": "No open positions to protect.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # Check protection
    protected = []
    naked = []
    for pos in positions:
        sym = pos["symbol"]
        has_protection = any(
            o["symbol"] == sym and o.get("reduce_only")
            for o in orders
        )
        if has_protection:
            protected.append(sym)
        else:
            naked.append(sym)

    if naked:
        color = COLOR_RED
        title = f"\u26a0\ufe0f Protection — {len(naked)} UNPROTECTED"
    elif exit_code != 0:
        color = COLOR_ORANGE
        title = "\u26a0\ufe0f Protection — Check completed with warnings"
    else:
        color = COLOR_GREEN
        title = f"\U0001f6e1 Protection — All {len(protected)} protected"

    fields = []
    if protected:
        fields.append({"name": "Protected", "value": ", ".join(protected), "inline": False})
    if naked:
        fields.append({"name": "\U0001f534 NAKED", "value": ", ".join(naked), "inline": False})

    return {
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "trappist v2.0"},
    }


def _build_error_embed(run_type: str, exit_code: int, error_msg: str = "") -> dict:
    """Build embed for error/crash."""
    return {
        "title": f"\U0001f6a8 TRAPPIST {run_type.upper()} FAILED",
        "color": COLOR_RED,
        "description": (
            f"Exit code: `{exit_code}`\n```\n{error_msg[:500]}\n```"
            if error_msg else f"Exit code: `{exit_code}`"
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "trappist v2.0 • error"},
    }


# ---------------------------------------------------------------------------
# Send webhook
# ---------------------------------------------------------------------------

def send_discord(embed: dict, webhook_url: str | None = None) -> bool:
    """Send an embed to Discord webhook."""
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not url:
        print("No DISCORD_WEBHOOK_URL configured", file=sys.stderr)
        return False

    payload = {"embeds": [embed]}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Discord send failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TRAPPIST Discord notifications")
    parser.add_argument("--run-type", default="cycle", choices=["cycle", "protect", "error"])
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--cost", type=float, default=0)
    parser.add_argument("--turns", type=int, default=0)
    parser.add_argument("--error-msg", default="")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    if args.test:
        embed = {
            "title": "\U0001fa90 TRAPPIST Test Notification",
            "color": COLOR_BLUE,
            "description": "Bot connected and ready to trade.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    elif args.exit_code != 0 and args.run_type != "protect":
        embed = _build_error_embed(args.run_type, args.exit_code, args.error_msg)
    elif args.run_type == "cycle":
        embed = _build_cycle_embed(args.exit_code, args.cost, args.turns)
    elif args.run_type == "protect":
        embed = _build_protect_embed(args.exit_code)
    else:
        embed = _build_error_embed(args.run_type, args.exit_code, args.error_msg)

    success = send_discord(embed, args.webhook_url)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
