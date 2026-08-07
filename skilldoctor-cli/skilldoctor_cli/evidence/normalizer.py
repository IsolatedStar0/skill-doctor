from __future__ import annotations

from typing import Any

from .contract import ArtifactRef, EvidenceBundle, EvidenceCheck


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_check(payload: dict[str, Any]) -> EvidenceCheck | None:
    name = str(payload.get("name") or payload.get("id") or "").strip()
    if not name:
        return None
    passed = payload.get("passed")
    if passed is None and "status" in payload:
        status = str(payload.get("status") or "").lower()
        if status in {"pass", "passed", "ok", "success"}:
            passed = True
        elif status in {"fail", "failed", "error", "mismatch", "warning"}:
            passed = False
    refs = payload.get("evidence_refs") or payload.get("refs") or []
    if isinstance(refs, str):
        refs = [refs]
    return EvidenceCheck(
        name=name,
        passed=passed if isinstance(passed, bool) else None,
        score=_as_float(payload.get("score")),
        severity=str(payload.get("severity") or "medium").lower(),
        reason=str(payload.get("reason") or payload.get("message") or ""),
        evidence_refs=tuple(str(item) for item in refs),
    )


def _normalize_artifact(payload: dict[str, Any]) -> ArtifactRef | None:
    uri = str(payload.get("uri") or payload.get("url") or payload.get("path") or "").strip()
    if not uri:
        return None
    return ArtifactRef(
        type=str(payload.get("type") or "artifact").lower(),
        uri=uri,
        name=str(payload.get("name") or ""),
    )


def _evidence_payload(business: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    for key in ("evidence", "quality_evidence", "diagnostic_evidence"):
        value = raw.get(key) or business.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _legacy_similarity_payload(business: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    value = (
        raw.get("shape_evidence")
        or raw.get("similarity_evidence")
        or business.get("shape_evidence")
        or business.get("similarity_evidence")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _checks_from_legacy_similarity(evidence: dict[str, Any]) -> list[EvidenceCheck]:
    checks: list[EvidenceCheck] = []
    for name in ("history_regular", "today_vs_day1_similar", "today_vs_day2_similar"):
        value = evidence.get(name)
        if isinstance(value, bool):
            checks.append(
                EvidenceCheck(
                    name=name,
                    passed=value,
                    severity="critical" if name.startswith("today_vs_") else "high",
                    reason=f"legacy similarity field {name}={value}",
                )
            )
    score = _as_float(evidence.get("shape_similarity_score") or evidence.get("similarity_score"))
    if score is not None:
        checks.append(
            EvidenceCheck(
                name="shape_similarity_score",
                passed=score >= 0.75,
                score=score,
                severity="high",
                reason=f"shape similarity score={score:.2f}",
            )
        )
    status = str(evidence.get("status") or evidence.get("comparison_status") or "").strip().lower()
    if status:
        checks.append(
            EvidenceCheck(
                name="comparison_status",
                passed=status not in {"fail", "failed", "mismatch", "not_similar", "irregular"},
                severity="critical",
                reason=f"comparison status={status}",
            )
        )
    mismatches = evidence.get("mismatches") or evidence.get("failed_checks") or []
    if mismatches:
        checks.append(
            EvidenceCheck(
                name="mismatches",
                passed=False,
                severity="critical",
                reason="存在结构化 mismatch: " + ", ".join(str(item) for item in mismatches),
            )
        )
    return checks


def normalize_evidence_bundle(business: dict[str, Any], raw: dict[str, Any]) -> EvidenceBundle:
    payload = _evidence_payload(business, raw)
    checks: list[EvidenceCheck] = []
    artifacts: list[ArtifactRef] = []
    metrics: dict[str, float] = {}

    for item in payload.get("checks") or []:
        if isinstance(item, dict):
            check = _normalize_check(item)
            if check:
                checks.append(check)

    for item in payload.get("artifacts") or []:
        if isinstance(item, dict):
            artifact = _normalize_artifact(item)
            if artifact:
                artifacts.append(artifact)

    raw_metrics = payload.get("metrics") or {}
    if isinstance(raw_metrics, dict):
        for name, value in raw_metrics.items():
            metric = _as_float(value)
            if metric is not None:
                metrics[str(name)] = metric

    legacy_similarity = _legacy_similarity_payload(business, raw)
    checks.extend(_checks_from_legacy_similarity(legacy_similarity))
    for key in ("shape_similarity_score", "similarity_score"):
        metric = _as_float(legacy_similarity.get(key))
        if metric is not None:
            metrics[key] = metric

    chart_url = raw.get("chart_url") or business.get("chart_url")
    if chart_url:
        artifacts.append(ArtifactRef(type="chart", uri=str(chart_url), name="chart_url"))

    return EvidenceBundle(
        checks=tuple(checks),
        artifacts=tuple(artifacts),
        metrics=metrics,
        raw={"evidence": payload, "legacy_similarity": legacy_similarity},
    )
