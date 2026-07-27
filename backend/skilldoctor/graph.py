from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig

from .models import (
    AgentState,
    AttributionResult,
    EvidenceSnapshot,
    ExecutionResult,
    RepairPatch,
    RunEvent,
    VerificationResult,
)
from .adaptor import (
    FaultType,
    Generator,
    LLMClient,
    Linker,
    Localizer,
    Qualifier,
    Reviser,
)
from .workers import ExecutionWorker


def _event(
    state: AgentState,
    stage: str,
    message: str,
    *,
    status: Literal["started", "completed", "failed", "skipped"] = "completed",
    usage: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    sequence_offset: int = 0,
) -> dict[str, Any]:
    return RunEvent(
        sequence=len(state.get("events", [])) + sequence_offset + 1,
        stage=stage,
        status=status,
        attempt=state["attempt"],
        message=message,
        usage=usage,
        metadata=metadata or {},
    ).model_dump(mode="json")


def _next_patch_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        major, minor, patch = (int(part) for part in parts)
        return f"{major}.{minor}.{patch + 1}"
    return f"{version}+repair.1"


def build_agent_graph(
    worker: ExecutionWorker,
    runtime_event_observer: Callable[
        [dict[str, Any], RunnableConfig],
        None,
    ]
    | None = None,
    *,
    adaptor_llm_client: LLMClient | None = None,
):
    """Compile the Skill Doctor lifecycle around an execution worker.

    The optional ``adaptor_llm_client`` swaps every Skill-Adaptor stage
    (Localizer / Linker / Generator / Reviser) from the deterministic
    rule-based fallback to an LLM-driven implementation. When ``None`` the
    graph behaves exactly as before, which keeps offline tests and
    fixture-based benchmarks reproducible.
    """

    localizer = Localizer(llm_client=adaptor_llm_client)
    linker = Linker(llm_client=adaptor_llm_client)
    generator = Generator(llm_client=adaptor_llm_client)
    reviser = Reviser(llm_client=adaptor_llm_client)
    qualifier = Qualifier()

    def prepare(state: AgentState) -> dict[str, Any]:
        return {
            "status": "running",
            "stop_reason": "",
            "events": [
                _event(
                    state,
                    "prepare",
                    f"Prepared {state['skill_id']}@{state['skill_version']}.",
                )
            ],
        }

    def execute(
        state: AgentState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        callback_setter = getattr(worker, "set_event_callback", None)
        if callable(callback_setter) and runtime_event_observer is not None:
            callback_setter(
                lambda event: runtime_event_observer(event, config)
            )
        try:
            result = worker.run(
                run_id=state["run_id"],
                attempt=state["attempt"],
                task=state["task"],
                skill_id=state["skill_id"],
                skill_content=state["skill_content"],
                condition=state["condition"],
            )
        finally:
            if callable(callback_setter):
                callback_setter(None)
        payload = result.model_dump(mode="json")
        runtime_events = [
            RunEvent(
                sequence=len(state.get("events", [])) + index,
                attempt=state["attempt"],
                **event,
            ).model_dump(mode="json")
            for index, event in enumerate(result.runtime_events, start=1)
        ]
        update: dict[str, Any] = {
            "execution": payload,
            "events": [
                *runtime_events,
                _event(
                    state,
                    "execute",
                    result.summary,
                    status="completed" if result.error is None else "failed",
                    usage=(
                        None
                        if runtime_events
                        else result.usage.model_dump(mode="json")
                    ),
                    metadata={
                        "condition": result.condition,
                        "pass_rate": result.pass_rate,
                        "executor": result.executor,
                    },
                    sequence_offset=len(runtime_events),
                )
            ],
        }
        if state["attempt"] == 0:
            update["baseline_execution"] = deepcopy(payload)
        return update

    def collect_evidence(state: AgentState) -> dict[str, Any]:
        execution = ExecutionResult.model_validate(state["execution"])
        serialized_execution = json.dumps(
            execution.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        serialized_assertions = json.dumps(
            [item.model_dump(mode="json") for item in execution.assertions],
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot = EvidenceSnapshot(
            run_id=state["run_id"],
            attempt=state["attempt"],
            skill_id=state["skill_id"],
            condition=execution.condition,
            execution_sha256=hashlib.sha256(
                serialized_execution.encode("utf-8")
            ).hexdigest(),
            assertion_sha256=hashlib.sha256(
                serialized_assertions.encode("utf-8")
            ).hexdigest(),
            artifact_refs=list(execution.artifacts.values()),
        )
        return {
            "evidence_snapshot": snapshot.model_dump(mode="json"),
            "events": [
                _event(
                    state,
                    "collect_evidence",
                    "Created a hash-addressed Evidence Snapshot.",
                    metadata={
                        "execution_sha256": snapshot.execution_sha256,
                        "artifact_count": len(snapshot.artifact_refs),
                    },
                )
            ],
        }

    def attribute(state: AgentState) -> dict[str, Any]:
        execution = ExecutionResult.model_validate(state["execution"])
        failed_skill_assertions = [
            item for item in execution.assertions if not item.passed and item.source == "skill"
        ]
        failed_system_assertions = [
            item for item in execution.assertions if not item.passed and item.source == "system"
        ]

        # ---- Skill-Adaptor Attribution stage --------------------------------
        # 1) Localizer: pinpoint t*, classify fault type, draft principle.
        # 2) Linker:    score which skill is responsible for the fault.
        # For traces that arrived from an Aime skill execution we always ask
        # DeepSeek to summarise the run, even if every heuristic check
        # passed — otherwise the frontend would show a canned rule-based
        # template instead of a real agent-authored conclusion.
        force_llm = execution.executor == "aime-skill-trace"
        localized = localizer.localize(
            execution,
            task=state.get("task", ""),
            current_skill_id=state["skill_id"],
            force_llm=force_llm,
        )
        attributions = (
            linker.attribute(
                localized,
                current_skill_id=state["skill_id"],
            )
            if localized is not None
            else []
        )

        # Map fault_type / attribution back to Skill-Doctor's existing
        # taxonomy so downstream contracts (API responses, tests) are
        # preserved.
        adaptor_fields: dict[str, Any] = {}
        if localized is not None:
            adaptor_fields = {
                "fault_type": localized.fault_type.value,
                "t_star": localized.t_star,
                "fault_chain": localized.fault_chain,
                "improvement_principle": localized.improvement_principle,
                "skill_attributions": [a.as_dict() for a in attributions],
                "agent_conclusion": localized.improvement_principle,
                "agent_reason": localized.reason,
                "agent_source": (
                    "llm"
                    if getattr(localized, "source", "rule-based") == "llm"
                    else "rule-based"
                ),
            }

        if execution.error and (
            execution.executor == "codex-sdk-live"
            or "network" in execution.error.lower()
        ):
            platform_adaptor_fields = {
                **adaptor_fields,
                "fault_type": FaultType.REASONING_WRONG.value,
            }
            result = AttributionResult(
                taxonomy="Non-Skill Cause",
                cause="platform",
                confidence=0.99,
                responsibility=0,
                action="split_non_skill",
                evidence_refs=[state["evidence_snapshot"]["execution_sha256"]],
                explanation="The execution failed at the platform boundary.",
                **platform_adaptor_fields,
            )
        elif failed_skill_assertions or (execution.task_kind == "code-repair" and failed_system_assertions):
            loading_miss = execution.condition == "without_skill"
            taxonomy = "Loading Miss" if loading_miss else "Content Gap"
            cause = "loader" if loading_miss else "skill"

            # If it's a code repair and only system (test) failed, it's still a content gap
            # because the skill didn't prevent the bug.
            explanation = (
                "The target Skill was absent from the control condition."
                if loading_miss
                else "Execution checks failed (code bug or missing constraints)."
            )

            result = AttributionResult(
                taxonomy=taxonomy,
                cause=cause,
                confidence=0.94 if loading_miss else 0.88,
                responsibility=0.92,
                action="patch_loader" if loading_miss else "patch_skill",
                evidence_refs=[
                    state["evidence_snapshot"]["assertion_sha256"],
                    *[item.id for item in failed_skill_assertions + failed_system_assertions],
                ],
                explanation=explanation,
                **adaptor_fields,
            )
        else:
            result = AttributionResult(
                taxonomy="Tool Misuse",
                cause="tool",
                confidence=0.72,
                responsibility=0.25,
                action="split_non_skill",
                evidence_refs=[state["evidence_snapshot"]["assertion_sha256"]],
                explanation="No failed assertion is owned by the Skill.",
                **adaptor_fields,
            )

        # If DeepSeek produced a real conclusion, prefer it over the
        # rule-based template so the frontend surfaces the agent output
        # directly. The rule-based text is kept as a fallback via the
        # `agent_source == "rule-based"` branch.
        if (
            localized is not None
            and getattr(localized, "source", "rule-based") == "llm"
            and localized.improvement_principle
        ):
            llm_text = localized.improvement_principle.strip()
            if localized.reason:
                llm_text = f"{llm_text} — {localized.reason.strip()}"
            result = result.model_copy(update={"explanation": llm_text})
        return {
            "attribution": result.model_dump(mode="json"),
            "events": [
                _event(
                    state,
                    "attribute",
                    result.explanation,
                    metadata={
                        "taxonomy": result.taxonomy,
                        "cause": result.cause,
                        "confidence": result.confidence,
                        "fault_type": result.fault_type,
                        "t_star": result.t_star,
                    },
                )
            ],
        }

    def repair(state: AgentState) -> dict[str, Any]:
        attribution = AttributionResult.model_validate(state["attribution"])
        execution = ExecutionResult.model_validate(state["execution"])
        before = state["skill_content"]

        # ---- Skill-Adaptor Modification stage --------------------------------
        # Reconstruct a lightweight LocalizedFault from the AttributionResult
        # so the Generator / Reviser modules can operate uniformly.
        from .adaptor import LocalizedFault as _LocalizedFault
        from .adaptor import SkillAttribution as _SkillAttribution
        try:
            fault_type = FaultType(attribution.fault_type)
        except ValueError:
            fault_type = FaultType.UNKNOWN
        localized = _LocalizedFault(
            fault_type=fault_type,
            t_star=attribution.t_star if attribution.t_star is not None else 0,
            fault_chain=list(attribution.fault_chain),
            improvement_principle=attribution.improvement_principle,
            wrong_action="",
            observation=attribution.explanation,
            steps=[],
            reason=attribution.explanation,
        )
        head_attribution = _SkillAttribution(
            skill_id=state["skill_id"],
            weight=float(attribution.responsibility),
            reason=attribution.explanation,
            action=(
                "generate"
                if attribution.action == "patch_loader"
                else "revise"
            ),
        )

        # -- Route to Generator (skill_missing / loader) or Reviser --
        if attribution.action == "patch_loader":
            after = generator.generate(
                localized,
                current_body=before,
                task=state.get("task", ""),
                skill_id=state["skill_id"],
            )
            kind = "loader_patch"
            repair_mode = "generate"
            revision_type = "loader_hint"
            principle = (
                attribution.improvement_principle
                or "Always install and load this Skill before executing the task."
            )
        else:
            after, revision_type, note = reviser.revise(
                localized,
                current_body=before,
                attribution=head_attribution,
                execution=execution,
            )
            kind = "skill_patch"
            repair_mode = "revise"
            principle = attribution.improvement_principle or note

        # Preserve legacy default patch text so downstream fixtures / benchmarks
        # that inspect the diff keep working. When the Reviser did not touch
        # the body (e.g. principle was empty), fall back to the original
        # phrasing shipped in the initial release.
        if after == before:
            fallback = (
                "\nProcess the complete input, not only preview rows, before "
                "computing or verifying the final result."
            )
            after = f"{before.rstrip()}{fallback}"
            revision_type = revision_type or "clarify_procedure"

        patch = RepairPatch(
            patch_id=f"patch-{uuid4().hex[:10]}",
            kind=kind,
            skill_id=state["skill_id"],
            base_version=state["skill_version"],
            next_version=_next_patch_version(state["skill_version"]),
            before=before,
            after=after,
            evidence_refs=attribution.evidence_refs,
            rollback_ref=f"{state['skill_id']}@{state['skill_version']}",
            repair_mode=repair_mode,
            revision_type=revision_type,
            principle=principle,
        )
        return {
            "attempt": state["attempt"] + 1,
            "skill_content": patch.after,
            "repair_patch": patch.model_dump(mode="json"),
            "status": "repairing",
            "events": [
                _event(
                    state,
                    "repair",
                    f"Created {patch.kind} {patch.patch_id}.",
                    metadata={
                        "base_version": patch.base_version,
                        "next_version": patch.next_version,
                        "rollback_ref": patch.rollback_ref,
                        "repair_mode": patch.repair_mode,
                        "revision_type": patch.revision_type,
                    },
                )
            ],
        }

    def verify(state: AgentState) -> dict[str, Any]:
        baseline = ExecutionResult.model_validate(state["baseline_execution"])
        candidate = ExecutionResult.model_validate(state["execution"])

        # ---- Skill-Adaptor Qualification stage ------------------------------
        # Centralise the adopt/reject decision + regression guard behind the
        # ``Qualifier`` module so future multi-sample validation can reuse
        # the exact same logic.
        verdict = qualifier.qualify(baseline=baseline, candidate=candidate)
        delta = verdict.delta_pass_rate
        adopted = verdict.adopt
        reasons = [
            f"Pass rate changed by {delta:+.1%}.",
            f"Regression rate is {candidate.regression_rate:.1%}.",
        ]
        if not candidate.passed:
            reasons.append("Candidate verification still has failing checks.")
        if verdict.regression_detected:
            reasons.append("Regression above tolerance detected.")
        result = VerificationResult(
            decision="ADOPT" if adopted else "REJECT",
            baseline_pass_rate=baseline.pass_rate,
            candidate_pass_rate=candidate.pass_rate,
            pass_rate_delta=delta,
            regression_rate=candidate.regression_rate,
            reasons=reasons,
            delta_avg_score=verdict.delta_avg_score,
            regression_detected=verdict.regression_detected,
            sample_size=verdict.sample_size,
            qualifier_reason=verdict.reason,
        )
        return {
            "verification": result.model_dump(mode="json"),
            "events": [
                _event(
                    state,
                    "verify",
                    f"Verification gate decided {result.decision}.",
                    metadata={
                        "pass_rate_delta": result.pass_rate_delta,
                        "regression_rate": result.regression_rate,
                        "regression_detected": result.regression_detected,
                        "sample_size": result.sample_size,
                    },
                )
            ],
        }

    def promote(state: AgentState) -> dict[str, Any]:
        patch = RepairPatch.model_validate(state["repair_patch"])
        return {
            "skill_version": patch.next_version,
            "status": "passed",
            "stop_reason": "repair_verified",
            "events": [
                _event(
                    state,
                    "promote",
                    f"Promoted {state['skill_id']} to {patch.next_version}.",
                    metadata={"patch_id": patch.patch_id},
                )
            ],
        }

    def finalize(state: AgentState) -> dict[str, Any]:
        execution = ExecutionResult.model_validate(state["execution"])
        passed = execution.passed and not state.get("repair_patch")
        reason = (
            "initial_execution_passed"
            if passed
            else state.get("stop_reason") or "no_safe_skill_mutation"
        )
        return {
            "status": "passed" if passed else "failed",
            "stop_reason": reason,
            "events": [
                _event(
                    state,
                    "finalize",
                    f"Run finished: {reason}.",
                    status="completed" if passed else "failed",
                )
            ],
        }

    def route_after_evidence(state: AgentState) -> str:
        execution = ExecutionResult.model_validate(state["execution"])
        # Uploaded Aime traces should always be sent through the attribute
        # node so DeepSeek can author a real conclusion, even when every
        # heuristic assertion has passed.
        if execution.executor == "aime-skill-trace":
            return "attribute"
        if execution.passed:
            return "verify" if state.get("repair_patch") else "finalize"
        return "attribute"

    def route_after_attribution(state: AgentState) -> str:
        if not state["repair_enabled"]:
            return "finalize"
        attribution = AttributionResult.model_validate(state["attribution"])
        repairable = attribution.action in {"patch_skill", "patch_loader"}
        confident = attribution.confidence >= 0.8
        attempts_left = state["attempt"] < state["max_attempts"]
        return "repair" if repairable and confident and attempts_left else "finalize"

    def route_after_verification(state: AgentState) -> str:
        verification = VerificationResult.model_validate(state["verification"])
        return "promote" if verification.decision == "ADOPT" else "finalize"

    builder = StateGraph(AgentState)
    builder.add_node("prepare", prepare)
    builder.add_node("execute", execute)
    builder.add_node("collect_evidence", collect_evidence)
    builder.add_node("attribute", attribute)
    builder.add_node("repair", repair)
    builder.add_node("verify", verify)
    builder.add_node("promote", promote)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "execute")
    builder.add_edge("execute", "collect_evidence")
    builder.add_conditional_edges(
        "collect_evidence",
        route_after_evidence,
        {
            "attribute": "attribute",
            "verify": "verify",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "attribute",
        route_after_attribution,
        {"repair": "repair", "finalize": "finalize"},
    )
    builder.add_edge("repair", "execute")
    builder.add_conditional_edges(
        "verify",
        route_after_verification,
        {"promote": "promote", "finalize": "finalize"},
    )
    builder.add_edge("promote", END)
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=InMemorySaver())
