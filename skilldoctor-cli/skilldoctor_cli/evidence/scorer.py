from __future__ import annotations

from typing import Any

from .contract import EvidenceBundle, EvidenceCheck, SkillProfile


def _check_passed(check: EvidenceCheck, *, min_score: float) -> bool:
    if check.passed is not None:
        return check.passed
    if check.score is not None:
        return check.score >= min_score
    return True


def _check_weight(check: EvidenceCheck, profile: SkillProfile) -> float:
    if check.name in profile.critical_checks or check.severity == "critical":
        return 1.5
    if check.name in profile.required_checks or check.severity == "high":
        return 1.2
    if check.severity == "low":
        return 0.8
    return 1.0


def score_evidence_bundle(
    bundle: EvidenceBundle,
    profile: SkillProfile,
    *,
    confidence: float | None = None,
) -> dict[str, Any]:
    checks = list(bundle.checks)
    check_by_name = {item.name: item for item in checks}
    missing_required = [name for name in profile.required_checks if name not in check_by_name]
    failed_checks = [
        item
        for item in checks
        if not _check_passed(item, min_score=profile.min_evidence_score)
    ]
    failed_critical = [
        item.name
        for item in failed_checks
        if item.name in profile.critical_checks or item.severity == "critical"
    ]

    if checks:
        total_weight = sum(_check_weight(item, profile) for item in checks)
        passed_weight = sum(
            _check_weight(item, profile)
            for item in checks
            if _check_passed(item, min_score=profile.min_evidence_score)
        )
        score = passed_weight / total_weight if total_weight else 1.0
    else:
        score = 1.0 if bundle.artifacts else 0.0

    if bundle.available and missing_required:
        score = max(0.0, score - min(0.3, len(missing_required) * 0.08))

    artifact_types = {item.type for item in bundle.artifacts}
    missing_artifacts: list[str] = []
    if confidence is not None and confidence >= 0.85:
        for artifact_type in profile.high_confidence_requires_artifact_types:
            if artifact_type not in artifact_types:
                missing_artifacts.append(artifact_type)
        if missing_artifacts and not checks:
            score = min(score, 0.65)

    passed = bundle.available and score >= profile.min_evidence_score and not failed_critical
    findings: list[str] = []
    if not bundle.available:
        findings.append("未提供结构化 evidence bundle。")
    if failed_checks:
        findings.append(
            "结构化 evidence checks 未通过："
            + ", ".join(item.name for item in failed_checks)
            + "。"
        )
    if missing_required:
        findings.append("缺少 required evidence checks：" + ", ".join(missing_required) + "。")
    if missing_artifacts:
        findings.append("高置信结果缺少 artifact：" + ", ".join(missing_artifacts) + "。")

    return {
        "available": bundle.available,
        "passed": passed,
        "score": round(max(0.0, min(1.0, score)), 4),
        "failed_checks": [item.name for item in failed_checks],
        "failed_critical_checks": failed_critical,
        "missing_required_checks": missing_required,
        "missing_artifacts": missing_artifacts,
        "findings": findings,
        "checks": [
            {
                "name": item.name,
                "passed": _check_passed(item, min_score=profile.min_evidence_score),
                "score": item.score,
                "severity": item.severity,
                "reason": item.reason,
            }
            for item in checks
        ],
        "artifacts": [
            {"type": item.type, "uri": item.uri, "name": item.name}
            for item in bundle.artifacts
        ],
        "metrics": bundle.metrics,
    }
