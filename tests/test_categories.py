"""Tests for trading/categories.py"""

from trading.categories import (
    CATEGORY_MAP,
    MAX_PER_CATEGORY,
    check_category_limit,
    get_category,
    normalize_symbol,
)


class TestNormalizeSymbol:
    def test_ccxt_format(self):
        assert normalize_symbol("BTC/USDT:USDT") == "BTC"

    def test_binance_format(self):
        assert normalize_symbol("ETHUSDT") == "ETH"

    def test_bare_symbol(self):
        assert normalize_symbol("SOL") == "SOL"

    def test_lowercase(self):
        assert normalize_symbol("btc/usdt:usdt") == "BTC"

    def test_with_spaces(self):
        assert normalize_symbol("  DOGE  ") == "DOGE"


class TestGetCategory:
    def test_known_symbols(self):
        assert get_category("BTC") == "Store of Value"
        assert get_category("ETH") == "Smart Contract L1"
        assert get_category("DOGE") == "Meme"
        assert get_category("LINK") == "DeFi"
        assert get_category("ARB") == "Layer 2"
        assert get_category("BNB") == "Exchange Token"
        assert get_category("FET") == "AI"
        assert get_category("XRP") == "Payment"

    def test_ccxt_format_input(self):
        assert get_category("BTC/USDT:USDT") == "Store of Value"
        assert get_category("ETH/USDT:USDT") == "Smart Contract L1"

    def test_unknown_symbol(self):
        assert get_category("FAKETOKEN") == "Unknown"

    def test_all_mapped_symbols_have_category(self):
        for sym, cat in CATEGORY_MAP.items():
            assert cat != "Unknown", f"{sym} mapped to Unknown"
            assert len(cat) > 0, f"{sym} has empty category"


class TestCheckCategoryLimit:
    def test_empty_portfolio_allows(self):
        ok, reason = check_category_limit("BTC/USDT:USDT", [])
        assert ok
        assert "0/3" in reason

    def test_blocks_unknown_symbol(self):
        ok, reason = check_category_limit("FAKETOKEN", [])
        assert not ok
        assert "BLOCKED" in reason
        assert "unknown category" in reason

    def test_allows_different_category(self):
        positions = [{"symbol": "BTC/USDT:USDT"}]  # Store of Value
        ok, _ = check_category_limit("ETH/USDT:USDT", positions)  # Smart Contract L1
        assert ok

    def test_blocks_same_category_at_limit(self):
        positions = [
            {"symbol": "ETH/USDT:USDT"},
            {"symbol": "SOL/USDT:USDT"},
            {"symbol": "AVAX/USDT:USDT"},
        ]  # 3 Smart Contract L1 = max
        ok, reason = check_category_limit("SUI/USDT:USDT", positions)
        assert not ok
        assert "BLOCKED" in reason
        assert "Smart Contract L1" in reason

    def test_counts_pending_orders(self):
        positions = [{"symbol": "ETH/USDT:USDT"}, {"symbol": "SOL/USDT:USDT"}]
        pending = [{"symbol": "AVAX/USDT:USDT"}]
        ok, _ = check_category_limit("SUI/USDT:USDT", positions, pending)
        assert not ok

    def test_custom_max(self):
        positions = [{"symbol": "ETH/USDT:USDT"}]
        ok, _ = check_category_limit("SOL/USDT:USDT", positions, max_per_category=1)
        assert not ok

    def test_default_max_is_three(self):
        assert MAX_PER_CATEGORY == 3
