from __future__ import annotations

from typing import Any


def _percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def print_run_summary(state: dict[str, Any], *, title: str = "Skill Doctor") -> None:
    execution = state.get("execution") or {}
    attribution = state.get("attribution") or {}
    print(f"\n{title}")
    print("=" * len(title))
    print(f"run_id: {state.get('run_id')}")
    print(f"status: {state.get('status')} ({state.get('stop_reason', '')})")
    print(f"skill: {state.get('skill_id')}@{state.get('skill_version')}")
    if execution:
        print(f"execution: passed={execution.get('passed')} pass_rate={_percent(execution.get('pass_rate'))}")
        if execution.get("summary"):
            print(f"summary: {execution.get('summary')}")
    if attribution:
        print(
            "attribution: "
            f"cause={attribution.get('cause')} "
            f"action={attribution.get('action')} "
            f"confidence={_percent(attribution.get('confidence'))}"
        )
        explanation = attribution.get("agent_reason") or attribution.get("explanation")
        if explanation:
            print(f"reason: {explanation}")
    quality = state.get("quality")
    if quality:
        print(f"quality: {quality.get('grade')} ({quality.get('overall_score')})")
        for finding in quality.get("findings", [])[:5]:
            print(f"- {finding}")


def print_suite_summary(report: dict[str, Any], *, title: str = "Diagnostic Suite") -> None:
    summary = report.get("summary") or {}
    print(f"\n{title}")
    print("=" * len(title))
    print(f"status: {report.get('status')}")
    print(
        "cases: "
        f"total={summary.get('total', 0)} "
        f"passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} "
        f"pass_rate={_percent(summary.get('pass_rate'))}"
    )
    if "quality_average" in summary:
        print(f"quality_average: {summary.get('quality_average'):.4f}")
    for case in (report.get("cases") or [])[:8]:
        marker = "PASS" if case.get("passed") else "FAIL"
        print(f"[{marker}] {case.get('case_id')}: {case.get('name', '')}")


def print_compare_summary(report: dict[str, Any]) -> None:
    print("\nSkill Doctor Compare")
    print("====================")
    print(f"decision: {report.get('decision')}")
    print(f"old_pass_rate: {_percent(report.get('old', {}).get('pass_rate'))}")
    print(f"new_pass_rate: {_percent(report.get('new', {}).get('pass_rate'))}")
    print(f"pass_rate_delta: {_percent(report.get('delta', {}).get('pass_rate_delta'))}")
    print(f"regressed_cases: {len(report.get('delta', {}).get('regressed_cases') or [])}")
    for reason in report.get("reasons", []):
        print(f"- {reason}")
