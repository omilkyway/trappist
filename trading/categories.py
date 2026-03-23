"""Crypto category mapping for programmatic concentration enforcement.

Maps crypto tickers to market categories (replaces GICS sectors for stocks).
Used by executor.py to enforce max-per-category limits BEFORE order placement.
"""

from __future__ import annotations

# Crypto tickers mapped to market categories.
# Format: base symbol (without /USDT:USDT suffix) → category.
CATEGORY_MAP: dict[str, str] = {
    # Store of Value / Digital Gold
    "BTC": "Store of Value",

    # Smart Contract Platforms (Layer 1)
    "ETH": "Smart Contract L1", "SOL": "Smart Contract L1",
    "AVAX": "Smart Contract L1", "ADA": "Smart Contract L1",
    "DOT": "Smart Contract L1", "NEAR": "Smart Contract L1",
    "SUI": "Smart Contract L1", "APT": "Smart Contract L1",
    "ATOM": "Smart Contract L1", "ICP": "Smart Contract L1",
    "FTM": "Smart Contract L1", "SEI": "Smart Contract L1",
    "INJ": "Smart Contract L1", "TIA": "Smart Contract L1",
    "TON": "Smart Contract L1", "TRX": "Smart Contract L1",
    "ALGO": "Smart Contract L1", "HBAR": "Smart Contract L1",
    "EOS": "Smart Contract L1",

    # Layer 2 / Scaling
    "ARB": "Layer 2", "OP": "Layer 2", "MATIC": "Layer 2",
    "POL": "Layer 2", "MNT": "Layer 2", "STRK": "Layer 2",
    "ZK": "Layer 2", "METIS": "Layer 2", "IMX": "Layer 2",

    # Exchange Tokens
    "BNB": "Exchange Token", "OKB": "Exchange Token",
    "CRO": "Exchange Token", "LEO": "Exchange Token",
    "GT": "Exchange Token", "MX": "Exchange Token",

    # DeFi
    "LINK": "DeFi", "UNI": "DeFi", "AAVE": "DeFi",
    "MKR": "DeFi", "SNX": "DeFi", "CRV": "DeFi",
    "LDO": "DeFi", "DYDX": "DeFi", "COMP": "DeFi",
    "SUSHI": "DeFi", "1INCH": "DeFi", "JUP": "DeFi",
    "PENDLE": "DeFi", "ENA": "DeFi", "PYTH": "DeFi",
    "W": "DeFi", "JTO": "DeFi", "RAY": "DeFi",
    "SIREN": "DeFi", "QUICK": "DeFi",

    # Meme
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme",
    "WIF": "Meme", "BONK": "Meme", "FLOKI": "Meme",
    "MEME": "Meme", "PEOPLE": "Meme", "TURBO": "Meme",
    "BRETT": "Meme", "NEIRO": "Meme", "BOME": "Meme",
    "MYRO": "Meme", "1000X": "Meme", "CUDIS": "Meme",

    # AI / Compute
    "RENDER": "AI", "FET": "AI", "AGIX": "AI",
    "OCEAN": "AI", "AKT": "AI", "TAO": "AI",
    "WLD": "AI", "ARKM": "AI", "RNDR": "AI",
    "AR": "AI", "VIRTUAL": "AI", "AI16Z": "AI",
    "GRIFFAIN": "AI", "COOKIE": "AI",

    # Payment / Transfer
    "XRP": "Payment", "XLM": "Payment", "LTC": "Payment",
    "BCH": "Payment", "XMR": "Payment", "DASH": "Payment",
    "ZEC": "Payment",

    # Gaming / Metaverse
    "AXS": "Gaming", "GALA": "Gaming", "SAND": "Gaming",
    "MANA": "Gaming", "ENJ": "Gaming", "PIXEL": "Gaming",
    "RONIN": "Gaming", "YGG": "Gaming", "VOXEL": "Gaming",

    # Infrastructure / Storage
    "FIL": "Infrastructure", "GRT": "Infrastructure",
    "STX": "Infrastructure", "THETA": "Infrastructure",
    "ROSE": "Infrastructure", "ONDO": "Infrastructure",
    "TOKEN": "Infrastructure",

    # Misc tokens frequently scanned on Binance Futures
    "BR": "DeFi", "ARIA": "AI", "JCT": "Infrastructure",

    # Staking / Liquid Staking
    "EIGEN": "Staking", "ETHFI": "Staking",
    "SSV": "Staking", "RPL": "Staking",
}

# Maximum trades per category (enforced programmatically)
MAX_PER_CATEGORY = 3


def normalize_symbol(symbol: str) -> str:
    """Extract base token from various symbol formats.

    Examples:
        'BTC/USDT:USDT' → 'BTC'
        'ETHUSDT' → 'ETH'
        'SOL' → 'SOL'
    """
    s = symbol.upper().strip()
    # CCXT format: BTC/USDT:USDT
    if "/" in s:
        s = s.split("/")[0]
    # Binance format: BTCUSDT
    for suffix in ("USDT", "BUSD", "USD"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    return s


def get_category(symbol: str) -> str:
    """Return the market category for a symbol, or 'Other' if not mapped."""
    base = normalize_symbol(symbol)
    return CATEGORY_MAP.get(base, "Other")


def check_category_limit(
    new_symbol: str,
    existing_positions: list[dict],
    pending_orders: list[dict] | None = None,
    max_per_category: int = MAX_PER_CATEGORY,
) -> tuple[bool, str]:
    """Check if adding a new trade would violate category concentration limits.

    Returns (allowed, reason).
    """
    new_cat = get_category(new_symbol)

    # Unknown category = allowed but tracked as "Other"
    if new_cat == "Unknown":
        new_cat = "Other"

    # Count existing positions in same category
    count = 0
    symbols_in_cat: list[str] = []
    for pos in existing_positions:
        sym = pos.get("symbol", "")
        if get_category(sym) == new_cat:
            count += 1
            symbols_in_cat.append(sym)

    # Count pending orders in same category
    if pending_orders:
        for order in pending_orders:
            sym = order.get("symbol", "")
            if get_category(sym) == new_cat and sym not in symbols_in_cat:
                count += 1
                symbols_in_cat.append(sym)

    if count >= max_per_category:
        return False, (
            f"BLOCKED: category '{new_cat}' already has {count} position(s) "
            f"({', '.join(symbols_in_cat)}), max {max_per_category}"
        )

    return True, f"OK: category '{new_cat}' has {count}/{max_per_category}"
