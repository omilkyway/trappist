"""Tests for .claude/hooks/risk_guardian.py — circuit breaker hook."""

import importlib.util
import json
from pathlib import Path

import pytest

# Import the hook module by path
HOOK_PATH = (
    Path(__file__).resolve().parent.parent
    / ".claude" / "hooks" / "risk_guardian.py"
)

spec = importlib.util.spec_from_file_location("risk_guardian", HOOK_PATH)
rg = importlib.util.module_from_spec(spec)


def _load_module():
    """Load risk_guardian module fresh."""
    spec.loader.exec_module(rg)
    return rg


class TestReadonlyTools:
    """Read-only tools should always be allowed (exit 0)."""

    @pytest.mark.parametrize("tool", [
        "mcp__alpaca__get_account_info",
        "mcp__alpaca__get_positions",
        "mcp__alpaca__get_orders",
        "mcp__alpaca__get_stock_quote",
        "mcp__alpaca__get_market_clock",
    ])
    def test_readonly_tools_allowed(self, tool):
        mod = _load_module()
        assert tool in mod.READONLY_TOOLS


class TestBashOrderPatterns:
    """Test regex patterns that detect order-placing bash commands."""

    def test_bracket_command_detected(self):
        mod = _load_module()
        import re
        cmd = "python trading/executor.py bracket NVDA 28 185 166"
        assert any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS)

    def test_opg_command_detected(self):
        mod = _load_module()
        import re
        cmd = "python trading/executor.py opg NVDA 28"
        assert any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS)

    def test_oco_command_detected(self):
        mod = _load_module()
        import re
        cmd = "python trading/executor.py oco NVDA 28 185 166"
        assert any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS)

    def test_close_command_detected(self):
        mod = _load_module()
        import re
        cmd = "python trading/executor.py close NVDA"
        assert any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS)

    def test_curl_alpaca_detected(self):
        mod = _load_module()
        import re
        cmd = "curl -X POST https://paper-api.alpaca.markets/v2/orders"
        assert any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS)

    def test_non_order_bash_not_detected(self):
        mod = _load_module()
        import re
        safe_cmds = [
            "python trading/executor.py account",
            "python trading/executor.py positions",
            "python trading/executor.py analyze NVDA --json",
            "ls -la",
            "git status",
        ]
        for cmd in safe_cmds:
            assert not any(re.search(pat, cmd) for pat in mod.BASH_ORDER_PATTERNS), \
                f"Safe command matched as order: {cmd}"


class TestDrawdownLimit:
    def test_drawdown_limit_is_negative_two_percent(self):
        mod = _load_module()
        assert mod.DRAWDOWN_LIMIT == -0.02


class TestLogEvent:
    def test_log_event_creates_file(self, tmp_path, monkeypatch):
        mod = _load_module()
        monkeypatch.chdir(tmp_path)
        mod.log_event({"test": True})
        log_file = tmp_path / "logs" / "risk_guardian.json"
        assert log_file.exists()
        data = json.loads(log_file.read_text())
        assert len(data) == 1
        assert data[0]["test"] is True

    def test_log_rotation(self, tmp_path, monkeypatch):
        mod = _load_module()
        monkeypatch.chdir(tmp_path)
        # Write MAX_LOG_ENTRIES + 10 entries
        for i in range(mod.MAX_LOG_ENTRIES + 10):
            mod.log_event({"index": i})
        log_file = tmp_path / "logs" / "risk_guardian.json"
        data = json.loads(log_file.read_text())
        assert len(data) == mod.MAX_LOG_ENTRIES
        # Should keep the most recent entries
        assert data[-1]["index"] == mod.MAX_LOG_ENTRIES + 9


class TestDotenvLoading:
    def test_load_dotenv_function_exists(self):
        mod = _load_module()
        assert callable(mod._load_dotenv)
