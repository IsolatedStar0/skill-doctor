from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .graph import build_agent_graph
from .models import AgentState, RunRequest
from .workers import (
    BenchmarkReplayWorker,
    CodexExecutionWorker,
    ExecutionWorker,
    FixtureWorker,
)


class RunService:
    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (
            project_root
            or Path(__file__).resolve().parents[2]
        ).resolve()
        self.report_directory = self.project_root / "reports" / "langgraph"

    def _worker(self, request: RunRequest) -> ExecutionWorker:
        if request.executor == "codex":
            return CodexExecutionWorker(self.project_root, request)
        if request.executor == "replay":
            return BenchmarkReplayWorker(
                self.project_root / "public" / "benchmarks" / "latest.json"
            )
        return FixtureWorker(request.scenario)

    def _resolved_inputs(self, request: RunRequest) -> tuple[str, str]:
        task = request.task
        skill_content = request.skill_content
        if request.executor != "codex":
            return task, skill_content

        default_task = RunRequest.model_fields["task"].default
        default_skill = RunRequest.model_fields["skill_content"].default
        if task != default_task and skill_content != default_skill:
            return task, skill_content
        dataset_path = (
            self.project_root
            / "benchmarks"
            / "cache"
            / "swe_skills_bench.jsonl"
        )
        if not dataset_path.is_file():
            return task, skill_content
        for line in dataset_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("skill_id") != request.skill_id:
                continue
            if task == default_task:
                task = row.get("task_prompt") or task
            if skill_content == default_skill:
                skill_content = row.get("skill_document") or skill_content
            break
        return task, skill_content

    def _initial_state(self, request: RunRequest, run_id: str) -> AgentState:
        task, skill_content = self._resolved_inputs(request)
        return AgentState(
            run_id=run_id,
            task=task,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            skill_content=skill_content,
            executor=request.executor,
            scenario=request.scenario,
            attempt=0,
            max_attempts=request.max_attempts,
            status="pending",
            stop_reason="",
            events=[],
        )

    def run(self, request: RunRequest) -> dict[str, Any]:
        run_id = f"lg-{uuid4().hex[:12]}"
        graph = build_agent_graph(self._worker(request))
        result = graph.invoke(
            self._initial_state(request, run_id),
            config={"configurable": {"thread_id": run_id}},
        )
        self._save(result)
        return result

    def stream(self, request: RunRequest) -> Iterator[dict[str, Any]]:
        run_id = f"lg-{uuid4().hex[:12]}"
        graph = build_agent_graph(self._worker(request))
        latest: dict[str, Any] | None = None
        for update in graph.stream(
            self._initial_state(request, run_id),
            config={"configurable": {"thread_id": run_id}},
            stream_mode="values",
        ):
            latest = update
            yield update
            if request.stream_delay_ms:
                time.sleep(request.stream_delay_ms / 1_000)
        if latest is not None:
            self._save(latest)

    def get(self, run_id: str) -> dict[str, Any]:
        if not run_id.startswith("lg-") or not run_id[3:].isalnum():
            raise ValueError("Invalid run id.")
        path = self.report_directory / f"{run_id}.json"
        if not path.is_file():
            raise FileNotFoundError(run_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def _save(self, result: dict[str, Any]) -> None:
        self.report_directory.mkdir(parents=True, exist_ok=True)
        path = self.report_directory / f"{result['run_id']}.json"
        path.write_text(
            f"{json.dumps(result, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
