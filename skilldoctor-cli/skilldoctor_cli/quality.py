from __future__ import annotations

from typing import Any


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _usage_total(execution: dict[str, Any]) -> int:
    usage = execution.get("usage") or {}
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def score_state(state: dict[str, Any]) -> dict[str, Any]:
    """Deterministic quality rubric for successful or partially successful traces.

    This intentionally consumes the backend's normalized execution result rather
    than re-analyzing raw traces. The CLI adds a product-level scorecard for the
    "successful but low quality" MVP path.
    """

    execution = state.get("execution") or {}
    assertions = execution.get("assertions") or []
    events = execution.get("runtime_events") or state.get("events") or []
    business = state.get("business_result") or {}
    attribution = state.get("attribution") or {}

    pass_rate = float(execution.get("pass_rate") or (1.0 if execution.get("passed") else 0.0))
    assertion_total = len(assertions)
    assertion_passed = sum(1 for item in assertions if item.get("passed"))
    assertion_score = assertion_passed / assertion_total if assertion_total else pass_rate

    failed_events = [item for item in events if item.get("status") == "failed"]
    warning_details = [
        item for item in business.get("details", []) if item.get("status") == "warning"
    ]
    evidence_refs = len(attribution.get("evidence_refs") or [])
    artifacts = execution.get("artifacts") or {}
    usage_total = _usage_total(execution)
    duration_ms = int(execution.get("duration_ms") or 0)

    dimensions = {
        "output_quality": _clamp((pass_rate * 0.65) + (assertion_score * 0.35)),
        "contract_compliance": _clamp(1.0 - (len(failed_events) * 0.2) - (len(warning_details) * 0.1)),
        "evidence_support": _clamp(0.35 + min(0.4, assertion_total * 0.08) + min(0.25, (evidence_refs + len(artifacts)) * 0.08)),
        "cost_efficiency": _clamp(1.0 - max(0, usage_total - 2_000) / 8_000 - max(0, duration_ms - 60_000) / 240_000),
        "safety_boundary": 0.65 if attribution.get("cause") in {"tool", "platform"} else 0.9,
        "stability": _clamp(1.0 - float(execution.get("regression_rate") or 0.0) - (0.1 if state.get("status") == "failed" else 0.0)),
    }
    weights = {
        "output_quality": 0.30,
        "contract_compliance": 0.20,
        "evidence_support": 0.15,
        "cost_efficiency": 0.10,
        "safety_boundary": 0.10,
        "stability": 0.15,
    }
    score = sum(dimensions[name] * weights[name] for name in weights)
    findings: list[str] = []
    if dimensions["output_quality"] < 0.75:
        findings.append("输出质量不足：通过率或断言通过比例偏低。")
    if dimensions["evidence_support"] < 0.65:
        findings.append("证据支撑不足：建议保留关键断言、artifact 或 evidence snapshot。")
    if dimensions["cost_efficiency"] < 0.75:
        findings.append("成本效率偏低：token 或耗时超过 MVP 阈值。")
    if failed_events:
        findings.append(f"存在 {len(failed_events)} 个失败事件，成功链路稳定性不足。")

    return {
        "schema_version": "1.0",
        "overall_score": round(score, 4),
        "grade": "A" if score >= 0.9 else "B" if score >= 0.8 else "C" if score >= 0.7 else "D",
        "dimensions": {key: round(value, 4) for key, value in dimensions.items()},
        "weights": weights,
        "findings": findings,
    }
