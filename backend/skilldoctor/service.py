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
from .models import (
    AgentState,
    DiagnosticCaseRequest,
    DiagnosticSuiteRequest,
    RunEvent,
    RunRequest,
    TraceIngestRequest,
)
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

    def run_diagnostic_suite(
        self,
        request: DiagnosticSuiteRequest | None = None,
    ) -> dict[str, Any]:
        suite = request or DiagnosticSuiteRequest()
        cases = [*(_default_diagnostic_cases() if suite.include_default_cases else []), *suite.cases]
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reports = [self._run_diagnostic_case(case) for case in cases]
        passed = sum(1 for item in reports if item["passed"])
        failed = len(reports) - passed
        return {
            "schema_version": "1.0",
            "suite_id": suite.suite_id,
            "name": suite.name,
            "generated_at": started_at,
            "status": "passed" if failed == 0 else "failed",
            "summary": {
                "total": len(reports),
                "passed": passed,
                "failed": failed,
                "pass_rate": passed / len(reports) if reports else 1.0,
                "repairable": sum(1 for item in reports if item["repairable"]),
                "non_skill": sum(1 for item in reports if item["category"] == "non_skill"),
                "llm_authored": sum(1 for item in reports if item["agent_source"] == "llm"),
            },
            "cases": reports,
            "markdown": self._diagnostic_markdown(suite.name, reports),
        }

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

    def _run_diagnostic_case(self, case: DiagnosticCaseRequest) -> dict[str, Any]:
        state = self.ingest_trace(case.trace)
        attribution = state.get("attribution") or {}
        repair_patch = state.get("repair_patch")
        verification = state.get("verification") or {}
        expectation = case.expectation
        checks: list[dict[str, Any]] = []

        def check(name: str, expected: Any, actual: Any) -> None:
            if expected is None:
                return
            checks.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": actual,
                    "passed": expected == actual,
                }
            )

        check("status", expectation.status, state.get("status"))
        check("cause", expectation.cause, attribution.get("cause"))
        check("fault_type", expectation.fault_type, attribution.get("fault_type"))
        check("action", expectation.action, attribution.get("action"))
        check("should_repair", expectation.should_repair, repair_patch is not None)
        check(
            "should_call_llm",
            expectation.should_call_llm,
            attribution.get("agent_source") == "llm",
        )

        if not checks:
            checks.append(
                {
                    "name": "state_completed",
                    "expected": "terminal_state",
                    "actual": state.get("status"),
                    "passed": state.get("status") in {"passed", "failed"},
                }
            )
        category = (
            "healthy"
            if state.get("status") == "passed" and not attribution
            else "skill"
            if attribution.get("cause") in {"skill", "loader", "routing"}
            else "non_skill"
        )
        return {
            "case_id": case.case_id,
            "name": case.name,
            "description": case.description,
            "passed": all(item["passed"] for item in checks),
            "category": category,
            "repairable": repair_patch is not None,
            "run_id": state["run_id"],
            "status": state.get("status"),
            "stop_reason": state.get("stop_reason", ""),
            "skill_id": state.get("skill_id"),
            "agent_source": attribution.get("agent_source", "none") if attribution else "none",
            "attribution": {
                "taxonomy": attribution.get("taxonomy", "Healthy Trace"),
                "cause": attribution.get("cause", "none"),
                "fault_type": attribution.get("fault_type", "none"),
                "action": attribution.get("action", "none"),
                "confidence": attribution.get("confidence", 1.0 if state.get("status") == "passed" else 0.0),
                "explanation": attribution.get("explanation", "Trace completed without attribution."),
            },
            "repair": (
                {
                    "kind": repair_patch.get("kind"),
                    "revision_type": repair_patch.get("revision_type", ""),
                    "principle": repair_patch.get("principle", ""),
                }
                if repair_patch
                else None
            ),
            "verification": {
                "decision": verification.get("decision", "N/A"),
                "pass_rate_delta": verification.get("pass_rate_delta", 0),
                "regression_rate": verification.get("regression_rate", 0),
                "reasons": verification.get("reasons", []),
            },
            "checks": checks,
        }

    @staticmethod
    def _diagnostic_markdown(name: str, reports: list[dict[str, Any]]) -> str:
        passed = sum(1 for item in reports if item["passed"])
        lines = [
            f"# {name}",
            "",
            f"- 用例总数：{len(reports)}",
            f"- 通过：{passed}",
            f"- 失败：{len(reports) - passed}",
            "",
        ]
        for item in reports:
            icon = "✅" if item["passed"] else "❌"
            attr = item["attribution"]
            lines.extend(
                [
                    f"## {icon} {item['name']}",
                    "",
                    f"- Run：`{item['run_id']}`",
                    f"- 状态：{item['status']} / {item['stop_reason']}",
                    f"- 归因：{attr['taxonomy']} · {attr['cause']} · {attr['fault_type']}",
                    f"- 动作：{attr['action']}",
                    f"- 解释：{attr['explanation']}",
                    "",
                ]
            )
        return "\n".join(lines).strip() + "\n"

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


def _default_diagnostic_cases() -> list[DiagnosticCaseRequest]:
    """Built-in trace regression cases that cover the main routing branches."""

    return [
        DiagnosticCaseRequest.model_validate(
            {
                "case_id": "healthy-aime-fast-path",
                "name": "健康 Trace 快路径",
                "description": "成功的 Aime Trace 不应进入归因/修复链路。",
                "trace": {
                    "task": "Summarize healthy trace.",
                    "skill_id": "healthy-skill",
                    "skill_version": "1.0.0",
                    "skill_content": "Follow the safe path.",
                    "repair_enabled": False,
                    "runtime_events": [
                        {
                            "stage": "aime.done",
                            "status": "completed",
                            "message": "Aime skill finished successfully.",
                        }
                    ],
                    "trace_metadata": {"confidence": 0.92},
                },
                "expectation": {
                    "status": "passed",
                    "should_repair": False,
                    "should_call_llm": False,
                },
            }
        ),
        DiagnosticCaseRequest.model_validate(
            {
                "case_id": "skill-content-gap",
                "name": "Skill 内容缺口",
                "description": "Skill 已加载但遗漏关键约束，应归因为 skill 内容问题。",
                "trace": {
                    "task": "读取全部订单并汇总营收。",
                    "skill_id": "spreadsheet-summary",
                    "skill_version": "1.2.0",
                    "skill_content": "预览输入并生成摘要。",
                    "repair_enabled": True,
                    "max_attempts": 1,
                    "execution": {
                        "executor": "aime-skill-trace",
                        "condition": "with_skill",
                        "passed": False,
                        "pass_rate": 0.5,
                        "duration_ms": 960,
                        "summary": "Only preview rows were processed.",
                        "assertions": [
                            {
                                "id": "full-input-coverage",
                                "source": "skill",
                                "passed": False,
                                "detail": "The procedure only processed preview rows.",
                            },
                            {
                                "id": "output-contract",
                                "source": "task",
                                "passed": True,
                                "detail": "The output schema is valid.",
                            },
                        ],
                        "runtime_events": [
                            {
                                "stage": "csv.preview",
                                "status": "completed",
                                "message": "Read 20/100 rows.",
                            }
                        ],
                    },
                },
                "expectation": {
                    "status": "failed",
                    "cause": "skill",
                    "fault_type": "skill_wrong",
                    "action": "patch_skill",
                    "should_repair": True,
                },
            }
        ),
        DiagnosticCaseRequest.model_validate(
            {
                "case_id": "loader-missing-skill",
                "name": "Skill 未加载",
                "description": "without_skill 基线失败时应归因为 loader/skill_missing。",
                "trace": {
                    "task": "Use the required Skill before planning.",
                    "skill_id": "tdd-workflow",
                    "skill_version": "1.0.0",
                    "skill_content": "Follow TDD workflow.",
                    "condition": "without_skill",
                    "repair_enabled": False,
                    "execution": {
                        "executor": "aime-skill-trace",
                        "condition": "without_skill",
                        "passed": False,
                        "pass_rate": 0.0,
                        "duration_ms": 500,
                        "summary": "Required skill was not loaded.",
                        "assertions": [
                            {
                                "id": "skill-loaded",
                                "source": "skill",
                                "passed": False,
                                "detail": "The target Skill was absent.",
                            }
                        ],
                    },
                },
                "expectation": {
                    "status": "failed",
                    "cause": "loader",
                    "fault_type": "skill_missing",
                    "action": "patch_loader",
                    "should_repair": False,
                },
            }
        ),
        DiagnosticCaseRequest.model_validate(
            {
                "case_id": "platform-network-failure",
                "name": "平台网络失败",
                "description": "网络/平台边界错误应路由为 non-skill。",
                "trace": {
                    "task": "Call external dependency.",
                    "skill_id": "external-api-skill",
                    "skill_version": "1.0.0",
                    "skill_content": "Retry transient dependency failures.",
                    "repair_enabled": False,
                    "execution": {
                        "executor": "aime-skill-trace",
                        "condition": "with_skill",
                        "passed": False,
                        "pass_rate": 0.0,
                        "duration_ms": 420,
                        "summary": "Network connection reset.",
                        "error": "network connection reset",
                        "assertions": [
                            {
                                "id": "external-service",
                                "source": "system",
                                "passed": False,
                                "detail": "Upstream service connection was reset.",
                            }
                        ],
                    },
                },
                "expectation": {
                    "status": "failed",
                    "cause": "platform",
                    "fault_type": "reasoning_wrong",
                    "action": "split_non_skill",
                    "should_repair": False,
                },
            }
        ),
    ]
