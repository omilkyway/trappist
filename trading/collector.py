#!/usr/bin/env python3
"""Data collector for auto-improve pipeline.

Gathers data from 3 sources:
1. Scaleway serverless job runs (via `scw` CLI)
2. S3 stored logs and reports (via `aws` CLI)
3. Alpaca closed orders and portfolio history (via trading.client)

Usage:
    python trading/collector.py scaleway [--days 30] [--json]
    python trading/collector.py trades [--days 30] [--json]
    python trading/collector.py reports [--days 30] [--json]
    python trading/collector.py all [--days 30] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Scaleway job runs collector
# ---------------------------------------------------------------------------

def collect_scaleway_runs(days: int = 30) -> list[dict]:
    """Collect Scaleway serverless job runs via `scw` CLI."""
    try:
        result = subprocess.run(
            ["scw", "jobs", "run", "list", "-o", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return [{"error": f"scw CLI failed: {result.stderr.strip()}"}]

        runs = json.loads(result.stdout)
        if not isinstance(runs, list):
            return []

        cutoff = datetime.now().astimezone() - timedelta(days=days)
        filtered = []
        for run in runs:
            # Parse created_at
            created = run.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if dt < cutoff:
                    continue
            except (ValueError, TypeError):
                pass

            # Extract job definition name from ID
            job_def_id = run.get("job_definition_id", "")

            # Determine run type from environment or job name
            env_vars = run.get("environment_variables", {})
            run_type = env_vars.get("RUN_TYPE", _guess_run_type(job_def_id, run))

            filtered.append({
                "id": run.get("id", ""),
                "job_definition_id": job_def_id,
                "run_type": run_type,
                "state": run.get("state", ""),
                "created_at": created,
                "terminated_at": run.get("terminated_at", ""),
                "duration_s": _calc_duration(created, run.get("terminated_at", "")),
                "exit_code": run.get("exit_code"),
                "error_message": run.get("error_message", ""),
                "cpu_limit": run.get("cpu_limit"),
                "memory_limit": run.get("memory_limit"),
                # Extract cost indicators
                "started_at": run.get("started_at", ""),
            })

        return filtered

    except FileNotFoundError:
        return [{"error": "scw CLI not found — install with: curl -s https://raw.githubusercontent.com/scaleway/scaleway-cli/master/scripts/get.sh | sh"}]
    except subprocess.TimeoutExpired:
        return [{"error": "scw CLI timed out (30s)"}]
    except Exception as e:
        return [{"error": str(e)}]


def _guess_run_type(job_def_id: str, run: dict) -> str:
    """Guess run type from job definition ID patterns."""
    # Known job definition IDs from create-jobs.sh
    name = run.get("job_definition_name", "") or ""
    if "open" in name.lower() and "protect" not in name.lower():
        return "open"
    if "close" in name.lower():
        return "close"
    if "protect" in name.lower():
        return "protect"
    return "unknown"


def _calc_duration(start: str, end: str) -> float | None:
    """Calculate duration in seconds between two ISO timestamps."""
    if not start or not end:
        return None
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (e - s).total_seconds()
    except (ValueError, TypeError):
        return None


def analyze_scaleway_runs(runs: list[dict]) -> dict:
    """Produce summary statistics from Scaleway runs."""
    if not runs or (len(runs) == 1 and "error" in runs[0]):
        return {"error": runs[0].get("error", "no data") if runs else "no data"}

    total = len(runs)
    by_state = {}
    by_type = {}
    failures = []
    durations = []
    timeouts = []

    for run in runs:
        state = run.get("state", "unknown")
        by_state[state] = by_state.get(state, 0) + 1

        rt = run.get("run_type", "unknown")
        by_type[rt] = by_type.get(rt, 0) + 1

        dur = run.get("duration_s")
        if dur is not None:
            durations.append(dur)

        err = run.get("error_message", "")
        if err:
            # Categorize error
            category = _categorize_error(err)
            failures.append({
                "id": run["id"][:8],
                "run_type": rt,
                "state": state,
                "category": category,
                "error_excerpt": err[:200],
                "created_at": run.get("created_at", ""),
            })
            if "timeout" in err.lower():
                timeouts.append(run)

    return {
        "total_runs": total,
        "by_state": by_state,
        "by_type": by_type,
        "success_rate": round(by_state.get("succeeded", 0) / total * 100, 1) if total > 0 else 0,
        "avg_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
        "max_duration_s": round(max(durations), 1) if durations else None,
        "failure_count": len(failures),
        "timeout_count": len(timeouts),
        "failures": failures,
    }


def _categorize_error(error_msg: str) -> str:
    """Categorize an error message into known patterns."""
    err = error_msg.lower()
    if "timeout" in err:
        return "timeout"
    if "s3" in err or "endpoint" in err or "amazonaws" in err:
        return "s3_connectivity"
    if "api" in err and ("rate" in err or "429" in err):
        return "api_rate_limit"
    if "circuit" in err or "drawdown" in err:
        return "circuit_breaker"
    if "permission" in err or "denied" in err:
        return "permissions"
    if "docker" in err or "container" in err:
        return "container"
    if "anthropic" in err or "claude" in err:
        return "llm_api"
    return "other"


# ---------------------------------------------------------------------------
# S3 reports collector (Scaleway Object Storage)
# ---------------------------------------------------------------------------

S3_BUCKET = "s3://claude-trading"
S3_REPORTS_PREFIX = "reports/"
S3_PROFILE = "scaleway"


def collect_reports(days: int = 30, local_dir: str = "reports") -> list[dict]:
    """Collect and parse trading session reports from Scaleway S3.

    Downloads reports from the remote S3 bucket (source of truth),
    falling back to local files only if S3 is unreachable.
    """
    cutoff = datetime.now() - timedelta(days=days)

    # Try S3 first (source of truth — cloud runs store reports there)
    reports = _collect_reports_s3(cutoff)
    if reports is not None:
        return reports

    # Fallback to local files if S3 is unreachable
    return _collect_reports_local(cutoff, local_dir)


def _collect_reports_s3(cutoff: datetime) -> list[dict] | None:
    """Fetch reports from Scaleway S3 bucket. Returns None if S3 fails."""
    import tempfile

    # List remote report files
    try:
        result = subprocess.run(
            ["aws", "--profile", S3_PROFILE, "s3", "ls",
             f"{S3_BUCKET}/{S3_REPORTS_PREFIX}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    # Parse listing to find matching files
    files_to_fetch = []
    for line in result.stdout.strip().splitlines():
        # Format: "2026-03-09 03:23:24      12511 trading-session-20260308-223000.md"
        parts = line.strip().split()
        if len(parts) < 4:
            continue
        filename = parts[-1]
        if not filename.startswith("trading-session-"):
            continue

        match = re.search(r"(\d{8})-(\d{6})", filename)
        if not match:
            continue
        try:
            dt = datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
            if dt < cutoff:
                continue
        except ValueError:
            continue

        files_to_fetch.append((filename, dt))

    if not files_to_fetch:
        return []

    # Download and parse each report
    reports = []
    with tempfile.TemporaryDirectory(prefix="trading-reports-") as tmpdir:
        for filename, dt in sorted(files_to_fetch, key=lambda x: x[1]):
            local_path = Path(tmpdir) / filename
            try:
                dl = subprocess.run(
                    ["aws", "--profile", S3_PROFILE, "s3", "cp",
                     f"{S3_BUCKET}/{S3_REPORTS_PREFIX}{filename}",
                     str(local_path)],
                    capture_output=True, text=True, timeout=15,
                )
                if dl.returncode != 0:
                    continue
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue

            content = local_path.read_text(encoding="utf-8", errors="replace")
            report = _parse_report(content, filename, dt)
            reports.append(report)

    return reports


def _collect_reports_local(cutoff: datetime, local_dir: str = "reports") -> list[dict]:
    """Fallback: collect reports from local directory."""
    reports_dir = Path(local_dir)
    if not reports_dir.exists():
        return []

    reports = []
    for f in sorted(reports_dir.glob("trading-session-*.md")):
        match = re.search(r"(\d{8})-(\d{6})", f.name)
        if not match:
            continue
        try:
            dt = datetime.strptime(f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S")
            if dt < cutoff:
                continue
        except ValueError:
            continue

        content = f.read_text(encoding="utf-8", errors="replace")
        report = _parse_report(content, f.name, dt)
        reports.append(report)

    return reports


def _parse_report(content: str, filename: str, dt: datetime) -> dict:
    """Extract structured data from a trading session report."""
    report = {
        "filename": filename,
        "date": dt.strftime("%Y-%m-%d %H:%M"),
        "trades_executed": 0,
        "trades_max": 5,
        "capital_engaged_pct": 0,
        "vix_regime": "",
        "direction": "",
        "tickers": [],
        "improvements": [],
        "divergences": [],
        "errors": [],
        "projected_best": 0,
        "projected_worst": 0,
        "projected_expected": 0,
    }

    # Extract trades count
    m = re.search(r"Trades execut.s.*?:\s*(\d+)\s*/\s*(\d+)", content)
    if m:
        report["trades_executed"] = int(m.group(1))
        report["trades_max"] = int(m.group(2))

    # Extract capital engaged
    m = re.search(r"Capital engage.*?:\s*~?\$?([\d,.]+)\s*\(([\d.]+)%", content)
    if m:
        report["capital_engaged_pct"] = float(m.group(2))

    # Extract VIX
    m = re.search(r"VIX.*?:\s*~?([\d.]+)", content)
    if m:
        report["vix_regime"] = m.group(1)

    # Extract direction
    m = re.search(r"Direction dominante.*?:\s*(.+)", content)
    if m:
        report["direction"] = m.group(1).strip()

    # Extract EXECUTED tickers from Phase 4 / Execution / Ordres places section
    exec_section = re.search(
        r"(?:PHASE 4|EXECUTION|Ordres plac)(.*?)(?:\n## |\n---|\Z)", content, re.DOTALL
    )
    if exec_section:
        seen_tickers = set()
        # Match: | TICKER | DIRECTION | (with flexible spacing)
        for m in re.finditer(r"\|\s*([A-Z]{1,5})\s+\|\s*(LONG|SHORT)\s+\|", exec_section.group(1)):
            sym = m.group(1)
            direction = m.group(2)
            key = f"{sym}_{direction}"
            if key not in seen_tickers:
                seen_tickers.add(key)
                report["tickers"].append({
                    "symbol": sym,
                    "direction": direction,
                })

    # Extract improvements
    improvements_section = re.search(
        r"AMELIORATIONS IDENTIFIEES(.*?)(?:---|\Z)", content, re.DOTALL
    )
    if improvements_section:
        for m in re.finditer(r"\d+\.\s*\*\*(.+?)\*\*:\s*(.+?)(?=\n\d+\.|\Z)", improvements_section.group(1), re.DOTALL):
            report["improvements"].append({
                "title": m.group(1).strip(),
                "description": m.group(2).strip()[:200],
            })

    # Extract divergences
    divergences_section = re.search(
        r"DIVERGENCES ENTRE AGENTS(.*?)(?:---|\Z)", content, re.DOTALL
    )
    if divergences_section:
        for m in re.finditer(r"\d+\.\s*\*\*(.+?)\*\*:\s*(.+?)(?=\n\d+\.|\Z)", divergences_section.group(1), re.DOTALL):
            report["divergences"].append({
                "title": m.group(1).strip(),
                "description": m.group(2).strip()[:200],
            })

    # Extract projected P&L (from the aggregate summary lines, not per-ticker)
    m = re.search(r"\*\*Best case\*\*.*?\+\$([\d,.]+)", content)
    if m:
        report["projected_best"] = float(m.group(1).replace(",", ""))
    m = re.search(r"\*\*Worst case\*\*.*?-\$([\d,.]+)", content)
    if m:
        report["projected_worst"] = -float(m.group(1).replace(",", ""))
    m = re.search(r"\*\*Expected\*\*.*?\+\$([\d,.]+)", content)
    if m:
        report["projected_expected"] = float(m.group(1).replace(",", ""))

    # Extract errors
    errors_section = re.search(r"Erreurs d.execution(.*?)(?:---|\Z)", content, re.DOTALL)
    if errors_section:
        err_text = errors_section.group(1).strip()
        if err_text.lower() != "none" and "none." not in err_text.lower()[:10]:
            report["errors"].append(err_text[:200])

    return report


# ---------------------------------------------------------------------------
# Alpaca trades collector
# ---------------------------------------------------------------------------

def collect_trades(days: int = 30) -> dict:
    """Collect closed trades and portfolio history from Alpaca."""
    from trading.client import get_closed_orders, get_portfolio_history, get_account, get_positions

    result = {
        "account": {},
        "positions": [],
        "closed_orders": [],
        "portfolio_history": {},
    }

    try:
        result["account"] = get_account()
    except Exception as e:
        result["account"] = {"error": str(e)}

    try:
        result["positions"] = get_positions()
    except Exception as e:
        result["positions"] = [{"error": str(e)}]

    try:
        result["closed_orders"] = get_closed_orders(days=days)
    except Exception as e:
        result["closed_orders"] = [{"error": str(e)}]

    try:
        result["portfolio_history"] = get_portfolio_history(days=days)
    except Exception as e:
        result["portfolio_history"] = {"error": str(e)}

    return result


# ---------------------------------------------------------------------------
# Collect all
# ---------------------------------------------------------------------------

def collect_all(days: int = 30) -> dict:
    """Collect data from all sources."""
    scw_runs = collect_scaleway_runs(days)
    return {
        "collected_at": datetime.now().isoformat(),
        "days_lookback": days,
        "scaleway": {
            "runs": scw_runs,
            "summary": analyze_scaleway_runs(scw_runs),
        },
        "reports": collect_reports(days),
        "trades": collect_trades(days),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auto-improve data collector")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, desc in [
        ("scaleway", "Collect Scaleway job runs"),
        ("trades", "Collect Alpaca closed trades"),
        ("reports", "Collect and parse session reports"),
        ("all", "Collect from all sources"),
    ]:
        p = sub.add_parser(name, help=desc)
        p.add_argument("--days", type=int, default=30, help="Lookback period in days")
        p.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    if args.command == "scaleway":
        runs = collect_scaleway_runs(args.days)
        summary = analyze_scaleway_runs(runs)
        data = {"runs": runs, "summary": summary}
    elif args.command == "trades":
        data = collect_trades(args.days)
    elif args.command == "reports":
        data = collect_reports(args.days)
    elif args.command == "all":
        data = collect_all(args.days)
    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        # Pretty human-readable output
        _print_summary(args.command, data)


def _print_summary(command: str, data):
    """Print human-readable summary."""
    print(f"\n{'='*60}")
    print(f" AUTO-IMPROVE COLLECTOR — {command.upper()}")
    print(f"{'='*60}\n")

    if command == "scaleway":
        s = data.get("summary", {})
        print(f"  Total runs: {s.get('total_runs', 0)}")
        print(f"  Success rate: {s.get('success_rate', 0)}%")
        print(f"  Avg duration: {s.get('avg_duration_s', 'N/A')}s")
        print(f"  Failures: {s.get('failure_count', 0)}")
        print(f"  Timeouts: {s.get('timeout_count', 0)}")
        if s.get("failures"):
            print(f"\n  Failure details:")
            for f in s["failures"]:
                print(f"    [{f['category']}] {f['run_type']} @ {f['created_at'][:10]}: {f['error_excerpt'][:80]}")

    elif command == "trades":
        acct = data.get("account", {})
        print(f"  Equity: ${acct.get('equity', 0):,.2f}")
        positions = data.get("positions", [])
        print(f"  Open positions: {len(positions)}")
        closed = data.get("closed_orders", [])
        closed_valid = [o for o in closed if "error" not in o]
        print(f"  Closed orders (period): {len(closed_valid)}")
        filled = [o for o in closed_valid if o.get("status") == "OrderStatus.FILLED"]
        print(f"  Filled orders: {len(filled)}")

    elif command == "reports":
        if isinstance(data, list):
            print(f"  Reports found: {len(data)}")
            for r in data:
                tickers = ", ".join(f"{t['symbol']} {t['direction']}" for t in r.get("tickers", []))
                print(f"  [{r['date']}] {r['trades_executed']} trades | {tickers or 'none'} | VIX {r['vix_regime']}")
                if r.get("improvements"):
                    for imp in r["improvements"][:2]:
                        print(f"    → {imp['title']}")

    elif command == "all":
        print(json.dumps(data, indent=2, default=str)[:3000])
        print("\n  ... (use --json for full output)")

    print()


if __name__ == "__main__":
    main()
