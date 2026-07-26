from __future__ import annotations

import json
import queue
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from .graph import build_agent_graph
from .models import AgentState, RunEvent, RunRequest
from .observability import (
    LangSmithRunExporter,
    create_observability_exporter,
)
from .workers import (
    BenchmarkReplayWorker,
    CodexExecutionWorker,
    ExecutionWorker,
    FixtureWorker,
)


class RunService:
    def __init__(
        self,
        project_root: Path | None = None,
        exporter_factory: Callable[
            [str, RunRequest],
            LangSmithRunExporter,
        ] = create_observability_exporter,
    ) -> None:
        self.project_root = (
            project_root
            or Path(__file__).resolve().parents[2]
        ).resolve()
        self.report_directory = self.project_root / "reports" / "langgraph"
        self.exporter_factory = exporter_factory

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
        exporter = self.exporter_factory(run_id, request)
        graph = build_agent_graph(self._worker(request))
        initial_state = self._initial_state(request, run_id)
        initial_state["observability"] = exporter.snapshot()
        try:
            result = graph.invoke(
                initial_state,
                config={"configurable": {"thread_id": run_id}},
            )
            for event in result.get("events", []):
                exporter.record_event(event)
            exporter.finish(result)
        except BaseException as error:
            exporter.finish(error=error)
            raise
        result["observability"] = exporter.snapshot()
        self._save(result)
        return result

    def stream(self, request: RunRequest) -> Iterator[dict[str, Any]]:
        run_id = f"lg-{uuid4().hex[:12]}"
        exporter = self.exporter_factory(run_id, request)
        worker = self._worker(request)
        graph = build_agent_graph(worker)
        updates: queue.Queue[tuple[str, Any]] = queue.Queue()
        latest: dict[str, Any] | None = None
        pending_runtime_events: list[dict[str, Any]] = []
        seen_graph_events: set[int] = set()
        exporter_finished = False

        callback_setter = getattr(worker, "set_event_callback", None)
        if callable(callback_setter):
            callback_setter(
                lambda event: updates.put(("runtime_event", event))
            )

        def produce() -> None:
            try:
                initial_state = self._initial_state(request, run_id)
                initial_state["observability"] = exporter.snapshot()
                for state in graph.stream(
                    initial_state,
                    config={"configurable": {"thread_id": run_id}},
                    stream_mode="values",
                ):
                    updates.put(("state", state))
            except BaseException as error:
                updates.put(("error", error))
            finally:
                updates.put(("done", None))

        producer = threading.Thread(
            target=produce,
            name=f"skill-doctor-{run_id}",
            daemon=True,
        )
        producer.start()
        try:
            while True:
                kind, payload = updates.get()
                if kind == "state":
                    snapshot = deepcopy(payload)
                    for event in snapshot.get("events", []):
                        sequence = int(event.get("sequence", 0))
                        if (
                            sequence not in seen_graph_events
                            and not str(event.get("stage", "")).startswith(
                                "codex."
                            )
                        ):
                            exporter.record_event(event)
                            seen_graph_events.add(sequence)
                    if snapshot.get("status") in {"passed", "failed"}:
                        exporter.finish(snapshot)
                        exporter_finished = True
                    snapshot["observability"] = exporter.snapshot()
                    latest = snapshot
                    pending_runtime_events = []
                    yield snapshot
                    if request.stream_delay_ms:
                        time.sleep(request.stream_delay_ms / 1_000)
                elif kind == "runtime_event":
                    pending_runtime_events.append(payload)
                    exporter.record_event(payload)
                    if latest is None:
                        continue
                    snapshot = deepcopy(latest)
                    base_sequence = len(latest.get("events", []))
                    live_events = [
                        RunEvent(
                            sequence=base_sequence + index,
                            attempt=latest["attempt"],
                            **event,
                        ).model_dump(mode="json")
                        for index, event in enumerate(
                            pending_runtime_events,
                            start=1,
                        )
                    ]
                    snapshot["events"] = [
                        *latest.get("events", []),
                        *live_events,
                    ]
                    snapshot["status"] = "running"
                    yield snapshot
                elif kind == "error":
                    exporter.finish(error=payload)
                    exporter_finished = True
                    raise payload
                elif kind == "done":
                    break
        finally:
            if callable(callback_setter):
                callback_setter(None)
            producer.join(timeout=1)
            if not exporter_finished:
                exporter.finish(latest)
        if latest is not None:
            latest["observability"] = exporter.snapshot()
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
