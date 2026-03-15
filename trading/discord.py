#!/usr/bin/env python3
"""Discord webhook notifications for trading pipeline.

Sends rich embed messages adapted per run type (open/close/protect).
Called by entrypoint.sh after each Scaleway job completes.

Usage:
  python trading/discord.py --run-type open --exit-code 0 --cost 7.78 --turns 188
  python trading/discord.py --run-type close --exit-code 0
  python trading/discord.py --run-type protect --exit-code 0
  python trading/discord.py --test  # send a test embed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading.client import get_account, get_clock, get_open_orders, get_positions

# ---------------------------------------------------------------------------
# Colors & formatting
# ---------------------------------------------------------------------------

COLOR_GREEN = 0x2ECC71   # success / profit
COLOR_RED = 0xE74C3C     # error / loss
COLOR_ORANGE = 0xF39C12  # warning
COLOR_BLUE = 0x3498DB    # info (protect)
COLOR_PURPLE = 0x9B59B6  # close session


def _pnl_emoji(value: float) -> str:
    if value > 0:
        return "\U0001f7e2"  # green circle
    elif value < 0:
        return "\U0001f534"  # red circle
    return "\u26aa"  # white circle


def _format_money(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}${value:,.2f}"


def _format_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _side_emoji(side: str) -> str:
    return "\U0001f4c8" if "long" in side.lower() else "\U0001f4c9"  # chart up/down


# ---------------------------------------------------------------------------
# Data gathering (fail-safe — never crash the notification)
# ---------------------------------------------------------------------------

def _safe_account() -> dict | None:
    try:
        return get_account()
    except Exception:
        return None


def _safe_positions() -> list[dict]:
    try:
        return get_positions()
    except Exception:
        return []


def _safe_orders() -> list[dict]:
    try:
        return get_open_orders()
    except Exception:
        return []


def _safe_clock() -> dict | None:
    try:
        return get_clock()
    except Exception:
        return None


def _load_metrics(run_type: str) -> dict | None:
    """Try to load the most recent session metrics file."""
    logs_dir = Path("/app/logs")
    if not logs_dir.exists():
        logs_dir = Path("logs")
    if not logs_dir.exists():
        return None
    candidates = sorted(logs_dir.glob(f"session_metrics_{run_type}_*.json"), reverse=True)
    if not candidates:
        return None
    try:
        return json.loads(candidates[0].read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Embed builders per run type
# ---------------------------------------------------------------------------

def _build_open_embed(exit_code: int, cost: float, turns: int) -> dict:
    """Market open session — trades placed, portfolio state."""
    acct = _safe_account()
    positions = _safe_positions()
    orders = _safe_orders()

    success = exit_code == 0
    color = COLOR_GREEN if success else COLOR_RED

    # Account line
    fields = []
    if acct:
        equity = acct["equity"]
        daily_pnl = equity - acct["last_equity"]
        pnl_pct = (daily_pnl / acct["last_equity"] * 100) if acct["last_equity"] else 0
        fields.append({
            "name": "\U0001f4b0 Portfolio",
            "value": (
                f"**${equity:,.2f}**\n"
                f"{_pnl_emoji(daily_pnl)} Day: {_format_money(daily_pnl)} ({_format_pct(pnl_pct)})"
            ),
            "inline": True,
        })
        # Buying power
        fields.append({
            "name": "\U0001f4b3 Buying Power",
            "value": f"${acct['buying_power']:,.2f}",
            "inline": True,
        })

    # Positions summary
    if positions:
        total_unrealized = sum(p["unrealized_pl"] for p in positions)
        exposure = sum(abs(p["market_value"]) for p in positions)
        exposure_pct = (exposure / acct["equity"] * 100) if acct else 0
        pos_lines = []
        for p in positions:
            emoji = _side_emoji(p["side"])
            pl_emoji = _pnl_emoji(p["unrealized_pl"])
            pos_lines.append(
                f"{emoji} **{p['symbol']}** {int(p['qty'])}x "
                f"@ ${p['avg_entry_price']:.2f} "
                f"{pl_emoji} {_format_money(p['unrealized_pl'])}"
            )
        fields.append({
            "name": f"\U0001f4ca Positions ({len(positions)})",
            "value": "\n".join(pos_lines[:8]),  # max 8
            "inline": False,
        })
        fields.append({
            "name": "Exposure",
            "value": f"${exposure:,.0f} ({exposure_pct:.1f}%)",
            "inline": True,
        })
        fields.append({
            "name": "Unrealized P&L",
            "value": f"{_pnl_emoji(total_unrealized)} {_format_money(total_unrealized)}",
            "inline": True,
        })

    # Pending orders
    bracket_orders = [o for o in orders if o.get("order_class") == "OrderClass.BRACKET"]
    if bracket_orders:
        order_lines = []
        for o in bracket_orders[:5]:
            side = "\U0001f4c8 LONG" if "buy" in o["side"].lower() else "\U0001f4c9 SHORT"
            price_str = f"@ ${float(o['limit_price']):,.2f}" if o.get("limit_price") else "MKT"
            order_lines.append(f"{side} **{o['symbol']}** {o['qty']}x {price_str}")
        fields.append({
            "name": f"\u23f3 Pending Orders ({len(bracket_orders)})",
            "value": "\n".join(order_lines),
            "inline": False,
        })

    # Session cost
    fields.append({
        "name": "\u2699\ufe0f Session",
        "value": f"${cost:.2f} \u2022 {turns} turns",
        "inline": True,
    })

    title = "\u2705 Market Open \u2014 Trades Placed" if success else "\u274c Market Open \u2014 Failed"
    return {
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "claude-trading v2.2"},
    }


def _build_close_embed(exit_code: int, cost: float, turns: int) -> dict:
    """Market close session — daily review, P&L recap."""
    acct = _safe_account()
    positions = _safe_positions()

    success = exit_code == 0
    color = COLOR_PURPLE if success else COLOR_RED

    fields = []
    if acct:
        equity = acct["equity"]
        daily_pnl = equity - acct["last_equity"]
        pnl_pct = (daily_pnl / acct["last_equity"] * 100) if acct["last_equity"] else 0
        emoji = _pnl_emoji(daily_pnl)
        fields.append({
            "name": f"{emoji} Daily P&L",
            "value": f"**{_format_money(daily_pnl)}** ({_format_pct(pnl_pct)})",
            "inline": True,
        })
        fields.append({
            "name": "\U0001f4b0 Equity",
            "value": f"**${equity:,.2f}**",
            "inline": True,
        })

    # Positions held overnight
    if positions:
        pos_lines = []
        for p in positions:
            emoji = _side_emoji(p["side"])
            pl_emoji = _pnl_emoji(p["unrealized_pl"])
            pct = p["unrealized_plpc"] * 100
            pos_lines.append(
                f"{emoji} **{p['symbol']}** "
                f"{pl_emoji} {_format_money(p['unrealized_pl'])} ({_format_pct(pct)})"
            )
        fields.append({
            "name": f"\U0001f319 Overnight ({len(positions)})",
            "value": "\n".join(pos_lines[:8]),
            "inline": False,
        })
    else:
        fields.append({
            "name": "\U0001f319 Overnight",
            "value": "All flat \u2014 no positions held",
            "inline": False,
        })

    if cost > 0:
        fields.append({
            "name": "\u2699\ufe0f Session",
            "value": f"${cost:.2f} \u2022 {turns} turns",
            "inline": True,
        })

    title = "\U0001f319 Market Close \u2014 Day Complete" if success else "\u274c Market Close \u2014 Failed"
    return {
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "claude-trading v2.2"},
    }


def _build_protect_embed(exit_code: int) -> dict:
    """Post-open protection check — OCO status."""
    positions = _safe_positions()
    orders = _safe_orders()

    success = exit_code == 0

    # Check which positions have OCO/bracket protection
    order_symbols = set()
    for o in orders:
        if o.get("legs"):
            order_symbols.add(o["symbol"])
        if o.get("order_class") in ("OrderClass.OCO", "OrderClass.BRACKET"):
            order_symbols.add(o["symbol"])

    protected = []
    naked = []
    for p in positions:
        if p["symbol"] in order_symbols:
            protected.append(p["symbol"])
        else:
            naked.append(p["symbol"])

    if naked:
        color = COLOR_RED
        status = f"\u26a0\ufe0f {len(naked)} UNPROTECTED"
    elif success:
        color = COLOR_GREEN
        status = "\u2705 All positions protected"
    else:
        color = COLOR_ORANGE
        status = "\u26a0\ufe0f Check completed with warnings"

    fields = [{
        "name": "\U0001f6e1\ufe0f Protection Status",
        "value": status,
        "inline": False,
    }]

    if protected:
        fields.append({
            "name": f"\u2705 Protected ({len(protected)})",
            "value": " \u2022 ".join(protected),
            "inline": True,
        })
    if naked:
        fields.append({
            "name": f"\u274c Naked ({len(naked)})",
            "value": " \u2022 ".join(naked),
            "inline": True,
        })

    if not positions:
        fields = [{"name": "\U0001f6e1\ufe0f Protection", "value": "No open positions", "inline": False}]
        color = COLOR_BLUE

    title = "\U0001f6e1\ufe0f Post-Open Protection"
    return {
        "title": title,
        "color": color,
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "claude-trading v2.2"},
    }


def _build_error_embed(run_type: str, exit_code: int, error_msg: str = "") -> dict:
    """Generic error notification when a job crashes."""
    desc = f"Job `{run_type}` exited with code **{exit_code}**"
    if error_msg:
        desc += f"\n```\n{error_msg[:500]}\n```"
    return {
        "title": f"\U0001f6a8 Pipeline Error \u2014 {run_type}",
        "description": desc,
        "color": COLOR_RED,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "claude-trading v2.2"},
    }


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_discord(webhook_url: str, embed: dict) -> bool:
    """Send a single embed to Discord. Returns True on success."""
    payload = {"embeds": [embed]}
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Discord webhook failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Discord trading notifications")
    parser.add_argument("--run-type", choices=["open", "close", "protect"], default="open")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--cost", type=float, default=0.0)
    parser.add_argument("--turns", type=int, default=0)
    parser.add_argument("--error", type=str, default="")
    parser.add_argument("--test", action="store_true", help="Send test notification")
    parser.add_argument("--webhook-url", type=str, default="")
    args = parser.parse_args()

    webhook_url = args.webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("No DISCORD_WEBHOOK_URL set", file=sys.stderr)
        return 1

    if args.test:
        embed = {
            "title": "\U0001f9ea Test Notification",
            "description": "Pipeline Discord notifications are working.",
            "color": COLOR_BLUE,
            "fields": [
                {"name": "Status", "value": "\u2705 Connected", "inline": True},
                {"name": "Timestamp", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
            ],
            "footer": {"text": "claude-trading v2.2"},
        }
        ok = send_discord(webhook_url, embed)
        return 0 if ok else 1

    # Non-zero exit → error embed (still try to get portfolio data)
    if args.exit_code != 0 and args.run_type != "protect":
        embed = _build_error_embed(args.run_type, args.exit_code, args.error)
    elif args.run_type == "open":
        embed = _build_open_embed(args.exit_code, args.cost, args.turns)
    elif args.run_type == "close":
        embed = _build_close_embed(args.exit_code, args.cost, args.turns)
    elif args.run_type == "protect":
        embed = _build_protect_embed(args.exit_code)
    else:
        embed = _build_error_embed(args.run_type, args.exit_code, "Unknown run type")

    ok = send_discord(webhook_url, embed)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
