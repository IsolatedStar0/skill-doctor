from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field


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


class ExecutionResult(BaseModel):
    executor: str
    condition: str
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


class VerificationResult(BaseModel):
    decision: Literal["ADOPT", "REJECT"]
    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_delta: float
    regression_rate: float
    reasons: list[str] = Field(default_factory=list)


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
    executor: Literal["fixture", "replay", "codex"] = "fixture"
    scenario: Literal["content-gap", "network-error"] = "content-gap"
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


class AgentState(TypedDict):
    run_id: str
    task: str
    skill_id: str
    skill_version: str
    skill_content: str
    executor: str
    scenario: str
    attempt: int
    max_attempts: int
    status: str
    stop_reason: str
    execution: NotRequired[dict[str, Any]]
    baseline_execution: NotRequired[dict[str, Any]]
    evidence_snapshot: NotRequired[dict[str, Any]]
    attribution: NotRequired[dict[str, Any]]
    repair_patch: NotRequired[dict[str, Any]]
    verification: NotRequired[dict[str, Any]]
    observability: NotRequired[dict[str, Any]]
    events: Annotated[list[dict[str, Any]], add]
