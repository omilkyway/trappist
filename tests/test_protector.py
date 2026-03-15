"""Tests for trading/protector.py — OCO protection logic."""

import json
from pathlib import Path

import pytest

from trading.protector import (
    load_protections,
    save_protections,
    has_oco_for_symbol,
    find_position,
    auto_detect_unprotected,
)


class TestLoadProtections:
    def test_load_valid_file(self, tmp_path):
        f = tmp_path / "protections.json"
        f.write_text('[{"symbol": "NVDA", "tp": 145, "sl": 125}]')
        result = load_protections(str(f))
        assert len(result) == 1
        assert result[0]["symbol"] == "NVDA"

    def test_load_missing_file(self, tmp_path):
        result = load_protections(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_load_corrupt_file(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json at all")
        result = load_protections(str(f))
        assert result == []

    def test_load_non_list(self, tmp_path):
        f = tmp_path / "obj.json"
        f.write_text('{"symbol": "NVDA"}')
        result = load_protections(str(f))
        assert result == []


class TestSaveProtections:
    def test_save_and_reload(self, tmp_path):
        f = str(tmp_path / "out.json")
        data = [{"symbol": "NVDA", "tp": 145}]
        save_protections(f, data)
        loaded = json.loads(Path(f).read_text())
        assert loaded == data

    def test_atomic_write(self, tmp_path):
        """After save, no temp files should remain."""
        f = str(tmp_path / "out.json")
        save_protections(f, [{"test": True}])
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "out.json"


class TestHasOcoForSymbol:
    def test_detects_oco(self):
        orders = [
            {"symbol": "AAPL", "order_class": "OrderClass.OCO", "legs": [{"id": "1"}]},
        ]
        assert has_oco_for_symbol("AAPL", orders) is True

    def test_no_oco(self):
        orders = [
            {"symbol": "AAPL", "order_class": "simple", "legs": []},
        ]
        assert has_oco_for_symbol("AAPL", orders) is False

    def test_different_symbol(self):
        orders = [
            {"symbol": "MSFT", "order_class": "OrderClass.OCO", "legs": [{"id": "1"}]},
        ]
        assert has_oco_for_symbol("AAPL", orders) is False

    def test_bracket_with_legs(self):
        orders = [
            {"symbol": "AAPL", "order_class": "OrderClass.BRACKET",
             "legs": [{"id": "1"}, {"id": "2"}]},
        ]
        assert has_oco_for_symbol("AAPL", orders) is True

    def test_empty_orders(self):
        assert has_oco_for_symbol("AAPL", []) is False


class TestFindPosition:
    def test_finds_existing(self, sample_positions):
        pos = find_position("AAPL", sample_positions)
        assert pos is not None
        assert pos["symbol"] == "AAPL"

    def test_not_found(self, sample_positions):
        pos = find_position("NVDA", sample_positions)
        assert pos is None


class TestAutoDetectUnprotected:
    def test_detects_naked_long(self):
        positions = [
            {"symbol": "AAPL", "avg_entry_price": 175.0, "qty": 10,
             "side": "PositionSide.LONG"},
        ]
        orders = []
        result = auto_detect_unprotected(positions, orders)
        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        assert result[0]["direction"] == "LONG"
        assert result[0]["oco_side"] == "sell"
        assert result[0]["sl"] < result[0]["tp"]

    def test_detects_naked_short(self):
        positions = [
            {"symbol": "XOM", "avg_entry_price": 105.0, "qty": -20,
             "side": "PositionSide.SHORT"},
        ]
        orders = []
        result = auto_detect_unprotected(positions, orders)
        assert len(result) == 1
        assert result[0]["direction"] == "SHORT"
        assert result[0]["oco_side"] == "buy"
        assert result[0]["sl"] > result[0]["tp"]  # SL above entry for shorts

    def test_protected_position_skipped(self):
        positions = [
            {"symbol": "AAPL", "avg_entry_price": 175.0, "qty": 10,
             "side": "PositionSide.LONG"},
        ]
        orders = [
            {"symbol": "AAPL", "order_class": "OrderClass.OCO",
             "legs": [{"id": "1"}]},
        ]
        result = auto_detect_unprotected(positions, orders)
        assert len(result) == 0

    def test_emergency_sl_tp_values(self):
        """Emergency SL should be 7% from entry, TP 10% from entry."""
        positions = [
            {"symbol": "AAPL", "avg_entry_price": 100.0, "qty": 10,
             "side": "PositionSide.LONG"},
        ]
        result = auto_detect_unprotected(positions, [])
        assert result[0]["sl"] == pytest.approx(93.0)   # 100 * 0.93
        assert result[0]["tp"] == pytest.approx(110.0)   # 100 * 1.10
