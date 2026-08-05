from __future__ import annotations

from typing import Any


DIMENSION_NAMES = (
    "output_quality",
    "contract_compliance",
    "evidence_support",
    "cost_efficiency",
    "safety_boundary",
    "stability",
)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _usage_total(execution: dict[str, Any]) -> int:
    usage = execution.get("usage") or {}
    return int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)


def _event_label(event: dict[str, Any], index: int) -> str:
    stage = event.get("stage") or event.get("id") or f"event-{index}"
    status = event.get("status") or "unknown"
    return f"runtime_event:{stage}:{status}"


def _collect_evidence_refs(
    execution: dict[str, Any],
    attribution: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[str]:
    refs: list[str] = []
    refs.extend(str(item) for item in attribution.get("evidence_refs") or [])
    for name, value in (execution.get("artifacts") or {}).items():
        refs.append(f"artifact:{name}={value}")
    for assertion in execution.get("assertions") or []:
        assertion_id = assertion.get("id")
        if assertion_id:
            refs.append(f"assertion:{assertion_id}:{'pass' if assertion.get('passed') else 'fail'}")
    for index, event in enumerate(events[:5], start=1):
        refs.append(_event_label(event, index))
    return refs


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
    regression_rate = float(execution.get("regression_rate") or 0.0)

    dimensions = {
        "output_quality": _clamp((pass_rate * 0.65) + (assertion_score * 0.35)),
        "contract_compliance": _clamp(1.0 - (len(failed_events) * 0.2) - (len(warning_details) * 0.1)),
        "evidence_support": _clamp(0.35 + min(0.4, assertion_total * 0.08) + min(0.25, (evidence_refs + len(artifacts)) * 0.08)),
        "cost_efficiency": _clamp(1.0 - max(0, usage_total - 2_000) / 8_000 - max(0, duration_ms - 60_000) / 240_000),
        "safety_boundary": 0.65 if attribution.get("cause") in {"tool", "platform"} else 0.9,
        "stability": _clamp(1.0 - regression_rate - (0.1 if state.get("status") == "failed" else 0.0)),
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
    reasons: dict[str, list[str]] = {name: [] for name in DIMENSION_NAMES}
    reasons["output_quality"].append(
        f"执行通过率为 {pass_rate:.2f}，断言通过率为 {assertion_score:.2f}。"
    )
    if pass_rate < 0.75 or assertion_score < 0.75:
        reasons["output_quality"].append("通过率或断言通过比例低于 0.75，输出结果存在明显质量风险。")
    if assertion_total == 0:
        reasons["output_quality"].append("未提供断言，输出质量主要依赖执行 pass_rate 推断。")

    if failed_events:
        reasons["contract_compliance"].append(f"存在 {len(failed_events)} 个失败 runtime event。")
    if warning_details:
        reasons["contract_compliance"].append(f"业务结果中存在 {len(warning_details)} 个 warning detail。")
    if not failed_events and not warning_details:
        reasons["contract_compliance"].append("未发现失败事件或业务 warning，契约执行链路完整。")

    if evidence_refs:
        reasons["evidence_support"].append(f"归因结果提供了 {evidence_refs} 个 evidence reference。")
    if artifacts:
        reasons["evidence_support"].append(f"执行结果包含 {len(artifacts)} 个 artifact。")
    if assertion_total:
        reasons["evidence_support"].append(f"执行结果包含 {assertion_total} 个可检查断言。")
    if not evidence_refs and not artifacts:
        reasons["evidence_support"].append("缺少 attribution evidence_refs 或 artifacts，证据链偏弱。")

    reasons["cost_efficiency"].append(
        f"总 token={usage_total}，耗时={duration_ms}ms。"
    )
    if usage_total > 2_000:
        reasons["cost_efficiency"].append("token 用量超过 2000，开始计入成本惩罚。")
    if duration_ms > 60_000:
        reasons["cost_efficiency"].append("耗时超过 60s，开始计入效率惩罚。")

    cause = attribution.get("cause")
    if cause in {"tool", "platform"}:
        reasons["safety_boundary"].append(
            f"当前归因为 {cause}，需要避免将非 Skill 问题误作为 Skill 质量通过。"
        )
    else:
        reasons["safety_boundary"].append("未发现 tool/platform 归因风险，安全边界正常。")

    if regression_rate:
        reasons["stability"].append(f"执行结果报告 regression_rate={regression_rate:.2f}。")
    if state.get("status") == "failed":
        reasons["stability"].append("当前 run 状态为 failed，稳定性扣分。")
    if not regression_rate and state.get("status") != "failed":
        reasons["stability"].append("未报告回归且 run 未失败，稳定性正常。")

    findings: list[str] = []
    if dimensions["output_quality"] < 0.75:
        findings.append("输出质量不足：通过率或断言通过比例偏低。")
    if dimensions["evidence_support"] < 0.65:
        findings.append("证据支撑不足：建议保留关键断言、artifact 或 evidence snapshot。")
    if dimensions["cost_efficiency"] < 0.75:
        findings.append("成本效率偏低：token 或耗时超过 MVP 阈值。")
    if failed_events:
        findings.append(f"存在 {len(failed_events)} 个失败事件，成功链路稳定性不足。")
    score_breakdown = {
        name: {
            "score": round(dimensions[name], 4),
            "weight": weights[name],
            "weighted_score": round(dimensions[name] * weights[name], 4),
            "reasons": reasons[name],
        }
        for name in DIMENSION_NAMES
    }

    return {
        "schema_version": "1.0",
        "overall_score": round(score, 4),
        "grade": "A" if score >= 0.9 else "B" if score >= 0.8 else "C" if score >= 0.7 else "D",
        "dimensions": {key: round(value, 4) for key, value in dimensions.items()},
        "weights": weights,
        "score_breakdown": score_breakdown,
        "reasons": reasons,
        "evidence_refs": _collect_evidence_refs(execution, attribution, events),
        "findings": findings,
    }
