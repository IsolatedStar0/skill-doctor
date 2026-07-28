from __future__ import annotations

import json
import shutil
import subprocess
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Protocol

from .models import (
    AssertionResult,
    ExecutionResult,
    RunRequest,
    TokenUsage,
    TraceIngestRequest,
)


class ExecutionWorker(Protocol):
    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
        condition: str,
    ) -> ExecutionResult: ...


class FixtureWorker:
    """Deterministic worker used by tests and the offline demo."""

    def __init__(self, scenario: str = "content-gap") -> None:
        self.scenario = scenario

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
        condition: str = "standard",
    ) -> ExecutionResult:
        del run_id, task, skill_content
        if self.scenario == "network-error":
            return ExecutionResult(
                executor="fixture",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=420,
                usage=TokenUsage(input_tokens=180, output_tokens=20),
                assertions=[
                    AssertionResult(
                        id="external-service",
                        source="system",
                        passed=False,
                        detail="Upstream service connection was reset.",
                    )
                ],
                summary="Execution failed before the Skill procedure could run.",
                error="network connection reset",
            )

        paired = condition in {"without_skill", "with_skill"}
        if self.scenario == "code-repair":
            passed = attempt > 0
            return ExecutionResult(
                executor="fixture",
                condition=condition,
                task_kind="code-repair",
                passed=passed,
                pass_rate=1.0 if passed else 0.5,
                duration_ms=1200,
                assertions=[
                    AssertionResult(
                        id="fix-bug",
                        source="task",
                        passed=passed,
                        detail="Bug fixed." if passed else "Bug remains.",
                    ),
                    AssertionResult(
                        id="pass-pytest",
                        source="system",
                        passed=passed,
                        detail="Tests passed." if passed else "Tests failed.",
                    ),
                ],
            )
        
        repaired = (
            condition == "with_skill"
            if paired
            else attempt > 0
        )
        return ExecutionResult(
            executor="fixture",
            condition=(
                condition
                if paired
                else "with_repaired_skill" if repaired else "with_skill"
            ),
            passed=repaired,
            pass_rate=1.0 if repaired else 0.5,
            duration_ms=1_180 if repaired else 960,
            usage=TokenUsage(
                input_tokens=1_420 if repaired else 1_050,
                output_tokens=260 if repaired else 180,
                cached_input_tokens=620 if repaired else 410,
                reasoning_tokens=96 if repaired else 82,
            ),
            assertions=[
                AssertionResult(
                    id="full-input-coverage",
                    source="skill",
                    passed=repaired,
                    detail=(
                        "All records were processed."
                        if repaired
                        else "The procedure only processed preview rows."
                    ),
                ),
                AssertionResult(
                    id="output-contract",
                    source="task",
                    passed=True,
                    detail="The requested output schema is valid.",
                ),
            ],
            regression_rate=0,
            summary=(
                f"{skill_id} repaired the missing full-input requirement."
                if repaired
                else f"{skill_id} omitted the full-input requirement."
            ),
        )


class UploadedTraceWorker:
    """Analyze an externally uploaded trace and feed the graph.

    The worker replaces the previous "pass-through" behaviour: instead of
    returning the pre-computed :class:`ExecutionResult` verbatim, it runs a
    lightweight but real analysis pass over the raw trace signal (runtime
    events, tool calls, model messages, trace metadata) and returns an
    enriched :class:`ExecutionResult` that downstream LangGraph nodes
    (attribute / collect_evidence / finalize) can act on.

    Analysis steps are surfaced as ``runtime_events`` so that they appear
    as discrete agent steps in the ``execute`` node of the graph and, by
    extension, in ``GET /runs/{run_id}``.
    """

    _ERROR_KEYWORDS = (
        "error",
        "failed",
        "exception",
        "timeout",
        "denied",
        "not found",
        "traceback",
    )

    def __init__(self, request: TraceIngestRequest) -> None:
        self.request = request
        self._event_callback: Callable[[dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------ hooks
    def set_event_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._event_callback = callback

    def _emit(
        self,
        stage: str,
        message: str,
        *,
        status: str = "completed",
        metadata: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "stage": stage,
            "status": status,
            "message": message,
            "usage": usage,
            "metadata": metadata or {},
        }
        if self._event_callback is not None:
            # deepcopy so the caller cannot mutate our internal buffer
            self._event_callback(deepcopy(event))
        return event

    # -------------------------------------------------------------- analysis
    @staticmethod
    def _stringify(value: Any, limit: int = 400) -> str:
        if isinstance(value, str):
            text = value.strip()
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                text = str(value)
        text = text.strip()
        return text if len(text) <= limit else f"{text[: limit - 1]}…"

    def _looks_like_error(self, blob: Any) -> bool:
        text = self._stringify(blob, limit=4000).lower()
        return any(keyword in text for keyword in self._ERROR_KEYWORDS)

    @staticmethod
    def _token_int(*values: Any) -> int:
        for value in values:
            if value is None:
                continue
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return max(0, int(value))
            if isinstance(value, str) and value.strip().isdigit():
                return max(0, int(value.strip()))
        return 0

    @classmethod
    def _normalize_usage(cls, raw: Any) -> dict[str, int] | None:
        """Normalize common provider/Aime token usage shapes to TokenUsage."""

        if not isinstance(raw, dict):
            return None
        input_details = raw.get("input_tokens_details") or {}
        output_details = raw.get("output_tokens_details") or {}
        if not isinstance(input_details, dict):
            input_details = {}
        if not isinstance(output_details, dict):
            output_details = {}
        usage = {
            "input_tokens": cls._token_int(
                raw.get("input_tokens"),
                raw.get("inputTokens"),
                raw.get("prompt_tokens"),
                raw.get("promptTokens"),
            ),
            "output_tokens": cls._token_int(
                raw.get("output_tokens"),
                raw.get("outputTokens"),
                raw.get("completion_tokens"),
                raw.get("completionTokens"),
            ),
            "cached_input_tokens": cls._token_int(
                raw.get("cached_input_tokens"),
                raw.get("cachedInputTokens"),
                raw.get("cache_read_input_tokens"),
                raw.get("cacheReadInputTokens"),
                input_details.get("cached_tokens"),
            ),
            "reasoning_tokens": cls._token_int(
                raw.get("reasoning_tokens"),
                raw.get("reasoningTokens"),
                raw.get("reasoning_output_tokens"),
                raw.get("reasoningOutputTokens"),
                output_details.get("reasoning_tokens"),
            ),
        }
        return usage if any(usage.values()) else None

    @classmethod
    def _usage_from_record(cls, raw: dict[str, Any]) -> dict[str, int] | None:
        for key in (
            "usage",
            "token_usage",
            "tokenUsage",
            "usage_metadata",
            "usageMetadata",
        ):
            usage = cls._normalize_usage(raw.get(key))
            if usage is not None:
                return usage
        return cls._normalize_usage(raw)

    @staticmethod
    def _sum_usage(events: list[dict[str, Any]]) -> TokenUsage:
        totals = TokenUsage()
        for event in events:
            usage = event.get("usage") if isinstance(event, dict) else None
            if not isinstance(usage, dict):
                continue
            totals.input_tokens += int(usage.get("input_tokens") or 0)
            totals.output_tokens += int(usage.get("output_tokens") or 0)
            totals.cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
            totals.reasoning_tokens += int(usage.get("reasoning_tokens") or 0)
        return totals

    def _analyze_runtime_events(
        self,
        events: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[AssertionResult], list[str]]:
        normalized: list[dict[str, Any]] = []
        assertions: list[AssertionResult] = []
        findings: list[str] = []
        failed_stages: list[str] = []

        for index, raw in enumerate(events, start=1):
            if not isinstance(raw, dict):
                continue
            stage = str(raw.get("stage") or f"trace.event.{index}")
            status_value = str(raw.get("status") or "completed").lower()
            if status_value not in {"started", "completed", "failed", "skipped"}:
                status_value = (
                    "failed" if self._looks_like_error(raw) else "completed"
                )
            message = self._stringify(
                raw.get("message") or raw.get("summary") or stage
            )
            metadata = raw.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            normalized.append(
                {
                    "stage": stage,
                    "status": status_value,
                    "message": message,
                    "usage": self._usage_from_record(raw),
                    "metadata": metadata,
                }
            )
            if status_value == "failed":
                failed_stages.append(stage)

        if failed_stages:
            summary = ", ".join(failed_stages[:5])
            assertions.append(
                AssertionResult(
                    id="runtime-events-clean",
                    source="system",
                    passed=False,
                    detail=(
                        f"{len(failed_stages)} runtime event(s) reported failure "
                        f"({summary})."
                    ),
                )
            )
            findings.append(
                f"{len(failed_stages)} failed runtime event(s) detected."
            )
        elif events:
            assertions.append(
                AssertionResult(
                    id="runtime-events-clean",
                    source="system",
                    passed=True,
                    detail=f"All {len(normalized)} runtime events completed.",
                )
            )

        return normalized, assertions, findings

    def _analyze_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> tuple[list[AssertionResult], list[str], dict[str, int]]:
        assertions: list[AssertionResult] = []
        findings: list[str] = []
        counts: dict[str, int] = {"total": 0, "failed": 0}
        failing_tools: list[str] = []
        for raw in tool_calls:
            if not isinstance(raw, dict):
                continue
            counts["total"] += 1
            name = str(
                raw.get("name") or raw.get("tool") or raw.get("id") or "unknown"
            )
            status_value = str(raw.get("status") or "").lower()
            error_field = raw.get("error")
            failed = (
                status_value in {"failed", "error"}
                or bool(error_field)
                or self._looks_like_error(raw.get("output") or raw.get("result"))
            )
            if failed:
                counts["failed"] += 1
                failing_tools.append(name)
        if counts["total"] > 0:
            passed = counts["failed"] == 0
            detail = (
                f"All {counts['total']} tool call(s) succeeded."
                if passed
                else (
                    f"{counts['failed']}/{counts['total']} tool call(s) failed"
                    + (
                        f" ({', '.join(failing_tools[:5])})"
                        if failing_tools
                        else ""
                    )
                    + "."
                )
            )
            assertions.append(
                AssertionResult(
                    id="tool-calls-healthy",
                    source="system",
                    passed=passed,
                    detail=detail,
                )
            )
            if not passed:
                findings.append(detail)
        return assertions, findings, counts

    def _analyze_model_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> tuple[list[AssertionResult], list[str], dict[str, int]]:
        assertions: list[AssertionResult] = []
        findings: list[str] = []
        counts: dict[str, int] = {"total": 0, "assistant": 0, "tool": 0}
        error_hits = 0
        for raw in messages:
            if not isinstance(raw, dict):
                continue
            counts["total"] += 1
            role = str(raw.get("role") or "").lower()
            if role in counts:
                counts[role] += 1
            if self._looks_like_error(raw.get("content") or raw.get("text")):
                error_hits += 1
        if counts["total"] > 0:
            passed = error_hits == 0
            detail = (
                f"Reviewed {counts['total']} model message(s); "
                f"assistant={counts['assistant']}, tool={counts['tool']}."
                if passed
                else (
                    f"Detected {error_hits} error-shaped model message(s) "
                    f"across {counts['total']} total."
                )
            )
            assertions.append(
                AssertionResult(
                    id="model-dialog-clean",
                    source="skill",
                    passed=passed,
                    detail=detail,
                )
            )
            if not passed:
                findings.append(detail)
        return assertions, findings, counts

    def _analyze_metadata(
        self,
        metadata: dict[str, Any],
    ) -> tuple[list[AssertionResult], list[str]]:
        assertions: list[AssertionResult] = []
        findings: list[str] = []
        if not metadata:
            return assertions, findings

        confidence = metadata.get("confidence")
        if isinstance(confidence, (int, float)):
            threshold = 0.75
            passed = confidence >= threshold
            assertions.append(
                AssertionResult(
                    id="skill-confidence",
                    source="skill",
                    passed=passed,
                    detail=(
                        f"Reported confidence {confidence:.2f} "
                        f"{'meets' if passed else 'below'} threshold {threshold}."
                    ),
                )
            )
            if not passed:
                findings.append(
                    f"Confidence {confidence:.2f} below {threshold} threshold."
                )
        rca_filter = metadata.get("rca_filter")
        if rca_filter is False:
            assertions.append(
                AssertionResult(
                    id="rca-filter-decision",
                    source="skill",
                    passed=False,
                    detail="Skill decided rca_filter=false (issue not filtered).",
                )
            )
            findings.append(
                "Skill decided rca_filter=false — anomaly retained."
            )
        return assertions, findings

    # ------------------------------------------------------------------ run
    def _collect_raw_channels(
        self,
        prior_execution: ExecutionResult | None,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        runtime_events = list(self.request.runtime_events)
        if prior_execution and prior_execution.runtime_events:
            # merge without duplicating identical entries
            seen = {json.dumps(item, sort_keys=True) for item in runtime_events}
            for item in prior_execution.runtime_events:
                key = json.dumps(item, sort_keys=True)
                if key not in seen:
                    runtime_events.append(item)
                    seen.add(key)
        return (
            runtime_events,
            list(self.request.tool_calls),
            list(self.request.model_messages),
            dict(self.request.trace_metadata),
        )

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
        condition: str = "standard",
    ) -> ExecutionResult:
        del run_id, attempt, task, skill_content
        prior = self.request.execution

        runtime_events, tool_calls, model_messages, metadata = (
            self._collect_raw_channels(prior)
        )

        analysis_events: list[dict[str, Any]] = []
        analysis_events.append(
            self._emit(
                "agent.analyze",
                (
                    "Uploaded trace received: "
                    f"runtime_events={len(runtime_events)}, "
                    f"tool_calls={len(tool_calls)}, "
                    f"model_messages={len(model_messages)}, "
                    f"trace_metadata_keys={len(metadata)}."
                ),
                status="started",
                metadata={
                    "skill_id": skill_id,
                    "condition": condition,
                    "has_prior_execution": prior is not None,
                    "trace_metadata_keys": sorted(metadata.keys()),
                },
            )
        )

        normalized_events, event_assertions, event_findings = (
            self._analyze_runtime_events(runtime_events)
        )
        runtime_usage = self._sum_usage(normalized_events)
        analysis_events.append(
            self._emit(
                "agent.analyze.runtime_events",
                (
                    f"Inspected {len(normalized_events)} runtime event(s); "
                    f"failures={sum(1 for e in normalized_events if e['status'] == 'failed')}."
                ),
                usage=(
                    runtime_usage.model_dump(mode="json")
                    if runtime_usage.total_tokens
                    else None
                ),
                metadata={
                    "count": len(normalized_events),
                    "failed": sum(
                        1 for e in normalized_events if e["status"] == "failed"
                    ),
                    "input_tokens": runtime_usage.input_tokens,
                    "output_tokens": runtime_usage.output_tokens,
                    "cached_input_tokens": runtime_usage.cached_input_tokens,
                    "reasoning_tokens": runtime_usage.reasoning_tokens,
                },
            )
        )

        tool_assertions, tool_findings, tool_counts = self._analyze_tool_calls(
            tool_calls
        )
        if tool_counts["total"]:
            analysis_events.append(
                self._emit(
                    "agent.analyze.tool_calls",
                    (
                        f"Reviewed {tool_counts['total']} tool call(s); "
                        f"failed={tool_counts['failed']}."
                    ),
                    metadata=tool_counts,
                )
            )

        (
            message_assertions,
            message_findings,
            message_counts,
        ) = self._analyze_model_messages(model_messages)
        if message_counts["total"]:
            analysis_events.append(
                self._emit(
                    "agent.analyze.model_messages",
                    (
                        f"Reviewed {message_counts['total']} model message(s) "
                        f"(assistant={message_counts['assistant']}, "
                        f"tool={message_counts['tool']})."
                    ),
                    metadata=message_counts,
                )
            )

        metadata_assertions, metadata_findings = self._analyze_metadata(metadata)
        if metadata:
            analysis_events.append(
                self._emit(
                    "agent.analyze.metadata",
                    f"Read {len(metadata)} trace metadata field(s).",
                    metadata={"keys": sorted(metadata.keys())},
                )
            )

        # ------- merge prior + derived assertions
        merged_assertions: list[AssertionResult] = []
        if prior is not None:
            merged_assertions.extend(prior.assertions)
        merged_assertions.extend(event_assertions)
        merged_assertions.extend(tool_assertions)
        merged_assertions.extend(message_assertions)
        merged_assertions.extend(metadata_assertions)

        all_findings = (
            event_findings
            + tool_findings
            + message_findings
            + metadata_findings
        )
        derived_passed = all(item.passed for item in merged_assertions)
        derived_pass_rate = (
            sum(1 for item in merged_assertions if item.passed)
            / len(merged_assertions)
            if merged_assertions
            else 1.0
        )

        # ------- reconcile with any prior execution result
        if prior is not None:
            passed = prior.passed and derived_passed
            pass_rate = min(prior.pass_rate, derived_pass_rate)
            executor = prior.executor or "aime-skill-trace"
            resolved_condition = prior.condition or condition
            task_kind = prior.task_kind
            duration_ms = prior.duration_ms
            usage = prior.usage
            if runtime_usage.total_tokens:
                usage = TokenUsage(
                    input_tokens=max(usage.input_tokens, runtime_usage.input_tokens),
                    output_tokens=max(usage.output_tokens, runtime_usage.output_tokens),
                    cached_input_tokens=max(
                        usage.cached_input_tokens,
                        runtime_usage.cached_input_tokens,
                    ),
                    reasoning_tokens=max(
                        usage.reasoning_tokens,
                        runtime_usage.reasoning_tokens,
                    ),
                )
            artifacts = dict(prior.artifacts)
            error = prior.error
        else:
            passed = derived_passed
            pass_rate = derived_pass_rate
            executor = "aime-skill-trace"
            resolved_condition = condition or "standard"
            task_kind = "knowledge-probe"
            duration_ms = 0
            usage = runtime_usage
            artifacts = {}
            error = None
            if not runtime_events and not tool_calls and not model_messages:
                error = "Empty trace payload."

        # error-flag any strong error signals
        if not passed and error is None and all_findings:
            error = all_findings[0]

        summary_bits: list[str] = []
        if prior is not None and prior.summary:
            summary_bits.append(prior.summary)
        if all_findings:
            summary_bits.append(
                f"Agent findings: {'; '.join(all_findings[:3])}."
            )
        else:
            summary_bits.append(
                f"Agent analysis found no issues across "
                f"{len(normalized_events)} runtime event(s), "
                f"{tool_counts['total']} tool call(s) and "
                f"{message_counts['total']} model message(s)."
            )
        summary = " ".join(summary_bits).strip()

        analysis_events.append(
            self._emit(
                "agent.analyze.summarize",
                summary,
                status="completed" if passed else "failed",
                metadata={
                    "passed": passed,
                    "pass_rate": round(pass_rate, 4),
                    "assertion_count": len(merged_assertions),
                    "findings": all_findings[:5],
                },
            )
        )
        analysis_events.append(
            self._emit(
                "agent.analyze",
                "Uploaded trace analysis complete.",
                status="completed" if passed else "failed",
                metadata={
                    "passed": passed,
                    "pass_rate": round(pass_rate, 4),
                    "findings_count": len(all_findings),
                },
            )
        )

        # combine analysis-emitted events with original trace runtime events so
        # the execute node stores the full agent-step timeline.
        combined_runtime_events: list[dict[str, Any]] = []
        combined_runtime_events.extend(normalized_events)
        combined_runtime_events.extend(analysis_events)

        return ExecutionResult(
            executor=executor,
            condition=resolved_condition,
            task_kind=task_kind,
            passed=passed,
            pass_rate=pass_rate,
            duration_ms=duration_ms,
            usage=usage,
            assertions=merged_assertions,
            regression_rate=(prior.regression_rate if prior else 0),
            summary=summary,
            artifacts=artifacts,
            runtime_events=combined_runtime_events,
            error=error,
        )


class BenchmarkReplayWorker:
    """Replays the repository's real Codex paired benchmark as graph input."""

    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path
        self.report = json.loads(report_path.read_text(encoding="utf-8"))

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
        condition: str = "standard",
    ) -> ExecutionResult:
        del run_id, task, skill_content
        pair = next(
            (
                candidate
                for candidate in self.report["pairs"]
                if candidate["skillId"] == skill_id
            ),
            None,
        )
        if pair is None:
            raise ValueError(
                f"Skill {skill_id!r} is not present in {self.report_path}."
            )

        candidate = (
            pair["control"]
            if condition == "without_skill"
            else pair["treatment"]
            if condition == "with_skill"
            else pair["control"]
            if attempt == 0
            else pair["treatment"]
        )
        verifier = candidate["verifier"]
        usage = candidate.get("usage") or {}
        assertions = [
            AssertionResult(
                id=item["id"],
                source=item.get("source", "system"),
                passed=item["passed"],
                detail=item.get("matched"),
            )
            for item in verifier["assertions"]
        ]
        return ExecutionResult(
            executor="codex-sdk-replay",
            condition=candidate["condition"],
            passed=verifier["failed"] == 0,
            pass_rate=verifier["passRate"],
            duration_ms=candidate["durationMs"],
            usage=TokenUsage(
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cached_input_tokens=usage.get("cachedInputTokens", 0),
                reasoning_tokens=usage.get("reasoningTokens", 0),
            ),
            assertions=assertions,
            regression_rate=(
                pair["comparison"]["regressionRate"] if attempt > 0 else 0
            ),
            summary=(
                f"Replayed {candidate['condition']} from real Codex run "
                f"{self.report['runId']}."
            ),
            artifacts=candidate.get("artifacts", {}),
            error=candidate.get("error"),
        )


class CodexExecutionWorker:
    """Execute the current Skill through the real JavaScript Codex SDK."""

    def __init__(
        self,
        project_root: Path,
        request: RunRequest,
        *,
        node_executable: str | None = None,
        bridge_path: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.request = request
        self.node_executable = node_executable or shutil.which("node")
        self.bridge_path = (
            bridge_path
            or self.project_root / "scripts" / "codex-execution-worker.mjs"
        ).resolve()
        self._event_callback: Callable[[dict[str, Any]], None] | None = None

    def set_event_callback(
        self,
        callback: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        self._event_callback = callback

    @staticmethod
    def _message(value: Any, limit: int = 360) -> str:
        text = str(value or "").strip()
        if not text:
            return "Codex SDK emitted an event."
        return text if len(text) <= limit else f"{text[:limit - 1]}…"

    def _runtime_event(self, envelope: dict[str, Any]) -> dict[str, Any]:
        event = envelope.get("event") or {}
        event_type = str(event.get("type") or "unknown")
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        metadata: dict[str, Any] = {
            "event_type": event_type,
            "sdk_sequence": envelope.get("sequence", 0),
            "occurred_at": envelope.get("occurredAt", ""),
        }
        usage = None

        if event_type == "thread.started":
            stage = "codex.thread"
            status = "started"
            metadata["thread_id"] = event.get("thread_id", "")
            message = f"Codex thread {event.get('thread_id', 'unknown')} started."
        elif event_type == "turn.started":
            stage = "codex.turn"
            status = "started"
            message = "Codex turn started."
        elif event_type == "turn.completed":
            stage = "codex.turn"
            status = "completed"
            raw_usage = event.get("usage") or {}
            usage = {
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
                "cached_input_tokens": raw_usage.get("cached_input_tokens", 0),
                "reasoning_tokens": raw_usage.get("reasoning_output_tokens", 0),
            }
            message = "Codex turn completed with final token usage."
        elif event_type == "turn.failed":
            stage = "codex.turn"
            status = "failed"
            message = self._message((event.get("error") or {}).get("message"))
        elif event_type == "error":
            stage = "codex.transport"
            status = "failed"
            message = self._message(event.get("message"))
        elif event_type.startswith("item."):
            stage = f"codex.{item_type or 'item'}"
            raw_status = str(item.get("status") or "")
            status = (
                "failed"
                if raw_status == "failed"
                else "completed"
                if event_type == "item.completed" or raw_status == "completed"
                else "started"
            )
            metadata["item_type"] = item_type
            metadata["item_id"] = item.get("id", "")
            if item_type == "command_execution":
                metadata["exit_code"] = item.get("exit_code")
                message = self._message(item.get("command"))
            elif item_type == "file_change":
                changes = item.get("changes") or []
                paths = [
                    str(change.get("path"))
                    for change in changes
                    if isinstance(change, dict) and change.get("path")
                ]
                metadata["changed_files"] = len(paths)
                message = self._message(", ".join(paths) or "File change event.")
            elif item_type == "mcp_tool_call":
                metadata["server"] = item.get("server", "")
                metadata["tool"] = item.get("tool", "")
                message = self._message(
                    f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}"
                )
            elif item_type == "web_search":
                message = self._message(item.get("query"))
            elif item_type in {"agent_message", "reasoning"}:
                message = self._message(item.get("text"))
            elif item_type == "error":
                status = "failed"
                message = self._message(item.get("message"))
            else:
                message = self._message(item_type or event_type)
        else:
            stage = "codex.event"
            status = "completed"
            message = self._message(event_type)

        return {
            "stage": stage,
            "status": status,
            "message": message,
            "usage": usage,
            "metadata": metadata,
        }

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
        condition: str = "standard",
    ) -> ExecutionResult:
        if not self.node_executable:
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK executor is unavailable.",
                error="Node.js is required for the Codex SDK executor.",
            )
        if not self.bridge_path.is_file():
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK executor is unavailable.",
                error=f"Codex SDK bridge not found: {self.bridge_path}",
            )

        request_payload = {
            "runId": run_id,
            "attempt": attempt,
            "task": task,
            "skillId": skill_id,
            "skillContent": skill_content,
            "condition": condition,
            "projectRoot": str(self.project_root),
            "timeoutMs": self.request.codex_timeout_ms,
            "reasoningEffort": self.request.codex_reasoning_effort,
        }
        runtime_events: list[dict[str, Any]] = []
        result_payload: dict[str, Any] | None = None
        bridge_error: str | None = None
        try:
            process = subprocess.Popen(
                [self.node_executable, str(self.bridge_path)],
                cwd=self.project_root,
                text=True,
                encoding="utf-8",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
            )
        except OSError as error:
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK bridge could not be started.",
                error=str(error),
            )

        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(json.dumps(request_payload))
        process.stdin.close()

        watchdog = threading.Timer(
            (self.request.codex_timeout_ms / 1_000) + 15,
            process.kill,
        )
        watchdog.daemon = True
        watchdog.start()
        try:
            for line in process.stdout:
                if not line.strip():
                    continue
                try:
                    envelope = json.loads(line)
                except json.JSONDecodeError as error:
                    bridge_error = f"Invalid bridge NDJSON: {error}"
                    continue
                kind = envelope.get("kind")
                if kind == "event":
                    runtime_event = self._runtime_event(envelope)
                    runtime_events.append(runtime_event)
                    if self._event_callback is not None:
                        self._event_callback(runtime_event)
                elif kind == "result":
                    result_payload = envelope.get("result")
                elif kind == "bridge_error":
                    bridge_error = str(envelope.get("error") or "Bridge failed.")
        finally:
            watchdog.cancel()

        return_code = process.wait()
        stderr = process.stderr.read().strip() if process.stderr else ""
        if return_code != 0 or result_payload is None:
            detail = bridge_error or stderr
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=self.request.codex_timeout_ms if return_code < 0 else 0,
                summary="Codex SDK bridge failed before verification.",
                runtime_events=runtime_events,
                error=detail or f"Bridge exited with code {return_code}.",
            )

        try:
            result_payload["runtime_events"] = runtime_events
            return ExecutionResult.model_validate(result_payload)
        except ValueError as error:
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK bridge returned an invalid result.",
                runtime_events=runtime_events,
                error=str(error),
            )
