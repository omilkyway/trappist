#!/usr/bin/env python3
"""Infrastructure diagnostics for auto-improve pipeline.

Analyzes Scaleway job runs, S3 connectivity, API costs, and pipeline health.
Produces actionable recommendations to fix infra issues.

Usage:
    python trading/diagnostics.py [--days 30] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def run_diagnostics(days: int = 30) -> dict:
    """Run full infrastructure diagnostics."""
    from trading.collector import collect_scaleway_runs, analyze_scaleway_runs, collect_reports

    runs = collect_scaleway_runs(days)
    summary = analyze_scaleway_runs(runs)
    reports = collect_reports(days)

    issues = []
    recommendations = []

    # --- Check 1: Job success rate ---
    success_rate = summary.get("success_rate", 100)
    if success_rate < 80:
        issues.append({
            "category": "reliability",
            "severity": "critical",
            "title": f"Low job success rate: {success_rate}%",
            "detail": f"{summary.get('failure_count', 0)} failures out of {summary.get('total_runs', 0)} runs",
        })

    # --- Check 2: S3 connectivity failures ---
    s3_failures = [f for f in summary.get("failures", []) if f["category"] == "s3_connectivity"]
    if s3_failures:
        issues.append({
            "category": "s3",
            "severity": "critical",
            "title": f"{len(s3_failures)} S3 connectivity failures",
            "detail": "AWS CLI defaulting to amazonaws.com instead of s3.fr-par.scw.cloud",
        })
        recommendations.append({
            "priority": 1,
            "action": "FIX_S3_ENDPOINT",
            "description": "Ensure $S3_EP is always passed to aws commands. Add fallback: export AWS_ENDPOINT_URL=https://s3.fr-par.scw.cloud",
            "file": "deploy/scripts/entrypoint.sh",
            "effort": "5min",
        })

    # --- Check 3: Timeouts ---
    timeout_count = summary.get("timeout_count", 0)
    avg_duration = summary.get("avg_duration_s")
    max_duration = summary.get("max_duration_s")
    if timeout_count > 0:
        issues.append({
            "category": "timeout",
            "severity": "high",
            "title": f"{timeout_count} job timeouts",
            "detail": f"Max duration: {max_duration}s. Pipeline sometimes exceeds 1800s limit.",
        })
        recommendations.append({
            "priority": 2,
            "action": "INCREASE_TIMEOUT",
            "description": "Increase job-timeout from 1800s to 2700s (45min) in create-jobs.sh. Also reduce max-turns from 45 to 35.",
            "file": "deploy/create-jobs.sh",
            "effort": "5min",
        })

    # --- Check 4: Duplicate runs ---
    duplicate_runs = _detect_duplicate_runs(runs)
    if duplicate_runs:
        issues.append({
            "category": "scheduling",
            "severity": "medium",
            "title": f"{len(duplicate_runs)} duplicate/concurrent runs detected",
            "detail": "Multiple runs triggered within same minute window",
        })
        recommendations.append({
            "priority": 3,
            "action": "ADD_DEDUP_LOCK",
            "description": "Add a lock mechanism in entrypoint.sh: check S3 for .lock file before starting, skip if another run is active",
            "file": "deploy/scripts/entrypoint.sh",
            "effort": "15min",
        })

    # --- Check 5: API cost analysis ---
    cost_analysis = _analyze_costs(runs)
    if cost_analysis.get("avg_cost_open", 0) > 8:
        issues.append({
            "category": "cost",
            "severity": "medium",
            "title": f"High API cost per open session: ~${cost_analysis['avg_cost_open']:.2f}",
            "detail": "Consider reducing max-turns or using sonnet for less critical phases",
        })
        recommendations.append({
            "priority": 4,
            "action": "OPTIMIZE_COSTS",
            "description": "Use haiku for technical-analyst (bulk data processing) and sonnet for debate/selection phases",
            "file": ".claude/commands/make-profitables-trades.md",
            "effort": "30min",
        })

    # --- Check 6: Pipeline completion ---
    open_runs = [r for r in runs if r.get("run_type") == "open" and r.get("state") in ("succeeded", "completed")]
    if open_runs and reports:
        completion_rate = len(reports) / len(open_runs) * 100 if open_runs else 0
        if completion_rate < 80:
            issues.append({
                "category": "pipeline",
                "severity": "high",
                "title": f"Pipeline completion rate: {completion_rate:.0f}%",
                "detail": f"{len(reports)} reports generated from {len(open_runs)} successful open runs",
            })

    # --- Check 7: Protection gap ---
    protect_runs = [r for r in runs if r.get("run_type") == "protect"]
    protect_successes = [r for r in protect_runs if r.get("state") in ("succeeded", "completed")]
    if protect_runs and len(protect_successes) < len(protect_runs):
        issues.append({
            "category": "protection",
            "severity": "critical",
            "title": f"OCO protection failures: {len(protect_runs) - len(protect_successes)}/{len(protect_runs)}",
            "detail": "Failed protections = unprotected positions in live market",
        })
        recommendations.append({
            "priority": 1,
            "action": "PROTECT_RELIABILITY",
            "description": "Add retry loop with exponential backoff in protector.py. Send Discord alert on failure.",
            "file": "trading/protector.py",
            "effort": "20min",
        })

    # --- Check 8: Report quality ---
    report_issues = _check_report_quality(reports)
    issues.extend(report_issues)

    # Sort recommendations by priority
    recommendations.sort(key=lambda r: r["priority"])

    return {
        "diagnosed_at": datetime.now().isoformat(),
        "days_lookback": days,
        "scaleway_summary": summary,
        "issues": issues,
        "recommendations": recommendations,
        "health_score": _calc_health_score(issues),
        "cost_analysis": cost_analysis,
    }


def _detect_duplicate_runs(runs: list[dict]) -> list[tuple]:
    """Detect runs that were triggered simultaneously."""
    duplicates = []
    by_minute = defaultdict(list)

    for run in runs:
        created = run.get("created_at", "")[:16]  # YYYY-MM-DDTHH:MM
        rt = run.get("run_type", "")
        key = f"{created}_{rt}"
        by_minute[key].append(run)

    for key, group in by_minute.items():
        if len(group) > 1:
            duplicates.append((key, len(group)))

    return duplicates


def _analyze_costs(runs: list[dict]) -> dict:
    """Estimate API costs from run durations and types."""
    # Estimate: ~$0.004/s for sonnet (input+output averaged)
    COST_PER_SECOND = 0.004

    by_type = defaultdict(list)
    for run in runs:
        rt = run.get("run_type", "unknown")
        dur = run.get("duration_s")
        if dur is not None:
            by_type[rt].append(dur)

    result = {}
    for rt, durations in by_type.items():
        avg_dur = sum(durations) / len(durations) if durations else 0
        result[f"avg_duration_{rt}"] = round(avg_dur, 1)
        result[f"avg_cost_{rt}"] = round(avg_dur * COST_PER_SECOND, 2)
        result[f"total_cost_{rt}"] = round(sum(durations) * COST_PER_SECOND, 2)
        result[f"runs_{rt}"] = len(durations)

    total_cost = sum(v for k, v in result.items() if k.startswith("total_cost_"))
    result["total_estimated_cost"] = round(total_cost, 2)

    return result


def _check_report_quality(reports: list[dict]) -> list[dict]:
    """Check quality of session reports."""
    issues = []

    if not reports:
        return issues

    # Check for reports with 0 trades
    zero_trade_sessions = [r for r in reports if r.get("trades_executed", 0) == 0]
    if len(zero_trade_sessions) > len(reports) * 0.5 and len(reports) >= 3:
        issues.append({
            "category": "trading_activity",
            "severity": "high",
            "title": f"{len(zero_trade_sessions)}/{len(reports)} sessions produced 0 trades",
            "detail": "Pipeline runs cost money even with 0 trades. Review halt conditions.",
        })

    # Check for missing improvements section
    no_improvements = [r for r in reports if not r.get("improvements")]
    if len(no_improvements) > len(reports) * 0.3:
        issues.append({
            "category": "report_quality",
            "severity": "low",
            "title": f"{len(no_improvements)}/{len(reports)} reports missing improvement section",
            "detail": "trade-reporter should always identify at least 1 improvement",
        })

    return issues


def _calc_health_score(issues: list[dict]) -> int:
    """Calculate overall health score 0-100."""
    score = 100
    for issue in issues:
        severity = issue.get("severity", "low")
        if severity == "critical":
            score -= 25
        elif severity == "high":
            score -= 15
        elif severity == "medium":
            score -= 8
        else:
            score -= 3
    return max(0, score)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Infrastructure diagnostics")
    parser.add_argument("--days", type=int, default=30, help="Lookback period")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = run_diagnostics(args.days)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_diagnostics(result)


def _print_diagnostics(result: dict):
    """Human-readable diagnostics output."""
    print(f"\n{'='*60}")
    print(f" INFRASTRUCTURE DIAGNOSTICS")
    print(f"{'='*60}\n")

    score = result.get("health_score", 0)
    icon = "OK" if score >= 80 else "WARN" if score >= 50 else "CRITICAL"
    print(f"  Health Score: {score}/100 [{icon}]\n")

    issues = result.get("issues", [])
    if issues:
        print(f"  --- ISSUES ({len(issues)}) ---")
        for issue in issues:
            sev = issue["severity"].upper()
            print(f"  [{sev}] {issue['title']}")
            print(f"         {issue['detail']}")
        print()

    recs = result.get("recommendations", [])
    if recs:
        print(f"  --- RECOMMENDATIONS (priority order) ---")
        for r in recs:
            print(f"  P{r['priority']}: {r['action']} ({r['effort']})")
            print(f"      {r['description']}")
            print(f"      File: {r['file']}")
        print()

    cost = result.get("cost_analysis", {})
    total = cost.get("total_estimated_cost", 0)
    if total > 0:
        print(f"  --- COST SUMMARY ---")
        print(f"  Total estimated: ${total:.2f}")
        for k, v in cost.items():
            if k.startswith("avg_cost_"):
                rt = k.replace("avg_cost_", "")
                runs = cost.get(f"runs_{rt}", 0)
                print(f"    {rt}: ${v:.2f}/run x {runs} runs = ${cost.get(f'total_cost_{rt}', 0):.2f}")
    print()


if __name__ == "__main__":
    main()
