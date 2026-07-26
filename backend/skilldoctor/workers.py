from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

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

        repaired = attempt > 0
        return ExecutionResult(
            executor="fixture",
            condition="with_repaired_skill" if repaired else "with_skill",
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

        candidate = pair["control"] if attempt == 0 else pair["treatment"]
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

    def run(
        self,
        *,
        run_id: str,
        attempt: int,
        task: str,
        skill_id: str,
        skill_content: str,
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
            "projectRoot": str(self.project_root),
            "timeoutMs": self.request.codex_timeout_ms,
            "reasoningEffort": self.request.codex_reasoning_effort,
        }
        try:
            completed = subprocess.run(
                [self.node_executable, str(self.bridge_path)],
                cwd=self.project_root,
                input=json.dumps(request_payload),
                text=True,
                capture_output=True,
                timeout=(self.request.codex_timeout_ms / 1_000) + 15,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=self.request.codex_timeout_ms,
                summary="Codex SDK execution exceeded its timeout.",
                error=str(error),
            )

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK bridge failed before verification.",
                error=detail or f"Bridge exited with code {completed.returncode}.",
            )

        try:
            payload = json.loads(completed.stdout)
            return ExecutionResult.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as error:
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=False,
                pass_rate=0,
                duration_ms=0,
                summary="Codex SDK bridge returned an invalid result.",
                error=str(error),
            )
