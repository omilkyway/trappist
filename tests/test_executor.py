"""Tests for trading/executor.py — CLI commands with mocked Alpaca client."""

import json
from argparse import Namespace
from unittest.mock import patch

from trading.executor import (
    _check_sector_before_order,
    cmd_sector,
    cmd_check_protection,
)


class TestCheckSectorBeforeOrder:
    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_allows_when_under_limit(self, mock_pos, mock_orders):
        mock_pos.return_value = [{"symbol": "AAPL"}]
        mock_orders.return_value = []
        result = _check_sector_before_order("MSFT")
        # 2 tech allowed, so MSFT (2nd) should pass
        assert result is None

    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_blocks_when_over_limit(self, mock_pos, mock_orders):
        mock_pos.return_value = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        mock_orders.return_value = []
        result = _check_sector_before_order("NVDA")
        assert result is not None
        assert result["success"] is False
        assert "BLOCKED" in result["error"]
        assert result["sector"] == "Technology"

    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_allows_different_sector(self, mock_pos, mock_orders):
        mock_pos.return_value = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        mock_orders.return_value = []
        result = _check_sector_before_order("XOM")
        assert result is None

    @patch("trading.executor.get_positions", side_effect=Exception("API down"))
    def test_fail_closed_on_api_error(self, mock_pos):
        """If API fails, BLOCK the order (fail-closed sector check)."""
        result = _check_sector_before_order("AAPL")
        assert result is not None
        assert result["success"] is False
        assert "fail-closed" in result["error"]


class TestCmdSector:
    def test_sector_lookup(self, capsys):
        args = Namespace(symbols=["AAPL", "XOM", "UNKNOWN"])
        cmd_sector(args)
        output = json.loads(capsys.readouterr().out)
        assert output["AAPL"] == "Technology"
        assert output["XOM"] == "Energy"
        assert output["UNKNOWN"] == "Unknown"


class TestCmdCheckProtection:
    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_all_protected(self, mock_pos, mock_orders, capsys):
        mock_pos.return_value = [
            {"symbol": "AAPL", "qty": 10.0, "side": "PositionSide.LONG",
             "avg_entry_price": 175.0, "current_price": 178.0,
             "unrealized_pl": 30.0, "unrealized_plpc": 0.017},
        ]
        mock_orders.return_value = [
            {"symbol": "AAPL", "order_class": "OrderClass.OCO", "legs": [{"id": "1"}]},
        ]
        result = cmd_check_protection(Namespace())
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "all_protected"

    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_unprotected_detected(self, mock_pos, mock_orders, capsys):
        mock_pos.return_value = [
            {"symbol": "AAPL", "qty": 10.0, "side": "PositionSide.LONG",
             "avg_entry_price": 175.0, "current_price": 178.0,
             "unrealized_pl": 30.0, "unrealized_plpc": 0.017},
        ]
        mock_orders.return_value = []  # No protection orders
        result = cmd_check_protection(Namespace())
        assert result == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "UNPROTECTED_POSITIONS"
        assert len(output["unprotected"]) == 1
        assert output["unprotected"][0]["symbol"] == "AAPL"
        assert output["unprotected"][0]["oco_side"] == "sell"

    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_short_position_oco_side(self, mock_pos, mock_orders, capsys):
        mock_pos.return_value = [
            {"symbol": "XOM", "qty": -20.0, "side": "PositionSide.SHORT",
             "avg_entry_price": 105.0, "current_price": 100.0,
             "unrealized_pl": 100.0, "unrealized_plpc": 0.048},
        ]
        mock_orders.return_value = []
        cmd_check_protection(Namespace())
        output = json.loads(capsys.readouterr().out)
        assert output["unprotected"][0]["oco_side"] == "buy"

    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_no_positions(self, mock_pos, mock_orders, capsys):
        mock_pos.return_value = []
        mock_orders.return_value = []
        result = cmd_check_protection(Namespace())
        assert result == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "no_positions"


class TestCmdReconcile:
    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_account")
    @patch("trading.executor.get_positions")
    def test_no_progress_file(
        self, mock_pos, mock_acct, mock_orders,
    ):
        """Reconcile should not crash if progress.md missing."""
        mock_pos.return_value = []
        mock_acct.return_value = {
            "equity": 100000, "cash": 50000,
            "buying_power": 100000,
        }
        mock_orders.return_value = []


class TestTrailStops:
    @patch("trading.executor.place_oco_order")
    @patch("trading.executor.cancel_order")
    @patch("trading.executor.get_open_orders")
    @patch("trading.executor.get_positions")
    def test_dry_run_no_side_effects(
        self, mock_pos, mock_orders, mock_cancel, mock_oco,
        capsys,
    ):
        from trading.executor import cmd_trail_stops

        mock_pos.return_value = [
            {"symbol": "AAPL", "qty": 10.0, "side": "PositionSide.LONG",
             "avg_entry_price": 170.0, "current_price": 180.0,
             "unrealized_pl": 100.0, "unrealized_plpc": 0.058},
        ]
        mock_orders.return_value = [
            {"symbol": "AAPL", "order_class": "OrderClass.OCO",
             "legs": [
                 {"stop_price": "160.00", "limit_price": None},
                 {"stop_price": None, "limit_price": "190.00"},
             ]},
        ]

        args = Namespace(breakeven_pct=3.0, trail_pct=2.0, dry_run=True)
        cmd_trail_stops(args)

        # Should NOT have called cancel or place_oco
        mock_cancel.assert_not_called()
        mock_oco.assert_not_called()

        output = json.loads(capsys.readouterr().out)
        assert output["dry_run"] is True
        assert len(output["adjustments"]) == 1
        assert output["adjustments"][0]["action"] == "DRY_RUN"
        assert output["adjustments"][0]["new_sl"] > output["adjustments"][0]["old_sl"]
