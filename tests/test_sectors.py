"""Tests for trading/sectors.py — GICS sector mapping & concentration enforcement."""

from trading.sectors import SECTOR_MAP, MAX_PER_SECTOR, get_sector, check_sector_limit


class TestGetSector:
    def test_known_ticker(self):
        assert get_sector("AAPL") == "Technology"
        assert get_sector("XOM") == "Energy"
        assert get_sector("NEE") == "Utilities"
        assert get_sector("JPM") == "Financials"

    def test_case_insensitive(self):
        assert get_sector("aapl") == "Technology"
        assert get_sector("Xom") == "Energy"

    def test_unknown_ticker(self):
        assert get_sector("ZZZZZ") == "Unknown"
        assert get_sector("") == "Unknown"

    def test_all_sectors_represented(self):
        sectors = set(SECTOR_MAP.values())
        expected = {
            "Energy", "Technology", "Communication Services",
            "Consumer Discretionary", "Consumer Staples", "Financials",
            "Healthcare", "Industrials", "Materials", "Real Estate", "Utilities",
        }
        assert sectors == expected

    def test_max_per_sector_is_two(self):
        assert MAX_PER_SECTOR == 2


class TestCheckSectorLimit:
    def test_empty_portfolio_allows(self):
        allowed, reason = check_sector_limit("AAPL", [], None)
        assert allowed is True
        assert "0/2" in reason

    def test_one_position_allows_second(self):
        positions = [{"symbol": "MSFT"}]
        allowed, reason = check_sector_limit("AAPL", positions, None)
        assert allowed is True
        assert "1/2" in reason

    def test_two_positions_blocks_third(self):
        positions = [{"symbol": "MSFT"}, {"symbol": "NVDA"}]
        allowed, reason = check_sector_limit("AAPL", positions, None)
        assert allowed is False
        assert "BLOCKED" in reason
        assert "Technology" in reason

    def test_different_sector_allowed(self):
        positions = [{"symbol": "MSFT"}, {"symbol": "NVDA"}]
        allowed, reason = check_sector_limit("XOM", positions, None)
        assert allowed is True

    def test_pending_orders_counted(self):
        positions = [{"symbol": "XOM"}]
        pending = [{"symbol": "CVX"}]
        allowed, reason = check_sector_limit("COP", positions, pending)
        assert allowed is False
        assert "Energy" in reason

    def test_pending_orders_no_double_count(self):
        """Same symbol in positions and orders should not double-count."""
        positions = [{"symbol": "XOM"}]
        pending = [{"symbol": "XOM"}]  # same symbol
        allowed, reason = check_sector_limit("CVX", positions, pending)
        assert allowed is True
        assert "1/2" in reason

    def test_custom_max_per_sector(self):
        positions = [{"symbol": "AAPL"}]
        allowed, _ = check_sector_limit("MSFT", positions, None, max_per_sector=1)
        assert allowed is False

    def test_unknown_sector_blocked(self):
        """Unknown tickers are blocked immediately (fail-closed)."""
        positions = []
        allowed, reason = check_sector_limit("XXXXX", positions, None)
        assert allowed is False
        assert "unknown sector mapping" in reason
        assert "XXXXX" in reason

    def test_energy_disaster_scenario(self):
        """Reproduce the Disaster 1 scenario: 4/5 energy trades should be blocked."""
        positions = [{"symbol": "COP"}, {"symbol": "XOM"}]
        allowed, _ = check_sector_limit("CVX", positions)
        assert allowed is False
        allowed, _ = check_sector_limit("OXY", positions)
        assert allowed is False
        # Non-energy should still be fine
        allowed, _ = check_sector_limit("AAPL", positions)
        assert allowed is True
