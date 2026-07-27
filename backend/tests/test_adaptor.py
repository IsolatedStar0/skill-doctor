"""Tests for the Skill-Adaptor style attribution / repair / qualification layer.

These tests document the behaviour of the ported ``Localizer`` /
``Linker`` / ``Generator`` / ``Reviser`` / ``Qualifier`` modules and lock
in the round-trip semantics of the new ``AttributionResult`` /
``RepairPatch`` / ``VerificationResult`` fields.
"""

from __future__ import annotations

from pathlib import Path

from backend.skilldoctor.adaptor import (
    FaultType,
    Generator,
    Linker,
    Localizer,
    Qualifier,
    Reviser,
    trajectory_from_execution,
)
from backend.skilldoctor.models import (
    AssertionResult,
    ExecutionResult,
    RunRequest,
    TokenUsage,
)
from backend.skilldoctor.service import RunService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Unit-level tests for the individual stages
# ---------------------------------------------------------------------------


def _make_execution(
    *,
    passed: bool,
    condition: str = "with_skill",
    assertions: list[AssertionResult] | None = None,
    error: str | None = None,
    executor: str = "fixture",
    runtime_events: list[dict] | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        executor=executor,
        condition=condition,
        passed=passed,
        pass_rate=1.0 if passed else 0.5,
        duration_ms=100,
        usage=TokenUsage(input_tokens=10, output_tokens=10),
        assertions=assertions or [],
        runtime_events=runtime_events or [],
        summary="",
        error=error,
    )


def test_localizer_classifies_skill_wrong() -> None:
    execution = _make_execution(
        passed=False,
        assertions=[
            AssertionResult(
                id="full-input-coverage",
                source="skill",
                passed=False,
                detail="The procedure only processed preview rows.",
            ),
            AssertionResult(
                id="output-contract",
                source="task",
                passed=True,
                detail="ok",
            ),
        ],
    )
    fault = Localizer().localize(execution, current_skill_id="spreadsheet")

    assert fault is not None
    assert fault.fault_type is FaultType.SKILL_WRONG
    assert fault.t_star == 0
    assert fault.wrong_action == "full-input-coverage"
    assert "spreadsheet" not in fault.improvement_principle  # generic principle
    assert fault.improvement_principle  # non-empty


def test_localizer_classifies_skill_missing_for_without_skill() -> None:
    execution = _make_execution(
        passed=False,
        condition="without_skill",
        assertions=[
            AssertionResult(
                id="baseline-check",
                source="task",
                passed=False,
                detail="Baseline missed the constraint.",
            ),
        ],
    )
    fault = Localizer().localize(execution, current_skill_id="tdd-workflow")

    assert fault is not None
    assert fault.fault_type is FaultType.SKILL_MISSING
    assert "tdd-workflow" in fault.improvement_principle


def test_localizer_classifies_reasoning_wrong_for_platform_error() -> None:
    execution = _make_execution(
        passed=False,
        assertions=[
            AssertionResult(
                id="external-service",
                source="system",
                passed=False,
                detail="Upstream service connection was reset.",
            )
        ],
        error="network connection reset",
    )
    fault = Localizer().localize(execution, current_skill_id="anything")

    assert fault is not None
    assert fault.fault_type is FaultType.REASONING_WRONG


def test_linker_promotes_current_skill_for_skill_wrong() -> None:
    execution = _make_execution(
        passed=False,
        assertions=[
            AssertionResult(
                id="constraint",
                source="skill",
                passed=False,
            )
        ],
    )
    fault = Localizer().localize(execution, current_skill_id="target")
    assert fault is not None

    attributions = Linker().attribute(fault, current_skill_id="target")

    assert attributions
    head = attributions[0]
    assert head.skill_id == "target"
    assert head.action == "revise"
    assert head.weight >= 0.8


def test_reviser_adds_negative_example_for_reasoning_wrong() -> None:
    fault = Localizer()._localize_rule_based(  # type: ignore[attr-defined]
        _make_execution(
            passed=False,
            assertions=[
                AssertionResult(id="sys", source="system", passed=False, detail="oops")
            ],
        ),
        trajectory_from_execution(
            _make_execution(
                passed=False,
                assertions=[
                    AssertionResult(id="sys", source="system", passed=False, detail="oops")
                ],
            )
        ),
        current_skill_id="s",
    )
    reviser = Reviser()
    linker = Linker()
    head = linker.attribute(fault, current_skill_id="s")[0]

    body, revision_type, _ = reviser.revise(
        fault, current_body="# Skill body", attribution=head
    )

    assert revision_type == "add_negative_example"
    assert "Do NOT" in body
    assert "step" in body.lower()


def test_trajectory_filters_synthetic_analysis_events() -> None:
    execution = _make_execution(
        passed=False,
        runtime_events=[
            {
                "stage": "agent.analyze.summarize",
                "status": "failed",
                "message": "Synthetic analyzer summary failed.",
            },
            {
                "stage": "puck.noise_judge",
                "status": "failed",
                "message": "Business tool produced a low-confidence verdict.",
            },
        ],
    )

    steps = trajectory_from_execution(execution)

    assert [step.label for step in steps] == ["puck.noise_judge"]


def test_reviser_ignores_unsupported_llm_revision_type() -> None:
    fault = Localizer().localize(
        _make_execution(
            passed=False,
            assertions=[
                AssertionResult(id="constraint", source="skill", passed=False)
            ],
        ),
        current_skill_id="target",
    )
    assert fault is not None
    head = Linker().attribute(fault, current_skill_id="target")[0]
    reviser = Reviser(
        llm_client=lambda _: '{"revision_type":"delete_skill","after":"DELETE ALL"}'
    )

    body, revision_type, _ = reviser.revise(
        fault,
        current_body="# Skill body",
        attribution=head,
    )

    assert revision_type == "clarify_procedure"
    assert "DELETE ALL" not in body


def test_generator_appends_loader_hint_when_body_empty() -> None:
    fault = Localizer().localize(
        _make_execution(
            passed=False,
            condition="without_skill",
            assertions=[
                AssertionResult(id="task", source="task", passed=False, detail="miss")
            ],
        ),
        current_skill_id="target",
    )
    assert fault is not None

    generator = Generator()
    body = generator.generate(fault, current_body="", skill_id="target")

    assert "Loader hint" in body
    assert "target" in body


def test_qualifier_rejects_regression() -> None:
    baseline = _make_execution(passed=True, condition="without_skill")
    candidate = _make_execution(
        passed=False,
        assertions=[
            AssertionResult(id="fail", source="skill", passed=False, detail="regressed")
        ],
    )
    candidate = candidate.model_copy(update={"regression_rate": 0.2})

    verdict = Qualifier().qualify(baseline=baseline, candidate=candidate)

    assert verdict.adopt is False
    assert verdict.regression_detected is True
    assert verdict.delta_pass_rate < 0


def test_qualifier_adopts_when_candidate_improves() -> None:
    baseline = _make_execution(passed=False, condition="without_skill")
    candidate = _make_execution(passed=True)

    verdict = Qualifier().qualify(baseline=baseline, candidate=candidate)

    assert verdict.adopt is True
    assert verdict.delta_pass_rate == 0.5
    assert verdict.regression_detected is False


# ---------------------------------------------------------------------------
# Graph-level integration – confirm the adaptor fields flow through the run
# ---------------------------------------------------------------------------


def test_run_service_populates_adaptor_fields(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path

    result = service.run(
        RunRequest(
            skill_id="spreadsheet-summary",
            executor="fixture",
            scenario="content-gap",
        )
    )

    assert result["status"] == "passed"
    attribution = result["attribution"]
    # Legacy contract remains intact
    assert attribution["taxonomy"] == "Content Gap"
    # New Skill-Adaptor fields are populated
    assert attribution["fault_type"] == "skill_wrong"
    assert attribution["t_star"] == 0
    assert attribution["improvement_principle"]
    assert attribution["skill_attributions"]
    assert attribution["skill_attributions"][0]["action"] == "revise"

    patch = result["repair_patch"]
    assert patch["repair_mode"] == "revise"
    assert patch["revision_type"]
    assert patch["principle"]

    verification = result["verification"]
    assert verification["decision"] == "ADOPT"
    assert verification["regression_detected"] is False
    assert verification["sample_size"] >= 1
    assert verification["qualifier_reason"]


def test_platform_failure_records_reasoning_wrong(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path

    result = service.run(
        RunRequest(executor="fixture", scenario="network-error")
    )

    assert result["status"] == "failed"
    attribution = result["attribution"]
    assert attribution["cause"] == "platform"
    assert attribution["fault_type"] == "reasoning_wrong"
    assert "repair_patch" not in result
