"""
Skill-Adaptor style attribution / repair / qualification helpers.

This module ports the three-stage design of the upstream
`zjunlp/SkillAdaptor` project into Skill-Doctor:

  1. Attribution   - ``Localizer``  identifies the first failing step (t*),
                     classifies the fault type, and drafts an improvement
                     principle. ``Linker`` scores which skill is at fault.

  2. Modification  - ``Generator``  drafts a brand-new skill block for the
                     ``skill_missing`` case. ``Reviser`` produces a targeted
                     patch (structured revision types) for the
                     ``skill_wrong`` / ``reasoning_wrong`` cases.

  3. Qualification - ``Qualifier``  decides ``ADOPT`` vs ``REJECT`` based on
                     baseline / candidate deltas plus a regression guard.

All classes accept an optional ``llm_client`` callable of the shape
``Callable[[str], str]`` (prompt in, response out) so that adopters can
plug an OpenAI / Codex / Anthropic client without pulling any SDK into
Skill-Doctor. When no LLM client is provided, deterministic rule-based
fallbacks operate on the ``ExecutionResult`` signals already available
inside the graph so unit tests and offline benchmarks remain reproducible.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import ExecutionResult


LOGGER = logging.getLogger("skilldoctor.adaptor")
MAX_LLM_TRAJECTORY_STEPS = 24
MAX_STEP_DETAIL_CHARS = 500
MAX_SKILL_BODY_CHARS = 6_000
MAX_LLM_JSON_CHARS = 20_000
_PLACEHOLDER_TEXT = {
    "",
    "(no action)",
    "(end)",
    "(no transcript captured)",
    "(assistant response)",
}
_SYNTHETIC_STAGE_PREFIXES = (
    "agent.analyze",
    "prepare",
    "execute",
    "collect_evidence",
    "attribute",
    "repair",
    "verify",
    "promote",
    "finalize",
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# An LLM client is any callable that receives a prompt and returns a string.
LLMClient = Callable[[str], str]


class FaultType(str, Enum):
    """Ported from ``skill-adaptor.core.types.FaultType``."""

    SKILL_WRONG = "skill_wrong"
    SKILL_MISSING = "skill_missing"
    REASONING_WRONG = "reasoning_wrong"
    UNKNOWN = "unknown"


@dataclass
class TrajectoryStep:
    """A single step derived from ``ExecutionResult`` for localisation."""

    index: int
    source: str  # "task" | "skill" | "system" | "runtime"
    label: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "source": self.source,
            "label": self.label,
            "passed": self.passed,
            "detail": self.detail,
        }

    def prompt_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "source": self.source,
            "label": self.label,
            "passed": self.passed,
            "detail": _truncate(self.detail, MAX_STEP_DETAIL_CHARS),
        }


@dataclass
class LocalizedFault:
    """Ported subset of ``skill-adaptor.core.types.LocalizedFault``.

    ``t_star`` is 0-based to match Python indexing conventions of the
    upstream project.
    """

    fault_type: FaultType
    t_star: int
    fault_chain: List[int] = field(default_factory=list)
    improvement_principle: str = ""
    wrong_action: str = ""
    observation: str = ""
    steps: List[TrajectoryStep] = field(default_factory=list)
    reason: str = ""
    # Provenance of this localization: "llm" when DeepSeek/OpenAI produced
    # it, "rule-based" for the deterministic fallback, "none" when the
    # Localizer decided the run was fully healthy.
    source: str = "rule-based"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "fault_type": self.fault_type.value,
            "t_star": self.t_star,
            "fault_chain": self.fault_chain,
            "improvement_principle": self.improvement_principle,
            "wrong_action": self.wrong_action,
            "observation": self.observation,
            "reason": self.reason,
            "steps": [s.as_dict() for s in self.steps],
        }

    def prompt_dict(self, *, include_steps: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "fault_type": self.fault_type.value,
            "t_star": self.t_star,
            "fault_chain": self.fault_chain,
            "improvement_principle": self.improvement_principle,
            "wrong_action": self.wrong_action,
            "observation": _truncate(self.observation, MAX_STEP_DETAIL_CHARS),
            "reason": _truncate(self.reason, MAX_STEP_DETAIL_CHARS),
        }
        if include_steps:
            payload["steps"] = [
                step.prompt_dict()
                for step in _select_prompt_steps(self.steps)
            ]
        return payload


@dataclass
class SkillAttribution:
    """Ported from ``skill-adaptor.core.types.SkillAttribution``."""

    skill_id: str
    weight: float  # 0.0 = not at fault, 1.0 = fully at fault
    reason: str = ""
    action: str = "revise"  # "revise" | "generate" | "loader" | "split"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "weight": self.weight,
            "reason": self.reason,
            "action": self.action,
        }


@dataclass
class QualifierVerdict:
    """Adopt / reject decision produced by the qualifier."""

    adopt: bool
    delta_pass_rate: float
    delta_avg_score: float
    regression_detected: bool
    sample_size: int
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "adopt": self.adopt,
            "delta_pass_rate": self.delta_pass_rate,
            "delta_avg_score": self.delta_avg_score,
            "regression_detected": self.regression_detected,
            "sample_size": self.sample_size,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Helpers - trajectory extraction
# ---------------------------------------------------------------------------

def trajectory_from_execution(execution: ExecutionResult) -> List[TrajectoryStep]:
    """Flatten ``ExecutionResult`` into a linear list of trajectory steps.

    Runtime events (when present) are placed first, followed by assertion
    outcomes. This preserves temporal ordering: the actual tool / model
    calls executed first, and only afterwards the verifier issued
    assertions. Assertion sources are surfaced so downstream components
    can distinguish skill-owned from platform-owned failures.
    """

    steps: List[TrajectoryStep] = []
    for idx, event in enumerate(execution.runtime_events):
        if not _is_actionable_runtime_event(event):
            continue
        status = str(event.get("status", "completed"))
        passed = status not in {"failed", "error"}
        label = str(event.get("stage") or event.get("type") or f"runtime[{idx}]")
        detail = str(event.get("message") or event.get("detail") or "")
        steps.append(
            TrajectoryStep(
                index=len(steps),
                source="runtime",
                label=label,
                passed=passed,
                detail=detail,
            )
        )
    for assertion in execution.assertions:
        steps.append(
            TrajectoryStep(
                index=len(steps),
                source=assertion.source,
                label=assertion.id,
                passed=assertion.passed,
                detail=assertion.detail or "",
            )
        )
    return steps


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _is_placeholder_text(value: Any) -> bool:
    return str(value or "").strip().lower() in _PLACEHOLDER_TEXT


def _is_actionable_runtime_event(event: Dict[str, Any]) -> bool:
    stage = str(event.get("stage") or event.get("type") or "").strip()
    if any(
        stage == prefix or stage.startswith(f"{prefix}.")
        for prefix in _SYNTHETIC_STAGE_PREFIXES
    ):
        return False
    status = str(event.get("status") or "completed").lower()
    message = event.get("message") or event.get("detail") or ""
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if status in {"failed", "error"}:
        return True
    if status in {"started", "skipped"} and _is_placeholder_text(message):
        return False
    return bool(stage or not _is_placeholder_text(message) or metadata)


def _select_prompt_steps(steps: List[TrajectoryStep]) -> List[TrajectoryStep]:
    if len(steps) <= MAX_LLM_TRAJECTORY_STEPS:
        return steps
    failing_indices = [index for index, step in enumerate(steps) if not step.passed]
    if not failing_indices:
        return steps[:MAX_LLM_TRAJECTORY_STEPS]
    first_failure = failing_indices[0]
    before = max(0, first_failure - 8)
    after = min(len(steps), first_failure + 8)
    window = steps[before:after]
    if len(window) < MAX_LLM_TRAJECTORY_STEPS:
        remainder = MAX_LLM_TRAJECTORY_STEPS - len(window)
        window = [*steps[:remainder], *window]
    return window[:MAX_LLM_TRAJECTORY_STEPS]


def _format_trajectory_for_prompt(steps: List[TrajectoryStep]) -> str:
    selected = _select_prompt_steps(steps)
    omitted = len(steps) - len(selected)
    lines = [
        f"Step {s.index}: [{s.source}/{s.label}] "
        f"{'PASS' if s.passed else 'FAIL'} - "
        f"{_truncate(s.detail, MAX_STEP_DETAIL_CHARS)}"
        for s in selected
    ]
    if omitted > 0:
        lines.append(f"... omitted {omitted} low-signal step(s) for prompt budget ...")
    return "\n".join(lines)


def _trim_skill_body(body: str) -> str:
    return _truncate(body or "", MAX_SKILL_BODY_CHARS)


def _first_failing_index(steps: List[TrajectoryStep]) -> int:
    for step in steps:
        if not step.passed:
            return step.index
    return -1


def _all_failing_indices(steps: List[TrajectoryStep]) -> List[int]:
    return [step.index for step in steps if not step.passed]


# ---------------------------------------------------------------------------
# Localizer
# ---------------------------------------------------------------------------

_LOCALIZER_PROMPT_TEMPLATE = """# Fault Localisation

You are the Localizer stage of a Skill-Doctor style attribution pipeline.

## Task
{task}

## Current Skill Content
{skill_content}

## Trajectory
{trajectory}

## Fault-type definitions
- skill_wrong: the referenced skill guided the agent incorrectly (revise it).
- skill_missing: no skill covered this scenario (generate a new skill).
- reasoning_wrong: the agent had adequate skills but made a wrong choice (soft patch).

## Response (JSON only)
{{"fault_type": "skill_wrong|skill_missing|reasoning_wrong",
  "t_star": <0-based step index>,
  "fault_chain": [<indices>],
  "improvement_principle": "<one concise sentence>",
  "reason": "<why this classification>"}}
"""


class Localizer:
    """Identify the first mistake (t*) and classify its fault type.

    Adapted from ``skill-adaptor.core.localizer.Localizer``. The upstream
    project relies entirely on LLM output; Skill-Doctor also supports a
    deterministic fallback so offline unit tests can exercise the graph.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def localize(
        self,
        execution: ExecutionResult,
        *,
        task: str = "",
        current_skill_id: str = "",
        current_skill_content: str = "",
        force_llm: bool = False,
    ) -> Optional[LocalizedFault]:
        if not force_llm:
            if execution.passed and not any(
                (not a.passed) for a in execution.assertions
            ):
                return None

        steps = trajectory_from_execution(execution)
        if not steps:
            return None

        if self.llm_client is not None:
            fault = self._localize_with_llm(
                execution,
                steps,
                task,
                current_skill_content,
            )
            if fault is not None:
                LOGGER.info(
                    "Localizer: LLM invoked → fault_type=%s t*=%s conclusion=%r",
                    fault.fault_type.value,
                    fault.t_star,
                    (fault.improvement_principle or "")[:160],
                )
                return fault
        return self._localize_rule_based(execution, steps, current_skill_id)

    # ------------------------------------------------------------------
    def _localize_rule_based(
        self,
        execution: ExecutionResult,
        steps: List[TrajectoryStep],
        current_skill_id: str,
    ) -> LocalizedFault:
        t_star = _first_failing_index(steps)
        if t_star < 0:
            t_star = len(steps) - 1
        chain = _all_failing_indices(steps) or [t_star]
        failing_step = steps[t_star]

        # -- Fault type classification --
        error_text = (execution.error or "").lower()
        platform_signal = (
            error_text
            and (
                "network" in error_text
                or "timeout" in error_text
                or "reset" in error_text
                or "connection" in error_text
            )
        )
        loading_miss = execution.condition == "without_skill"

        failed_skill = [
            a for a in execution.assertions
            if not a.passed and a.source == "skill"
        ]
        failed_task = [
            a for a in execution.assertions
            if not a.passed and a.source == "task"
        ]
        failed_system = [
            a for a in execution.assertions
            if not a.passed and a.source == "system"
        ]

        if platform_signal:
            fault_type = FaultType.REASONING_WRONG
            principle = (
                "Platform / network failure - retry with backoff instead of "
                "mutating the skill."
            )
            reason = "Execution reported an infrastructure error."
        elif loading_miss:
            fault_type = FaultType.SKILL_MISSING
            principle = (
                f"Always load `{current_skill_id or 'the target skill'}` before "
                "attempting this task; without-skill execution regressed the "
                "verifier."
            )
            reason = (
                "Baseline ran without the target skill, so the loader must be "
                "the intervention point."
            )
        elif failed_skill:
            fault_type = FaultType.SKILL_WRONG
            principle = (
                f"Reinforce {failed_skill[0].id} so that "
                f"{failed_skill[0].detail or 'the checked constraint'} "
                "is guaranteed by the procedure."
            )
            reason = (
                "Skill-owned assertions failed while the skill was loaded - "
                "content of the skill is the root cause."
            )
        elif failed_task and not failed_system:
            fault_type = FaultType.SKILL_WRONG
            principle = (
                "Extend the skill to explicitly enforce the failing task "
                "requirement."
            )
            reason = "Task-owned assertion failed while system checks passed."
        elif failed_system and not failed_task and not failed_skill:
            fault_type = FaultType.REASONING_WRONG
            principle = (
                "System check failed but no skill or task assertion did - "
                "prefer platform / reasoning fixes over skill mutation."
            )
            reason = "Only system-owned assertions failed."
        else:
            fault_type = FaultType.SKILL_MISSING
            principle = (
                "No matching skill fires for this scenario - synthesise a "
                "guidance block that covers it."
            )
            reason = "No skill or task assertion owns the failure."

        return LocalizedFault(
            fault_type=fault_type,
            t_star=t_star,
            fault_chain=chain,
            improvement_principle=principle,
            wrong_action=failing_step.label,
            observation=failing_step.detail,
            steps=steps,
            reason=reason,
        )

    # ------------------------------------------------------------------
    def _localize_with_llm(
        self,
        execution: ExecutionResult,
        steps: List[TrajectoryStep],
        task: str,
        skill_content: str,
    ) -> Optional[LocalizedFault]:
        client = self.llm_client
        if client is None:
            return None
        trajectory_str = _format_trajectory_for_prompt(steps)
        prompt = _LOCALIZER_PROMPT_TEMPLATE.format(
            task=task or "(no task description provided)",
            skill_content=_trim_skill_body(skill_content) or "(no skill content provided)",
            trajectory=trajectory_str,
        )
        try:
            raw = client(prompt)
            payload = _extract_json(raw, context="localizer")
            if not payload:
                return None
            ft = FaultType(payload.get("fault_type", "unknown"))
            t_star = int(payload.get("t_star", 0))
            chain = [int(x) for x in payload.get("fault_chain", []) or [t_star]]
            principle = str(payload.get("improvement_principle", "")).strip()
            reason = str(payload.get("reason", "")).strip()
            if t_star < 0 or t_star >= len(steps):
                t_star = max(0, _first_failing_index(steps))
            failing_step = steps[t_star]
            return LocalizedFault(
                fault_type=ft,
                t_star=t_star,
                fault_chain=chain,
                improvement_principle=principle,
                wrong_action=failing_step.label,
                observation=failing_step.detail,
                steps=steps,
                reason=reason,
                source="llm",
            )
        except Exception as error:  # pragma: no cover - LLM parsing is best-effort
            LOGGER.warning("Localizer LLM output rejected: %s", error)
            return None


# ---------------------------------------------------------------------------
# Linker
# ---------------------------------------------------------------------------

class Linker:
    """Score which skill is responsible for the fault.

    Adapted from ``skill-adaptor.core.linker.Linker``. Skill-Doctor
    currently pipelines exactly one skill through the graph, so the linker
    always returns a single attribution but records a ``weight`` /
    ``action`` explaining how downstream nodes should behave.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def attribute(
        self,
        fault: LocalizedFault,
        *,
        current_skill_id: str,
        candidate_skills: Optional[List[str]] = None,
    ) -> List[SkillAttribution]:
        candidates = list(candidate_skills or [])
        if current_skill_id and current_skill_id not in candidates:
            candidates.insert(0, current_skill_id)
        if not candidates:
            return []

        if fault.fault_type is FaultType.SKILL_WRONG:
            weight, action, reason = (
                0.9,
                "revise",
                "Skill-owned assertion failed while skill was loaded.",
            )
        elif fault.fault_type is FaultType.SKILL_MISSING:
            weight, action, reason = (
                0.6,
                "generate",
                "No skill matches the failing scenario - draft a new one.",
            )
        elif fault.fault_type is FaultType.REASONING_WRONG:
            weight, action, reason = (
                0.25,
                "split",
                "Reasoning-level failure, skill body is only tangentially "
                "responsible.",
            )
        else:
            weight, action, reason = 0.0, "split", "Fault type unclassified."

        head = SkillAttribution(
            skill_id=candidates[0],
            weight=weight,
            reason=reason,
            action=action,
        )
        rest = [
            SkillAttribution(
                skill_id=sid,
                weight=max(0.0, weight - 0.3),
                reason="Adjacent skill listed for completeness.",
                action="split",
            )
            for sid in candidates[1:]
        ]
        return [head, *rest]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

_GENERATOR_PROMPT_TEMPLATE = """# Skill Generation

You are the Generator stage of a Skill-Doctor style repair pipeline.

## Task
{task}

## Fault
{fault_json}

## Existing skill body (may be empty)
{skill_body}

## Response
Return the full body of a NEW markdown skill block that covers this
scenario. Include an `## When to apply` section and a numbered
`## Procedure` section. Do not wrap in JSON.
"""


class Generator:
    """Draft a brand-new skill block for the ``skill_missing`` case.

    Adapted from ``skill-adaptor.core.generator.Generator``. The Generator
    is only invoked when the Linker recommends ``action == "generate"``.
    """

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def generate(
        self,
        fault: LocalizedFault,
        *,
        current_body: str,
        task: str = "",
        skill_id: str = "",
    ) -> str:
        if self.llm_client is not None:
            try:
                prompt = _GENERATOR_PROMPT_TEMPLATE.format(
                    task=task or "(no task provided)",
                    fault_json=json.dumps(
                        fault.prompt_dict(include_steps=True),
                        indent=2,
                    ),
                    skill_body=_trim_skill_body(current_body) or "(empty)",
                )
                text = self.llm_client(prompt)
                if text and text.strip():
                    return text.strip()
            except Exception as error:  # pragma: no cover
                LOGGER.warning("Generator LLM output rejected: %s", error)

        header = f"## Loader hint for `{skill_id}`" if skill_id else "## Loader hint"
        block = (
            f"\n\n{header}\n"
            f"\nAlways install and load the target Skill before executing the "
            "target task.\n"
            f"\n### Why\n{fault.improvement_principle or fault.reason}\n"
        )
        base = current_body or ""
        if block.strip() in base:
            return base
        return f"{base.rstrip()}{block}"


# ---------------------------------------------------------------------------
# Reviser
# ---------------------------------------------------------------------------

_REVISER_PROMPT_TEMPLATE = """# Skill Revision

You are the Reviser stage of a Skill-Doctor style repair pipeline.

## Existing skill body
{skill_body}

## Fault
{fault_json}

## Response (JSON only)
{{"revision_type": "add_precondition|add_negative_example|clarify_procedure|add_validation|loader_hint",
  "after": "<text to insert>",
  "section": "<optional section header to target>"}}
"""


class Reviser:
    """Apply a targeted, structured patch to an existing skill body.

    Adapted from ``skill-adaptor.core.reviser.Reviser`` - we keep the same
    ``revision_type`` vocabulary (``add_precondition``,
    ``add_negative_example``, ``clarify_procedure``, ``add_validation``)
    plus a ``loader_hint`` variant that mirrors Skill-Doctor's existing
    ``patch_loader`` action.
    """

    DEFAULT_REVISION_TYPES = (
        "add_precondition",
        "add_negative_example",
        "clarify_procedure",
        "add_validation",
        "loader_hint",
    )

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm_client = llm_client

    def revise(
        self,
        fault: LocalizedFault,
        *,
        current_body: str,
        attribution: SkillAttribution,
        execution: Optional[ExecutionResult] = None,
    ) -> Tuple[str, str, str]:
        """Return ``(new_body, revision_type, patch_note)``."""

        revision_type, patch_text = self._decide_revision(fault, execution)
        if self.llm_client is not None:
            try:
                payload = _extract_json(
                    self.llm_client(
                        _REVISER_PROMPT_TEMPLATE.format(
                            skill_body=_trim_skill_body(current_body),
                            fault_json=json.dumps(
                                fault.prompt_dict(include_steps=True),
                                indent=2,
                            ),
                        )
                    ),
                    context="reviser",
                )
                if payload:
                    proposed_type = str(payload.get("revision_type", revision_type))
                    accepted_payload = proposed_type in self.DEFAULT_REVISION_TYPES
                    if proposed_type in self.DEFAULT_REVISION_TYPES:
                        revision_type = proposed_type
                    else:
                        LOGGER.warning(
                            "Reviser ignored unsupported revision_type=%r",
                            proposed_type,
                        )
                    after = str(payload.get("after", "")).strip()
                    if accepted_payload and after:
                        patch_text = after
            except Exception as error:  # pragma: no cover
                LOGGER.warning("Reviser LLM output rejected: %s", error)

        new_body = self._apply_revision(
            current_body,
            revision_type=revision_type,
            patch_text=patch_text,
            fault=fault,
        )
        note = (
            f"revision_type={revision_type}; "
            f"weight={attribution.weight:.2f}; "
            f"principle={fault.improvement_principle}"
        )
        return new_body, revision_type, note

    # ------------------------------------------------------------------
    def _decide_revision(
        self,
        fault: LocalizedFault,
        execution: Optional[ExecutionResult],
    ) -> Tuple[str, str]:
        if fault.fault_type is FaultType.SKILL_MISSING:
            return (
                "loader_hint",
                "Always install and load this Skill before executing the "
                "target task.",
            )
        if execution is not None:
            failed_checks = [
                a for a in execution.assertions
                if not a.passed and a.source in {"skill", "system"}
            ]
            if failed_checks and execution.executor == "codex-sdk-live":
                lines = "\n".join(
                    f"- {a.id}: {a.detail or 'Satisfy this check.'}"
                    for a in failed_checks
                )
                return (
                    "add_validation",
                    "The following requirements must be explicit in the "
                    "result or satisfy the verifier:\n" + lines,
                )
        if fault.fault_type is FaultType.REASONING_WRONG:
            return (
                "add_negative_example",
                fault.improvement_principle
                or "Do not proceed without re-checking the wrong action at t*.",
            )
        # Default: clarify procedure with the improvement principle
        principle = fault.improvement_principle or (
            "Process the complete input, not only preview rows, before "
            "computing or verifying the final result."
        )
        return ("clarify_procedure", principle)

    # ------------------------------------------------------------------
    def _apply_revision(
        self,
        body: str,
        *,
        revision_type: str,
        patch_text: str,
        fault: LocalizedFault,
    ) -> str:
        base = body or ""
        # Idempotence: never insert the same instruction twice.
        if patch_text and patch_text.strip() in base:
            return base

        if revision_type == "loader_hint":
            block = f"\n{patch_text.strip()}"
            return f"{base.rstrip()}{block}"

        if revision_type == "add_precondition":
            section = "## Preconditions"
            block = f"\n\n{section}\n\n- {patch_text.strip()}\n"
            if section not in base:
                return f"{base.rstrip()}{block}"
            return base.replace(section, f"{section}\n\n- {patch_text.strip()}")

        if revision_type == "add_negative_example":
            block = (
                f"\n\n## Negative example (step {fault.t_star})\n\n"
                f"**Do NOT:** {fault.wrong_action or 'repeat the failing action'}\n\n"
                f"**Why it fails:** {patch_text.strip()}\n"
            )
            return f"{base.rstrip()}{block}"

        if revision_type == "add_validation":
            section = "## Verification addendum"
            block = f"\n\n{section}\n{patch_text.strip()}\n"
            if section not in base:
                return f"{base.rstrip()}{block}"
            return base.replace(section, f"{section}\n{patch_text.strip()}")

        # clarify_procedure (default)
        block = f"\n{patch_text.strip()}"
        return f"{base.rstrip()}{block}"


# ---------------------------------------------------------------------------
# Qualifier
# ---------------------------------------------------------------------------

@dataclass
class QualifierConfig:
    success_delta_threshold: float = 0.0  # candidate must strictly improve
    regression_threshold: float = 0.0     # any regression rejects the patch
    min_sample_size: int = 1


class Qualifier:
    """Decide whether to adopt or reject the revised skill.

    Adapted from ``skill-adaptor.core.validator.Validator``. Skill-Doctor
    already has a lightweight verify node - this class centralises the
    logic so it can be reused by benchmark harnesses and future
    multi-sample validation.
    """

    def __init__(self, config: Optional[QualifierConfig] = None) -> None:
        self.config = config or QualifierConfig()

    def qualify(
        self,
        baseline: ExecutionResult,
        candidate: ExecutionResult,
    ) -> QualifierVerdict:
        delta_pass = candidate.pass_rate - baseline.pass_rate
        # Skill-Doctor executions do not currently expose ``avg_score``;
        # ``pass_rate`` is a reasonable stand-in.
        delta_avg = delta_pass
        regression = (
            candidate.regression_rate > self.config.regression_threshold
        )
        adopt = (
            candidate.passed
            and delta_pass > self.config.success_delta_threshold
            and not regression
        )
        parts: List[str] = [
            f"pass_rate_delta={delta_pass:+.2%}",
            f"regression_rate={candidate.regression_rate:.2%}",
        ]
        if not candidate.passed:
            parts.append("candidate still has failing checks")
        if regression:
            parts.append("regression above tolerance")
        return QualifierVerdict(
            adopt=bool(adopt),
            delta_pass_rate=delta_pass,
            delta_avg_score=delta_avg,
            regression_detected=bool(regression),
            sample_size=max(self.config.min_sample_size, 1),
            reason="; ".join(parts),
        )


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def _extract_json(
    raw: str,
    *,
    context: str = "LLM response",
) -> Optional[Dict[str, Any]]:
    """Best-effort JSON extraction from a possibly noisy LLM response."""

    if not raw:
        return None
    text = raw.strip()[:MAX_LLM_JSON_CHARS]
    # Strip common code fences
    text = re.sub(r"^```(?:json)?", "", text)
    text = re.sub(r"```$", "", text.strip())
    errors: List[str] = []
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError(f"{context} JSON must be an object")
        return payload
    except Exception as error:
        errors.append(str(error))
        candidates = []
        if "```" in raw:
            candidates.extend(
                match.group(1)
                for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw)
            )
        candidates.extend(_json_object_candidates(text))
        seen: set[str] = set()
        for candidate in candidates:
            candidate = candidate.strip()[:MAX_LLM_JSON_CHARS]
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                payload = json.loads(candidate)
                if not isinstance(payload, dict):
                    raise ValueError(f"{context} JSON must be an object")
                return payload
            except Exception as candidate_error:
                errors.append(str(candidate_error))
        preview = text.replace("\n", " ")[:300]
        LOGGER.warning(
            "Could not parse %s JSON: %s; preview=%r",
            context,
            "; ".join(errors[:3]),
            preview,
        )
        return None


def _json_object_candidates(text: str) -> List[str]:
    candidates: List[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if escape:
            escape = False
            continue
        if char == "\\" and in_string:
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    return candidates


__all__ = [
    "FaultType",
    "TrajectoryStep",
    "LocalizedFault",
    "SkillAttribution",
    "QualifierVerdict",
    "QualifierConfig",
    "Localizer",
    "Linker",
    "Generator",
    "Reviser",
    "Qualifier",
    "LLMClient",
    "trajectory_from_execution",
]
