from __future__ import annotations

from skilldoctor_cli.commands.validate_labels import _evaluate_records
from skilldoctor_cli.evidence.profiles import get_skill_profile
from skilldoctor_cli.quality import score_state


def _native_puck_record(*, case_id: str, decision: bool, content: str, quality: str) -> dict:
    return {
        "case_id": case_id,
        "skill_id": "puck-rule-rca",
        "agent_output": {
            "rca_filter": decision,
            "rca_content": content,
            "rca_detail": {
                "confidence": 0.91,
                "chart_url": "https://example.com/native-puck.png",
                "details": [
                    {
                        "name": "history_pattern",
                        "status": "pass",
                        "reason": content,
                    }
                ],
            },
        },
        "expectation": {
            "expected_filter": not decision if quality == "bad" else decision,
            "business_quality": quality,
        },
    }


def test_validate_labels_reads_native_puck_fields_and_counts_false_accept() -> None:
    record = _native_puck_record(
        case_id="native-false-accept",
        decision=True,
        quality="bad",
        content="today 与 day-1/day-2 连续下行，趋势形态与峰谷节奏一致，建议降噪。",
    )

    report, _ = _evaluate_records([record], domain_quality_threshold=0.75)

    case = report["cases"][0]
    assert case["domain_quality_score"] >= 0.75
    assert report["summary"]["quality_confusion"]["false_accept"] == 1
    assert report["summary"]["false_accept_rate"] == 1.0


def test_puck_history_gap_rejects_filter_but_not_conservative_no_filter() -> None:
    content = "建议降噪；today 趋势形态连续，但历史覆盖分段且不完整，历史窗口不完整。"
    bad_record = _native_puck_record(
        case_id="0715-row-830-dispatch-12471508",
        decision=True,
        quality="bad",
        content=content,
    )
    conservative_record = _native_puck_record(
        case_id="history-missing-conservative",
        decision=False,
        quality="good",
        content="不建议降噪；历史无数据且历史窗口不完整，证据不足，保守保留告警。",
    )

    report, _ = _evaluate_records(
        [bad_record, conservative_record],
        domain_quality_threshold=0.75,
    )

    bad, conservative = report["cases"]
    assert bad["domain_quality_score"] < 0.75
    assert any("历史证据缺口" in finding for finding in bad["findings"])
    assert conservative["domain_quality_score"] >= 0.75
    assert conservative["domain_quality_passed"] is True


def test_release_checklist_profile_and_domain_scorer_reject_failed_fixture() -> None:
    assert get_skill_profile("release-checklist").critical_checks == ("rollback-gate-present",)
    state = {
        "skill_id": "release-checklist",
        "status": "failed",
        "execution": {
            "passed": False,
            "pass_rate": 0.5,
            "assertions": [
                {"id": "rollback-gate-present", "passed": False, "detail": "缺少回滚校验。"},
                {"id": "approval-gate-present", "passed": True},
            ],
        },
    }

    domain = score_state(state)["domain_quality"]

    assert domain["evidence_profile"] == "release-checklist"
    assert domain["score"] < 0.75
    assert domain["passed"] is False


def test_release_checklist_domain_scorer_accepts_healthy_fixture() -> None:
    state = {
        "skill_id": "release-checklist",
        "status": "passed",
        "business_result": {
            "verdict": "Release checklist is complete.",
            "verdict_type": "pass",
            "details": [
                {"name": "rollback_gate", "status": "pass", "reason": "Rollback gate included."}
            ],
        },
        "execution": {
            "passed": True,
            "pass_rate": 1.0,
            "assertions": [
                {"id": "validation-gate-present", "passed": True},
                {"id": "approval-gate-present", "passed": True},
            ],
        },
    }

    domain = score_state(state)["domain_quality"]

    assert domain["score"] == 1.0
    assert domain["passed"] is True
    assert {item["name"] for item in domain["checks"]} == {
        "rollback-gate-present",
        "validation-gate-present",
        "approval-gate-present",
    }
