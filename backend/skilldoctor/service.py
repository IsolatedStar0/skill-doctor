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
from .llm import build_deepseek_client
from .models import AgentState, RunEvent, RunRequest, TraceIngestRequest
from .observability import (
    LangSmithRunExporter,
    create_observability_exporter,
)
from .registry import RunRegistry
from .workers import (
    BenchmarkReplayWorker,
    CodexExecutionWorker,
    ExecutionWorker,
    FixtureWorker,
    UploadedTraceWorker,
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
        # Build a shared DeepSeek LLM client once per service. Returns None
        # gracefully when DEEPSEEK_API_KEY / openai SDK is unavailable, in
        # which case Skill-Adaptor stages keep using their rule-based
        # deterministic fallbacks.
        self.adaptor_llm_client = build_deepseek_client()

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
            run_kind="agent",
            run_id=run_id,
            parent_run_id=request.parent_run_id,
            task=task,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            skill_content=skill_content,
            executor=request.executor,
            scenario=request.scenario,
            condition=request.condition,
            repair_enabled=request.repair_enabled,
            attempt=0,
            max_attempts=request.max_attempts,
            status="pending",
            stop_reason="",
            events=[],
        )

    def run(self, request: RunRequest) -> dict[str, Any]:
        latest: dict[str, Any] | None = None
        immediate = request.model_copy(update={"stream_delay_ms": 0})
        for latest in self.stream(immediate):
            pass
        if latest is None:
            raise RuntimeError("Agent run completed without a state snapshot.")
        return latest

    def ingest_trace(self, request: TraceIngestRequest) -> dict[str, Any]:
        latest: dict[str, Any] | None = None
        for latest in self.stream_ingested_trace(request):
            pass
        if latest is None:
            raise RuntimeError("Trace ingest completed without a state snapshot.")
        return latest

    def stream_ingested_trace(
        self,
        request: TraceIngestRequest,
    ) -> Iterator[dict[str, Any]]:
        run_request = RunRequest(
            task=request.task,
            skill_id=request.skill_id,
            skill_version=request.skill_version,
            skill_content=request.skill_content,
            executor="trace-ingest",
            scenario="content-gap",
            condition=request.condition,
            parent_run_id=request.parent_run_id,
            repair_enabled=request.repair_enabled,
            max_attempts=request.max_attempts,
            stream_delay_ms=0,
        )
        extra_state: dict[str, Any] | None = None
        if request.business_result is not None:
            extra_state = {
                "business_result": request.business_result.model_dump(mode="json"),
            }
        yield from self._stream_with_worker(
            run_request,
            UploadedTraceWorker(request),
            extra_state=extra_state,
        )

    def stream(self, request: RunRequest) -> Iterator[dict[str, Any]]:
        yield from self._stream_with_worker(request, self._worker(request))

    def _stream_with_worker(
        self,
        request: RunRequest,
        worker: ExecutionWorker,
        extra_state: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        run_id = f"lg-{uuid4().hex[:12]}"
        exporter = self.exporter_factory(run_id, request)
        updates: queue.Queue[tuple[str, Any]] = queue.Queue()
        latest: dict[str, Any] | None = None
        pending_runtime_events: list[dict[str, Any]] = []
        exporter_finished = False

        def observe_runtime_event(
            event: dict[str, Any],
            config: dict[str, Any],
        ) -> None:
            exporter.record_event(event, config)
            updates.put(("runtime_event", event))

        graph = build_agent_graph(
            worker,
            runtime_event_observer=observe_runtime_event,
            adaptor_llm_client=self.adaptor_llm_client,
        )
        config = {
            **exporter.graph_config(),
            "configurable": {"thread_id": run_id},
        }

        def produce() -> None:
            try:
                initial_state = self._initial_state(request, run_id)
                initial_state["observability"] = exporter.snapshot()
                if extra_state:
                    initial_state.update(extra_state)
                for state in graph.stream(
                    initial_state,
                    config=config,
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
                    snapshot["observability"] = exporter.snapshot()
                    latest = snapshot
                    pending_runtime_events = []
                    self.registry.publish(snapshot)
                    yield snapshot
                    if request.stream_delay_ms:
                        time.sleep(request.stream_delay_ms / 1_000)
                elif kind == "runtime_event":
                    pending_runtime_events.append(payload)
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
                    self.registry.publish(snapshot)
                    yield snapshot
                elif kind == "error":
                    exporter.finish(error=payload)
                    exporter_finished = True
                    raise payload
                elif kind == "done":
                    before = exporter.snapshot()
                    exporter.finish(latest)
                    exporter_finished = True
                    after = exporter.snapshot()
                    if latest is not None and after != before:
                        latest = deepcopy(latest)
                        latest["observability"] = after
                        self.registry.publish(latest)
                        yield latest
                    break
        finally:
            producer.join(timeout=1)
            if not exporter_finished:
                exporter.finish(latest)
        if latest is not None:
            latest["observability"] = exporter.snapshot()
            self.registry.publish(latest)
            self._save(latest)

    def get(self, run_id: str) -> dict[str, Any]:
        if not run_id.startswith("lg-") or not run_id[3:].isalnum():
            raise ValueError("Invalid run id.")
        path = self.report_directory / f"{run_id}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return self.registry.get(run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.registry.list(limit)

    @property
    def registry(self) -> RunRegistry:
        return RunRegistry(self.report_directory / ".registry")

    def _save(self, result: dict[str, Any]) -> None:
        self.report_directory.mkdir(parents=True, exist_ok=True)
        path = self.report_directory / f"{result['run_id']}.json"
        path.write_text(
            f"{json.dumps(result, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
