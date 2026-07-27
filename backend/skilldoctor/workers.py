from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Protocol

from .models import AssertionResult, ExecutionResult, RunRequest, TokenUsage


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
