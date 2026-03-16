"""Tests for trading/executor.py — CLI parsing and validation logic.

These tests do NOT call Binance API. They test argument parsing,
parameter validation, and command routing only.
"""

from unittest.mock import patch
from trading.executor import build_parser, cmd_status
from trading.client import _validate_bracket_params, OrderResult


class TestBracketValidation:
    def test_valid_long(self):
        err = _validate_bracket_params(0.01, 76000, 71000, "buy", 73000)
        assert err is None

    def test_valid_short(self):
        err = _validate_bracket_params(0.01, 68000, 75000, "sell", 72000)
        assert err is None

    def test_zero_qty(self):
        err = _validate_bracket_params(0, 76000, 71000, "buy")
        assert "qty" in err

    def test_negative_qty(self):
        err = _validate_bracket_params(-1, 76000, 71000, "buy")
        assert "qty" in err

    def test_long_tp_below_sl(self):
        err = _validate_bracket_params(0.01, 70000, 75000, "buy")
        assert "LONG" in err

    def test_short_tp_above_sl(self):
        err = _validate_bracket_params(0.01, 75000, 70000, "sell")
        assert "SHORT" in err

    def test_long_tp_below_entry(self):
        err = _validate_bracket_params(0.01, 72000, 70000, "buy", 73000)
        assert "LONG" in err

    def test_short_tp_above_entry(self):
        err = _validate_bracket_params(0.01, 73000, 75000, "sell", 72000)
        assert "SHORT" in err

    def test_invalid_side(self):
        err = _validate_bracket_params(0.01, 76000, 71000, "hold")
        assert "side" in err


class TestCLIParsing:
    def setup_method(self):
        self.parser = build_parser()

    def test_status(self):
        args = self.parser.parse_args(["status"])
        assert args.command == "status"

    def test_scan_default(self):
        args = self.parser.parse_args(["scan"])
        assert args.command == "scan"
        assert args.pairs is None
        assert args.timeframe == "4h"

    def test_scan_with_pairs(self):
        args = self.parser.parse_args(["scan", "--pairs", "BTC,ETH"])
        assert args.pairs == "BTC,ETH"

    def test_bracket_long(self):
        args = self.parser.parse_args(["bracket", "BTC", "0.002", "76000", "71000"])
        assert args.command == "bracket"
        assert args.symbol == "BTC"
        assert args.qty == 0.002
        assert args.tp == 76000.0
        assert args.sl == 71000.0
        assert args.side == "buy"  # default
        assert args.leverage == 10  # default (aggressive)

    def test_bracket_short(self):
        args = self.parser.parse_args([
            "bracket", "ETH", "0.5", "3200", "3800", "--side", "sell", "--leverage", "10"
        ])
        assert args.side == "sell"
        assert args.leverage == 10

    def test_bracket_with_limit(self):
        args = self.parser.parse_args([
            "bracket", "BTC", "0.01", "80000", "70000", "--limit", "75000"
        ])
        assert args.limit == 75000.0

    def test_close(self):
        args = self.parser.parse_args(["close", "BTC/USDT:USDT"])
        assert args.command == "close"
        assert args.symbol == "BTC/USDT:USDT"

    def test_protect_defaults(self):
        args = self.parser.parse_args(["protect"])
        assert args.command == "protect"
        assert args.trail is False
        assert args.max_days == 10
        assert args.dry_run is False

    def test_protect_with_flags(self):
        args = self.parser.parse_args(["protect", "--trail", "--dry-run", "--max-days", "5"])
        assert args.trail is True
        assert args.dry_run is True
        assert args.max_days == 5


class TestOrderResult:
    def test_success_to_dict(self):
        r = OrderResult(success=True, order_id="123", status="closed")
        d = r.to_dict()
        assert d["success"] is True
        assert d["order_id"] == "123"
        assert d["error"] is None

    def test_failure_to_dict(self):
        r = OrderResult(success=False, error="Insufficient funds")
        d = r.to_dict()
        assert d["success"] is False
        assert "Insufficient" in d["error"]


class TestStatusCommand:
    @patch("trading.executor.get_account")
    @patch("trading.executor.get_positions")
    @patch("trading.executor.get_open_orders")
    def test_status_output(self, mock_orders, mock_positions, mock_account, capsys):
        mock_account.return_value = {
            "equity": 5000.0, "free": 4800.0, "used": 200.0,
            "unrealized_pnl": 10.0, "total_exposure": 200.0,
            "exposure_pct": 4.0, "positions_count": 1,
            "currency": "USDT", "sandbox": True,
        }
        mock_positions.return_value = []
        mock_orders.return_value = []

        args = build_parser().parse_args(["status"])
        result = cmd_status(args)

        assert result == 0
        output = capsys.readouterr().out
        assert "5000.0" in output
        assert "TESTNET" in output
