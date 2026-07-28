from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field, model_validator


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AssertionResult(BaseModel):
    id: str
    source: Literal["task", "skill", "system"] = "system"
    passed: bool
    detail: str | None = None


class BusinessResultDetail(BaseModel):
    name: str
    status: Literal["pass", "fail", "warning"]
    reason: str = ""


class BusinessResult(BaseModel):
    """Generic skill conclusion for data-driven display."""

    verdict: str
    verdict_type: Literal["pass", "fail", "warning"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    details: list[BusinessResultDetail] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    executor: str
    condition: str
    task_kind: str = "knowledge-probe"
    passed: bool
    pass_rate: float = Field(ge=0, le=1)
    duration_ms: int = Field(ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    assertions: list[AssertionResult] = Field(default_factory=list)
    regression_rate: float = Field(default=0, ge=0, le=1)
    summary: str
    artifacts: dict[str, str] = Field(default_factory=dict)
    runtime_events: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class EvidenceSnapshot(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    run_id: str
    attempt: int
    skill_id: str
    condition: str
    execution_sha256: str
    assertion_sha256: str
    artifact_refs: list[str] = Field(default_factory=list)


class AttributionResult(BaseModel):
    taxonomy: str
    cause: Literal["skill", "routing", "loader", "tool", "platform"]
    confidence: float = Field(ge=0, le=1)
    responsibility: float = Field(ge=0, le=1)
    action: Literal[
        "patch_skill",
        "patch_routing",
        "patch_loader",
        "split_non_skill",
    ]
    evidence_refs: list[str] = Field(default_factory=list)
    explanation: str
    # -- Skill-Adaptor style attribution fields (all optional, backward compat)
    fault_type: Literal[
        "skill_wrong",
        "skill_missing",
        "reasoning_wrong",
        "unknown",
    ] = "unknown"
    t_star: int | None = None
    fault_chain: list[int] = Field(default_factory=list)
    improvement_principle: str = ""
    skill_attributions: list[dict[str, Any]] = Field(default_factory=list)
    # -- LLM-authored conclusion (empty when Localizer fell back to rules)
    agent_conclusion: str = ""
    agent_reason: str = ""
    agent_source: Literal["llm", "rule-based", "none"] = "none"


class RepairPatch(BaseModel):
    patch_id: str
    kind: Literal["skill_patch", "loader_patch"]
    skill_id: str
    base_version: str
    next_version: str
    before: str
    after: str
    evidence_refs: list[str] = Field(default_factory=list)
    rollback_ref: str
    # -- Skill-Adaptor style repair fields (all optional, backward compat)
    repair_mode: Literal["generate", "revise", "loader", "none"] = "revise"
    revision_type: str = ""
    principle: str = ""


class VerificationResult(BaseModel):
    decision: Literal["ADOPT", "REJECT"]
    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_delta: float
    regression_rate: float
    reasons: list[str] = Field(default_factory=list)
    # -- Qualifier fields (all optional, backward compat)
    delta_avg_score: float = 0.0
    regression_detected: bool = False
    sample_size: int = 1
    qualifier_reason: str = ""


class RunEvent(BaseModel):
    sequence: int
    stage: str
    status: Literal["started", "completed", "failed", "skipped"]
    attempt: int
    message: str
    usage: TokenUsage | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    task: str = "Use the target Skill to produce a verified implementation plan."
    skill_id: str = "tdd-workflow"
    skill_version: str = "1.0.0"
    skill_content: str = (
        "Inspect the task, execute the required procedure, and verify the result."
    )
    executor: Literal["fixture", "replay", "codex", "trace-ingest"] = "fixture"
    scenario: Literal["content-gap", "network-error"] = "content-gap"
    condition: Literal[
        "standard",
        "without_skill",
        "with_skill",
    ] = "standard"
    parent_run_id: str | None = None
    repair_enabled: bool = True
    max_attempts: int = Field(default=2, ge=1, le=5)
    stream_delay_ms: int = Field(default=180, ge=0, le=2_000)
    codex_timeout_ms: int = Field(default=180_000, ge=10_000, le=600_000)
    codex_reasoning_effort: Literal[
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ] = "medium"


class TraceIngestRequest(BaseModel):
    """Trace payload pushed by an external Aime execution.

    Two shapes are supported:

    1. **Normalized**: caller has already reduced the trace to an
       :class:`ExecutionResult`. The uploaded worker will still run agent
       analysis over ``runtime_events`` embedded in that result.
    2. **Raw**: caller sends the untransformed runtime signal (raw
       ``runtime_events``, ``tool_calls``, ``model_messages`` and optional
       ``trace_metadata``). The uploaded worker synthesizes an
       :class:`ExecutionResult` from that signal before the LangGraph
       pipeline runs the attribute / evidence / finalize nodes.
    """

    task: str = "Imported Aime Skill execution trace."
    skill_id: str
    skill_version: str = "unknown"
    skill_content: str = ""
    condition: Literal[
        "standard",
        "without_skill",
        "with_skill",
    ] = "standard"
    parent_run_id: str | None = None
    repair_enabled: bool = True
    max_attempts: int = Field(default=1, ge=1, le=5)
    execution: ExecutionResult | None = None
    business_result: BusinessResult | None = None
    runtime_events: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_messages: list[dict[str, Any]] = Field(default_factory=list)
    trace_metadata: dict[str, Any] = Field(default_factory=dict)

    def has_raw_signal(self) -> bool:
        """True when any raw trace channel carries data."""

        return bool(
            self.runtime_events
            or self.tool_calls
            or self.model_messages
            or self.trace_metadata
        )

    @model_validator(mode="after")
    def _require_payload(self) -> "TraceIngestRequest":
        if self.execution is None and not self.has_raw_signal():
            raise ValueError(
                "TraceIngestRequest requires either 'execution' or at least "
                "one raw trace field (runtime_events / tool_calls / "
                "model_messages / trace_metadata)."
            )
        return self


class AgentState(TypedDict):
    run_kind: str
    run_id: str
    parent_run_id: str | None
    task: str
    skill_id: str
    skill_version: str
    skill_content: str
    executor: str
    scenario: str
    condition: str
    repair_enabled: bool
    attempt: int
    max_attempts: int
    status: str
    stop_reason: str
    business_result: NotRequired[dict[str, Any]]
    execution: NotRequired[dict[str, Any]]
    baseline_execution: NotRequired[dict[str, Any]]
    evidence_snapshot: NotRequired[dict[str, Any]]
    attribution: NotRequired[dict[str, Any]]
    repair_patch: NotRequired[dict[str, Any]]
    verification: NotRequired[dict[str, Any]]
    observability: NotRequired[dict[str, Any]]
    events: Annotated[list[dict[str, Any]], add]


class BenchmarkRequest(BaseModel):
    task: str = "Use the target Skill to produce a verified implementation plan."
    skill_id: str = "tdd-workflow"
    skill_version: str = "1.0.0"
    skill_content: str = (
        "Inspect the task, execute the required procedure, and verify the result."
    )
    executor: Literal["fixture", "replay", "codex"] = "fixture"
    scenario: Literal["content-gap", "network-error"] = "content-gap"
    codex_timeout_ms: int = Field(default=180_000, ge=10_000, le=600_000)
    codex_reasoning_effort: Literal[
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    ] = "medium"


class DiagnosticExpectation(BaseModel):
    """Expected outcome for one trace regression case."""

    status: Literal["passed", "failed"] | None = None
    cause: Literal["skill", "routing", "loader", "tool", "platform"] | None = None
    fault_type: Literal[
        "skill_wrong",
        "skill_missing",
        "reasoning_wrong",
        "unknown",
    ] | None = None
    action: Literal[
        "patch_skill",
        "patch_routing",
        "patch_loader",
        "split_non_skill",
    ] | None = None
    should_repair: bool | None = None
    should_call_llm: bool | None = None


class DiagnosticCaseRequest(BaseModel):
    case_id: str
    name: str
    description: str = ""
    trace: TraceIngestRequest
    expectation: DiagnosticExpectation = Field(default_factory=DiagnosticExpectation)


class DiagnosticSuiteRequest(BaseModel):
    suite_id: str = "core-trace-regression"
    name: str = "Core Trace Regression Suite"
    include_default_cases: bool = True
    cases: list[DiagnosticCaseRequest] = Field(default_factory=list)
