from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterator
from uuid import uuid4

from .models import BenchmarkRequest, RunRequest
from .service import RunService


def _now() -> str:
    return datetime.now(UTC).isoformat()


class BenchmarkService:
    """Run a controlled without/with-Skill pair through RunService."""

    def __init__(
        self,
        run_service: RunService | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.run_service = run_service or RunService(project_root)
        self.project_root = self.run_service.project_root

    @property
    def report_directory(self) -> Path:
        run_directory = self.run_service.report_directory
        return (
            run_directory.parent / "benchmarks"
            if run_directory.name == "langgraph"
            else run_directory / "benchmarks"
        )

    def run(self, request: BenchmarkRequest) -> dict[str, Any]:
        latest: dict[str, Any] | None = None
        for latest in self.stream(request):
            pass
        if latest is None:
            raise RuntimeError("Benchmark completed without a state snapshot.")
        return latest

    def stream(
        self,
        request: BenchmarkRequest,
    ) -> Iterator[dict[str, Any]]:
        benchmark_id = f"bm-{uuid4().hex[:12]}"
        state: dict[str, Any] = {
            "run_kind": "benchmark",
            "run_id": benchmark_id,
            "parent_run_id": None,
            "skill_id": request.skill_id,
            "skill_version": request.skill_version,
            "executor": request.executor,
            "scenario": request.scenario,
            "condition": "paired",
            "attempt": 0,
            "max_attempts": 0,
            "status": "pending",
            "stop_reason": "",
            "task": request.task,
            "events": [],
            "control_run_id": None,
            "treatment_run_id": None,
            "control": None,
            "treatment": None,
            "report": None,
            "error": None,
        }
        yield self._publish(state)

        state["status"] = "running"
        self._event(state, "benchmark.started", "Paired benchmark started.")
        yield self._publish(state)

        children: dict[str, dict[str, Any] | None] = {
            "without_skill": None,
            "with_skill": None,
        }
        errors: list[str] = []
        for condition in ("without_skill", "with_skill"):
            self._event(
                state,
                f"benchmark.{condition}.started",
                f"Starting {condition} child Run.",
            )
            yield self._publish(state)
            try:
                child = self.run_service.run(
                    RunRequest(
                        task=request.task,
                        skill_id=request.skill_id,
                        skill_version=request.skill_version,
                        skill_content=request.skill_content,
                        executor=request.executor,
                        scenario=request.scenario,
                        condition=condition,
                        parent_run_id=benchmark_id,
                        repair_enabled=False,
                        max_attempts=1,
                        stream_delay_ms=0,
                        codex_timeout_ms=request.codex_timeout_ms,
                        codex_reasoning_effort=(
                            request.codex_reasoning_effort
                        ),
                    )
                )
                children[condition] = child
                key = (
                    "control_run_id"
                    if condition == "without_skill"
                    else "treatment_run_id"
                )
                state[key] = child["run_id"]
                state[
                    "control" if condition == "without_skill" else "treatment"
                ] = self._benchmark_run(child)
                self._event(
                    state,
                    f"benchmark.{condition}.completed",
                    f"Completed child Run {child['run_id']}.",
                )
            except BaseException as error:
                message = f"{condition}: {error}"
                errors.append(message)
                self._event(
                    state,
                    f"benchmark.{condition}.failed",
                    message,
                    status="failed",
                )
            yield self._publish(state)

        report = self._report(request, benchmark_id, state)
        state["report"] = report
        state["status"] = (
            "completed"
            if report["summary"]["completedPairs"] == 1
            else "failed"
        )
        state["stop_reason"] = (
            "paired_benchmark_completed"
            if state["status"] == "completed"
            else "paired_benchmark_incomplete"
        )
        state["error"] = "; ".join(errors) or None
        self._event(
            state,
            "benchmark.completed",
            (
                "Paired metrics are ready."
                if state["status"] == "completed"
                else "Benchmark ended with an incomplete pair."
            ),
            status=(
                "completed" if state["status"] == "completed" else "failed"
            ),
        )
        self._save(state)
        yield self._publish(state)

    def get(self, benchmark_id: str) -> dict[str, Any]:
        if not benchmark_id.startswith("bm-") or not benchmark_id[3:].isalnum():
            raise ValueError("Invalid benchmark id.")
        try:
            return self.run_service.storage.get_benchmark(benchmark_id)
        except FileNotFoundError:
            pass
        return self.run_service.registry.get(benchmark_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item
            for item in self.run_service.list_runs(limit * 2)
            if item["run_kind"] == "benchmark"
        ][:limit]

    def _publish(self, state: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.run_service.storage.snapshot(state)
        self.run_service.registry.publish(snapshot)
        return snapshot

    def _save(self, state: dict[str, Any]) -> None:
        self.run_service.storage.save_benchmark(state)

    @staticmethod
    def _event(
        state: dict[str, Any],
        stage: str,
        message: str,
        *,
        status: str = "completed",
    ) -> None:
        state["events"].append(
            {
                "sequence": len(state["events"]) + 1,
                "stage": stage,
                "status": status,
                "attempt": 0,
                "message": message,
                "usage": None,
                "metadata": {},
            }
        )

    def _benchmark_run(self, child: dict[str, Any]) -> dict[str, Any]:
        execution = child["execution"]
        assertions = execution.get("assertions", [])
        passed = sum(1 for item in assertions if item["passed"])
        usage = execution.get("usage", {})
        artifacts = execution.get("artifacts", {})
        return {
            "id": child["run_id"],
            "condition": child["condition"],
            "status": (
                "failed" if execution.get("error") else "completed"
            ),
            "executor": execution["executor"],
            "taskKind": "knowledge-probe",
            "startedAt": _now(),
            "durationMs": execution["duration_ms"],
            "usage": {
                "inputTokens": usage.get("input_tokens", 0),
                "outputTokens": usage.get("output_tokens", 0),
                "cachedInputTokens": usage.get("cached_input_tokens", 0),
                "reasoningTokens": usage.get("reasoning_tokens", 0),
                "totalTokens": (
                    usage.get("input_tokens", 0)
                    + usage.get("output_tokens", 0)
                ),
            },
            "verifier": {
                "framework": "pytest",
                "passed": passed,
                "failed": len(assertions) - passed,
                "total": len(assertions),
                "passRate": execution["pass_rate"],
                "assertions": [
                    {
                        "id": item["id"],
                        "label": item.get("detail") or item["id"],
                        "source": (
                            item["source"]
                            if item["source"] in {"task", "skill"}
                            else "task"
                        ),
                        "passed": item["passed"],
                        "matched": item.get("detail"),
                    }
                    for item in assertions
                ],
            },
            "artifacts": {
                "evidenceSnapshot": self.run_service.storage.run_artifact_uri(
                    child["run_id"]
                ),
                "codexJsonl": artifacts.get("codexJsonl", ""),
                "pytestOutput": artifacts.get("pytestOutput", ""),
                "gitDiff": artifacts.get("gitDiff", ""),
            },
            "error": execution.get("error"),
        }

    def _report(
        self,
        request: BenchmarkRequest,
        benchmark_id: str,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        control = state["control"]
        treatment = state["treatment"]
        comparison = self._compare(control, treatment)
        complete = comparison["outcome"] != "incomplete"
        pair = {
            "skillId": request.skill_id,
            "name": request.skill_id.replace("-", " ").title(),
            "dimension": "dynamic-paired-run",
            "control": control,
            "treatment": treatment,
            "comparison": comparison,
        }
        return {
            "schemaVersion": "1.0",
            "runId": benchmark_id,
            "generatedAt": _now(),
            "executor": request.executor,
            "taskKind": "knowledge-probe",
            "isModelResult": request.executor == "codex",
            "dataset": {
                "name": "Skill Doctor Dynamic Pair",
                "sha256": "",
                "selectedSkills": [request.skill_id],
            },
            "summary": {
                "pairs": 1,
                "completedPairs": 1 if complete else 0,
                "improved": 1 if comparison["outcome"] == "improved" else 0,
                "tied": 1 if comparison["outcome"] == "tied" else 0,
                "regressed": (
                    1 if comparison["outcome"] == "regressed" else 0
                ),
                "averagePassRateDelta": comparison["passRateDelta"],
                "averageTokenOverheadRate": comparison[
                    "tokenOverheadRate"
                ],
                "averageDurationDeltaMs": comparison["durationDeltaMs"],
                "regressionRate": comparison["regressionRate"],
            },
            "pairs": [pair] if control and treatment else [],
        }

    @staticmethod
    def _compare(
        control: dict[str, Any] | None,
        treatment: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if (
            not control
            or not treatment
            or control["status"] != "completed"
            or treatment["status"] != "completed"
        ):
            return {
                "outcome": "incomplete",
                "passRateDelta": None,
                "tokenDelta": None,
                "tokenOverheadRate": None,
                "durationDeltaMs": None,
                "regressionRate": None,
                "regressedAssertionIds": [],
            }
        control_rate = control["verifier"]["passRate"]
        treatment_rate = treatment["verifier"]["passRate"]
        pass_delta = treatment_rate - control_rate
        control_tokens = control["usage"]["totalTokens"]
        treatment_tokens = treatment["usage"]["totalTokens"]
        token_delta = treatment_tokens - control_tokens
        treatment_assertions = {
            item["id"]: item for item in treatment["verifier"]["assertions"]
        }
        regressed = [
            item["id"]
            for item in control["verifier"]["assertions"]
            if item["passed"]
            and treatment_assertions.get(item["id"], {}).get("passed") is False
        ]
        control_passed = sum(
            1 for item in control["verifier"]["assertions"] if item["passed"]
        )
        regression_rate = (
            len(regressed) / control_passed if control_passed else 0
        )
        outcome = (
            "improved"
            if pass_delta > 0
            else "regressed"
            if pass_delta < 0 or regressed
            else "tied"
        )
        return {
            "outcome": outcome,
            "passRateDelta": pass_delta,
            "tokenDelta": token_delta,
            "tokenOverheadRate": (
                token_delta / control_tokens if control_tokens else 0
            ),
            "durationDeltaMs": (
                treatment["durationMs"] - control["durationMs"]
            ),
            "regressionRate": regression_rate,
            "regressedAssertionIds": regressed,
        }
