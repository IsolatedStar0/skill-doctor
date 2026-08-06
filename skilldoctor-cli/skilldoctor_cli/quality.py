from __future__ import annotations

import json
from typing import Any


BASE_DIMENSION_NAMES = (
    "output_quality",
    "contract_compliance",
    "evidence_support",
    "cost_efficiency",
    "safety_boundary",
    "stability",
)
DIMENSION_NAMES = (*BASE_DIMENSION_NAMES, "domain_quality")


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


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return str(value)
    return str(value).strip()


def _raw_business_result(business: dict[str, Any]) -> dict[str, Any]:
    extra = business.get("extra") if isinstance(business, dict) else None
    raw = extra.get("raw_business_result") if isinstance(extra, dict) else None
    return raw if isinstance(raw, dict) else business


def _business_confidence(business: dict[str, Any], raw: dict[str, Any]) -> float | None:
    value = business.get("confidence")
    if value is None:
        value = raw.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _business_reason_text(business: dict[str, Any], raw: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ("verdict", "reason", "summary", "rca_content", "rca_reason"):
        fragments.append(_text(business.get(key)))
        fragments.append(_text(raw.get(key)))
    for detail in business.get("details") or []:
        if isinstance(detail, dict):
            fragments.append(_text(detail.get("reason") or detail.get("detail")))
    raw_detail = raw.get("rca_detail") or raw.get("details")
    if raw_detail:
        fragments.append(_text(raw_detail))
    return "\n".join(item for item in fragments if item)


def _trace_evidence_strength(
    execution: dict[str, Any],
    attribution: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    strength = 0
    reasons: list[str] = []
    assertions = execution.get("assertions") or []
    passed_assertions = [item for item in assertions if item.get("passed")]
    if passed_assertions:
        strength += 1
        reasons.append(f"有 {len(passed_assertions)} 条通过断言")
    evidence_refs = attribution.get("evidence_refs") or []
    if evidence_refs:
        strength += 1
        reasons.append(f"有 {len(evidence_refs)} 条归因证据")
    artifacts = execution.get("artifacts") or {}
    if artifacts:
        strength += 1
        reasons.append(f"有 {len(artifacts)} 个 artifact")
    for event in events:
        stage = str(event.get("stage") or event.get("id") or "")
        metadata = event.get("metadata") or {}
        if stage == "agent.analyze.tool_calls" and int(metadata.get("total") or 0) > 0:
            strength += 1
            reasons.append(f"记录了 {metadata.get('total')} 次工具调用分析")
            break
    return strength, reasons


def _check(name: str, passed: bool, reason: str, weight: float) -> dict[str, Any]:
    return {
        "name": name,
        "passed": passed,
        "weight": weight,
        "reason": reason,
    }


def _score_puck_rule_rca_domain(state: dict[str, Any]) -> dict[str, Any] | None:
    skill_id = str(state.get("skill_id") or "").lower()
    if skill_id != "puck-rule-rca":
        return None

    business = state.get("business_result") or {}
    if not isinstance(business, dict):
        business = {"verdict": _text(business)}
    raw = _raw_business_result(business)
    execution = state.get("execution") or {}
    attribution = state.get("attribution") or {}
    events = execution.get("runtime_events") or state.get("events") or []
    confidence = _business_confidence(business, raw)
    reason_text = _business_reason_text(business, raw)
    verdict = _text(business.get("verdict") or raw.get("verdict"))
    verdict_type = _text(business.get("verdict_type") or raw.get("verdict_type")).lower()
    has_filter_decision = isinstance(raw.get("rca_filter"), bool)
    details = business.get("details") if isinstance(business.get("details"), list) else []
    strength, strength_reasons = _trace_evidence_strength(execution, attribution, events)

    clear_verdict = bool(verdict or verdict_type or has_filter_decision)
    confidence_valid = confidence is not None and 0.0 <= confidence <= 1.0
    reasoning_enough = len(reason_text) >= 20 and not reason_text.startswith("{")
    detail_enough = bool(details) or bool(raw.get("rca_detail"))
    contract_shape = verdict_type in {"pass", "warning", "fail"} and bool(details)
    evidence_available = strength > 0
    confidence_supported = True
    confidence_reason = "confidence 未达到高置信区间，无需额外证据惩罚。"
    if confidence is None:
        confidence_supported = False
        confidence_reason = "缺少 confidence，无法判断置信度与证据是否匹配。"
    elif confidence >= 0.85 and strength < 2:
        confidence_supported = False
        confidence_reason = "confidence 较高，但 trace 中可用证据少于 2 类。"
    elif confidence >= 0.85:
        confidence_reason = "高 confidence 有足够 trace 证据支撑。"

    checks = [
        _check(
            "has_clear_verdict",
            clear_verdict,
            "输出包含明确 RCA 结论。" if clear_verdict else "缺少明确 RCA 结论。",
            0.18,
        ),
        _check(
            "has_valid_confidence",
            confidence_valid,
            (
                f"confidence={confidence:.2f}，处于 0~1 合法范围。"
                if confidence_valid and confidence is not None
                else "缺少合法 confidence。"
            ),
            0.16,
        ),
        _check(
            "has_reasoning",
            reasoning_enough,
            "输出包含可读的降噪/不降噪依据。" if reasoning_enough else "缺少充分的 RCA 依据说明。",
            0.20,
        ),
        _check(
            "has_detail_evidence",
            detail_enough,
            "输出包含 detail/rca_detail 证据。" if detail_enough else "缺少 detail 或 rca_detail。",
            0.14,
        ),
        _check(
            "contract_shape",
            contract_shape,
            "业务结果符合 verdict_type + details 标准契约。" if contract_shape else "业务结果未完全符合标准契约。",
            0.14,
        ),
        _check(
            "trace_evidence_available",
            evidence_available,
            "；".join(strength_reasons) if strength_reasons else "trace 中缺少可用证据支撑。",
            0.08,
        ),
        _check(
            "confidence_evidence_match",
            confidence_supported,
            confidence_reason,
            0.10,
        ),
    ]
    score = sum(item["weight"] for item in checks if item["passed"])
    failed = [item for item in checks if not item["passed"]]
    return {
        "skill_id": "puck-rule-rca",
        "score": round(_clamp(score), 4),
        "passed": _clamp(score) >= 0.75,
        "confidence": confidence,
        "trace_evidence_strength": strength,
        "checks": checks,
        "findings": [item["reason"] for item in failed],
    }


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
    domain_quality = _score_puck_rule_rca_domain(state)
    weights = {
        "output_quality": 0.30,
        "contract_compliance": 0.20,
        "evidence_support": 0.15,
        "cost_efficiency": 0.10,
        "safety_boundary": 0.10,
        "stability": 0.15,
    }
    if domain_quality is not None:
        dimensions["domain_quality"] = float(domain_quality["score"])
        weights = {name: round(weight * 0.85, 4) for name, weight in weights.items()}
        weights["domain_quality"] = 0.15
    score = sum(dimensions[name] * weights[name] for name in weights)
    reasons: dict[str, list[str]] = {name: [] for name in dimensions}
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

    if domain_quality is not None:
        reasons["domain_quality"].append(
            f"puck-rule-rca 领域评分为 {domain_quality['score']:.2f}。"
        )
        for finding in domain_quality.get("findings") or []:
            reasons["domain_quality"].append(finding)

    findings: list[str] = []
    if dimensions["output_quality"] < 0.75:
        findings.append("输出质量不足：通过率或断言通过比例偏低。")
    if dimensions["evidence_support"] < 0.65:
        findings.append("证据支撑不足：建议保留关键断言、artifact 或 evidence snapshot。")
    if dimensions["cost_efficiency"] < 0.75:
        findings.append("成本效率偏低：token 或耗时超过 MVP 阈值。")
    if failed_events:
        findings.append(f"存在 {len(failed_events)} 个失败事件，成功链路稳定性不足。")
    if domain_quality is not None and not domain_quality.get("passed"):
        findings.append("puck-rule-rca 领域质量不足：业务结论、证据或置信度存在风险。")
    score_breakdown = {
        name: {
            "score": round(dimensions[name], 4),
            "weight": weights[name],
            "weighted_score": round(dimensions[name] * weights[name], 4),
            "reasons": reasons[name],
        }
        for name in dimensions
    }

    result = {
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
    if domain_quality is not None:
        result["domain_quality"] = domain_quality
    return result
