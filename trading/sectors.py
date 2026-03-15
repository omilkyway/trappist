"""GICS sector mapping for programmatic sector enforcement.

Maps common US equity tickers to their GICS sector.
Used by executor.py to enforce max-per-sector limits BEFORE order placement.
"""

from __future__ import annotations

# S&P 500 + common tickers mapped to GICS sectors
# This covers ~95% of tradable names. Unknown tickers return "Unknown".
SECTOR_MAP: dict[str, str] = {
    # Energy
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy", "EOG": "Energy",
    "SLB": "Energy", "MPC": "Energy", "PSX": "Energy", "VLO": "Energy",
    "OXY": "Energy", "PXD": "Energy", "HES": "Energy", "DVN": "Energy",
    "HAL": "Energy", "FANG": "Energy", "BKR": "Energy", "CTRA": "Energy",
    "MRO": "Energy", "APA": "Energy", "OVV": "Energy", "AR": "Energy",
    "EQT": "Energy", "RRC": "Energy", "SM": "Energy", "MTDR": "Energy",
    "CHRD": "Energy", "MGY": "Energy", "PR": "Energy", "DINO": "Energy",

    # Technology
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AVGO": "Technology", "ORCL": "Technology", "CRM": "Technology",
    "AMD": "Technology", "ADBE": "Technology", "CSCO": "Technology",
    "ACN": "Technology", "INTC": "Technology", "IBM": "Technology",
    "QCOM": "Technology", "TXN": "Technology", "INTU": "Technology",
    "AMAT": "Technology", "NOW": "Technology", "LRCX": "Technology",
    "ADI": "Technology", "MU": "Technology", "KLAC": "Technology",
    "SNPS": "Technology", "CDNS": "Technology", "MRVL": "Technology",
    "FTNT": "Technology", "PANW": "Technology", "CRWD": "Technology",
    "MSI": "Technology", "NXPI": "Technology", "ON": "Technology",
    "HPQ": "Technology", "HPE": "Technology", "DELL": "Technology",
    "PLTR": "Technology", "SMCI": "Technology", "ARM": "Technology",
    "MCHP": "Technology", "SWKS": "Technology", "MPWR": "Technology",
    "KEYS": "Technology", "TER": "Technology", "ENTG": "Technology",

    # Communication Services
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "META": "Communication Services", "NFLX": "Communication Services",
    "DIS": "Communication Services", "CMCSA": "Communication Services",
    "T": "Communication Services", "VZ": "Communication Services",
    "TMUS": "Communication Services", "CHTR": "Communication Services",
    "EA": "Communication Services", "TTWO": "Communication Services",
    "WBD": "Communication Services", "PARA": "Communication Services",
    "RBLX": "Communication Services", "SPOT": "Communication Services",
    "SNAP": "Communication Services", "PINS": "Communication Services",

    # Consumer Discretionary
    "AMZN": "Consumer Discretionary", "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary",
    "NKE": "Consumer Discretionary", "LOW": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary", "TJX": "Consumer Discretionary",
    "BKNG": "Consumer Discretionary", "CMG": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary", "ORLY": "Consumer Discretionary",
    "AZO": "Consumer Discretionary", "ROST": "Consumer Discretionary",
    "MAR": "Consumer Discretionary", "HLT": "Consumer Discretionary",
    "GM": "Consumer Discretionary", "F": "Consumer Discretionary",
    "DHI": "Consumer Discretionary", "LEN": "Consumer Discretionary",
    "PHM": "Consumer Discretionary", "CCL": "Consumer Discretionary",
    "RCL": "Consumer Discretionary", "NCLH": "Consumer Discretionary",
    "WYNN": "Consumer Discretionary", "LVS": "Consumer Discretionary",
    "MGM": "Consumer Discretionary", "EBAY": "Consumer Discretionary",
    "ETSY": "Consumer Discretionary", "DECK": "Consumer Discretionary",
    "ULTA": "Consumer Discretionary", "BBY": "Consumer Discretionary",
    "DRI": "Consumer Discretionary", "YUM": "Consumer Discretionary",
    "POOL": "Consumer Discretionary", "GPC": "Consumer Discretionary",

    # Consumer Staples
    "PG": "Consumer Staples", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "COST": "Consumer Staples", "WMT": "Consumer Staples", "PM": "Consumer Staples",
    "MO": "Consumer Staples", "MDLZ": "Consumer Staples", "CL": "Consumer Staples",
    "KMB": "Consumer Staples", "GIS": "Consumer Staples", "SJM": "Consumer Staples",
    "HSY": "Consumer Staples", "K": "Consumer Staples", "KHC": "Consumer Staples",
    "STZ": "Consumer Staples", "ADM": "Consumer Staples", "BG": "Consumer Staples",
    "CAG": "Consumer Staples", "CPB": "Consumer Staples", "TSN": "Consumer Staples",
    "HRL": "Consumer Staples", "EL": "Consumer Staples", "CLX": "Consumer Staples",
    "KR": "Consumer Staples", "SYY": "Consumer Staples", "TGT": "Consumer Staples",

    # Financials
    "BRK.B": "Financials", "JPM": "Financials", "V": "Financials",
    "MA": "Financials", "BAC": "Financials", "WFC": "Financials",
    "GS": "Financials", "MS": "Financials", "SPGI": "Financials",
    "BLK": "Financials", "C": "Financials", "SCHW": "Financials",
    "AXP": "Financials", "CB": "Financials", "MMC": "Financials",
    "PGR": "Financials", "AON": "Financials", "USB": "Financials",
    "CME": "Financials", "ICE": "Financials", "MCO": "Financials",
    "PNC": "Financials", "TFC": "Financials", "AIG": "Financials",
    "MET": "Financials", "PRU": "Financials", "AFL": "Financials",
    "ALL": "Financials", "TRV": "Financials", "FITB": "Financials",
    "COF": "Financials", "DFS": "Financials", "SYF": "Financials",
    "COIN": "Financials", "HOOD": "Financials",

    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "LLY": "Healthcare",
    "ABBV": "Healthcare", "MRK": "Healthcare", "PFE": "Healthcare",
    "TMO": "Healthcare", "ABT": "Healthcare", "DHR": "Healthcare",
    "BMY": "Healthcare", "AMGN": "Healthcare", "GILD": "Healthcare",
    "ISRG": "Healthcare", "MDT": "Healthcare", "CI": "Healthcare",
    "ELV": "Healthcare", "SYK": "Healthcare", "BSX": "Healthcare",
    "VRTX": "Healthcare", "REGN": "Healthcare", "ZTS": "Healthcare",
    "BDX": "Healthcare", "HCA": "Healthcare", "MCK": "Healthcare",
    "CVS": "Healthcare", "EW": "Healthcare", "A": "Healthcare",
    "DXCM": "Healthcare", "IDXX": "Healthcare", "IQV": "Healthcare",
    "MRNA": "Healthcare", "BIIB": "Healthcare", "GEHC": "Healthcare",

    # Industrials
    "GE": "Industrials", "CAT": "Industrials", "UNP": "Industrials",
    "HON": "Industrials", "UPS": "Industrials", "BA": "Industrials",
    "RTX": "Industrials", "DE": "Industrials", "LMT": "Industrials",
    "GD": "Industrials", "NOC": "Industrials", "MMM": "Industrials",
    "WM": "Industrials", "ETN": "Industrials", "ITW": "Industrials",
    "EMR": "Industrials", "FDX": "Industrials", "CSX": "Industrials",
    "NSC": "Industrials", "PH": "Industrials", "PCAR": "Industrials",
    "TT": "Industrials", "CARR": "Industrials", "GEV": "Industrials",
    "CTAS": "Industrials", "ROK": "Industrials", "FAST": "Industrials",
    "VRSK": "Industrials", "PWR": "Industrials", "DAL": "Industrials",
    "UAL": "Industrials", "LUV": "Industrials", "AAL": "Industrials",
    "UBER": "Industrials", "LYFT": "Industrials",

    # Materials
    "LIN": "Materials", "APD": "Materials", "SHW": "Materials",
    "FCX": "Materials", "NUE": "Materials", "NEM": "Materials",
    "ECL": "Materials", "DOW": "Materials", "DD": "Materials",
    "PPG": "Materials", "VMC": "Materials", "MLM": "Materials",
    "CTVA": "Materials", "ALB": "Materials", "CF": "Materials",
    "MOS": "Materials", "IFF": "Materials", "GOLD": "Materials",
    "AEM": "Materials", "RGLD": "Materials", "FNV": "Materials",
    "WPM": "Materials", "STLD": "Materials", "RS": "Materials",
    "X": "Materials", "CLF": "Materials", "AA": "Materials",

    # Real Estate
    "PLD": "Real Estate", "AMT": "Real Estate", "CCI": "Real Estate",
    "EQIX": "Real Estate", "PSA": "Real Estate", "DLR": "Real Estate",
    "O": "Real Estate", "SPG": "Real Estate", "WELL": "Real Estate",
    "VICI": "Real Estate", "AVB": "Real Estate", "EQR": "Real Estate",
    "ARE": "Real Estate", "MAA": "Real Estate", "IRM": "Real Estate",
    "VTR": "Real Estate", "SBAC": "Real Estate", "WPC": "Real Estate",

    # Utilities
    "NEE": "Utilities", "SO": "Utilities", "DUK": "Utilities",
    "CEG": "Utilities", "SRE": "Utilities", "AEP": "Utilities",
    "D": "Utilities", "PCG": "Utilities", "EXC": "Utilities",
    "XEL": "Utilities", "ED": "Utilities", "WEC": "Utilities",
    "ES": "Utilities", "AWK": "Utilities", "DTE": "Utilities",
    "ETR": "Utilities", "FE": "Utilities", "PPL": "Utilities",
    "AES": "Utilities", "CMS": "Utilities", "CNP": "Utilities",
    "VST": "Utilities", "NRG": "Utilities", "EVRG": "Utilities",
}

# Maximum trades per sector (enforced programmatically)
MAX_PER_SECTOR = 2


def get_sector(symbol: str) -> str:
    """Return the GICS sector for a symbol, or 'Unknown' if not mapped."""
    return SECTOR_MAP.get(symbol.upper(), "Unknown")


def check_sector_limit(
    new_symbol: str,
    existing_positions: list[dict],
    pending_orders: list[dict] | None = None,
    max_per_sector: int = MAX_PER_SECTOR,
) -> tuple[bool, str]:
    """Check if adding a new trade would violate sector concentration limits.

    Returns (allowed, reason).
    """
    new_sector = get_sector(new_symbol)

    # Fail-closed: unknown sector = blocked (prevents untracked concentration)
    if new_sector == "Unknown":
        return False, (
            f"BLOCKED: ticker '{new_symbol}' has unknown sector mapping. "
            f"Add it to SECTOR_MAP in sectors.py before trading."
        )

    # Count existing positions in same sector
    count = 0
    symbols_in_sector = []
    for pos in existing_positions:
        sym = pos.get("symbol", "")
        if get_sector(sym) == new_sector:
            count += 1
            symbols_in_sector.append(sym)

    # Count pending orders in same sector
    if pending_orders:
        for order in pending_orders:
            sym = order.get("symbol", "")
            if get_sector(sym) == new_sector and sym not in symbols_in_sector:
                count += 1
                symbols_in_sector.append(sym)

    if count >= max_per_sector:
        return False, (
            f"BLOCKED: sector '{new_sector}' already has {count} position(s) "
            f"({', '.join(symbols_in_sector)}), max {max_per_sector}"
        )

    return True, f"OK: sector '{new_sector}' has {count}/{max_per_sector}"
