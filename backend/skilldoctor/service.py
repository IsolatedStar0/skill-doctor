from __future__ import annotations

import json
import queue
import re
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
    DiagnosticExpectation,
    RepairPreviewRequest,
    RunEvent,
    RunRequest,
    SaveDiagnosticCaseRequest,
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
        self.diagnostic_case_directory = self.project_root / "diagnostic_cases"
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
        cases = [
            *(_default_diagnostic_cases() if suite.include_default_cases else []),
            *(self.load_saved_diagnostic_cases() if suite.include_saved_cases else []),
            *suite.cases,
        ]
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        reports = [self._run_diagnostic_case(case) for case in cases]
        passed = sum(1 for item in reports if item["passed"])
        failed = len(reports) - passed
        saved = sum(1 for item in reports if item.get("source") == "saved_run")
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
                "saved_cases": saved,
            },
            "cases": reports,
            "markdown": self._diagnostic_markdown(suite.name, reports),
        }

    def load_saved_diagnostic_cases(self) -> list[DiagnosticCaseRequest]:
        """Load locally persisted real-trace regression cases."""

        if not self.diagnostic_case_directory.is_dir():
            return []
        cases: list[DiagnosticCaseRequest] = []
        for path in sorted(self.diagnostic_case_directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            case = DiagnosticCaseRequest.model_validate(payload)
            cases.append(case)
        return cases

    def save_diagnostic_case_from_run(
        self,
        run_id: str,
        request: SaveDiagnosticCaseRequest | None = None,
    ) -> dict[str, Any]:
        """Persist an existing run as a reusable diagnostic regression case."""

        state = self.get(run_id)
        execution = state.get("execution")
        if not execution:
            raise ValueError("Run has no execution payload to save as a diagnostic case.")

        attribution = state.get("attribution") or {}
        requested = request or SaveDiagnosticCaseRequest()
        expectation = self._filled_expectation(requested.expectation, state, attribution)
        case_id = requested.case_id or self._case_id_from_state(state, attribution)
        case = DiagnosticCaseRequest.model_validate(
            {
                "case_id": case_id,
                "name": requested.name or self._case_name_from_state(state, attribution),
                "description": requested.description
                or self._case_description_from_state(state, attribution),
                "source": "saved_run",
                "trace": {
                    "task": state.get("task", "Imported Aime Skill execution trace."),
                    "skill_id": state.get("skill_id"),
                    "skill_version": state.get("skill_version", "unknown"),
                    "skill_content": state.get("skill_content", ""),
                    "condition": state.get("condition", "standard"),
                    "parent_run_id": run_id,
                    "repair_enabled": bool(state.get("repair_enabled", True)),
                    "max_attempts": int(state.get("max_attempts", 1)),
                    "execution": execution,
                    "business_result": state.get("business_result"),
                    "trace_metadata": {
                        "saved_from_run_id": run_id,
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "source": "saved_run",
                    },
                },
                "expectation": expectation.model_dump(mode="json"),
            }
        )
        self.diagnostic_case_directory.mkdir(parents=True, exist_ok=True)
        path = self.diagnostic_case_directory / f"{case.case_id}.json"
        path.write_text(
            f"{json.dumps(case.model_dump(mode='json'), ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        return {
            "status": "saved",
            "path": str(path.relative_to(self.project_root)),
            "case": case.model_dump(mode="json"),
        }

    def create_repair_preview(
        self,
        run_id: str,
        request: RepairPreviewRequest | None = None,
    ) -> dict[str, Any]:
        """Generate an auditable, non-mutating repair proposal for one run."""

        state = self.get(run_id)
        attribution = state.get("attribution")
        if not attribution:
            raise ValueError("Run has no attribution result to preview a repair.")
        options = request or RepairPreviewRequest()
        action = attribution.get("action", "split_non_skill")
        principle = (
            attribution.get("improvement_principle")
            or attribution.get("agent_conclusion")
            or attribution.get("explanation")
            or "根据归因结果补充缺失约束，并通过回归用例验证。"
        )
        skill_content = state.get("skill_content") or ""
        before = skill_content if options.include_full_skill else self._skill_preview(skill_content)
        added_rule = self._repair_instruction(principle, attribution)
        after = f"{before.rstrip()}\n\n{added_rule}".strip() if before else added_rule
        can_apply = action in {"patch_skill", "patch_loader"}
        repair_type = "skill_revision" if action == "patch_skill" else "loader_revision" if action == "patch_loader" else "manual_triage"
        return {
            "schema_version": "1.0",
            "run_id": run_id,
            "skill_id": state.get("skill_id"),
            "status": "preview_only",
            "repair_type": repair_type,
            "can_apply": False,
            "risk": self._repair_risk(attribution),
            "diagnosis": attribution.get("agent_reason") or attribution.get("explanation", ""),
            "principle": principle,
            "attribution": {
                "taxonomy": attribution.get("taxonomy"),
                "cause": attribution.get("cause"),
                "fault_type": attribution.get("fault_type"),
                "action": action,
                "confidence": attribution.get("confidence"),
                "agent_source": attribution.get("agent_source", "none"),
                "t_star": attribution.get("t_star"),
                "fault_chain": attribution.get("fault_chain", []),
            },
            "suggested_patch": {
                "summary": self._repair_summary(action, principle),
                "before": before,
                "after": after,
                "diff": self._simple_diff(before, after),
            },
            "verification_plan": self._verification_plan(state, attribution),
            "notes": [
                "当前接口只生成可审查修复预览，不会直接修改 Skill 文件。",
                "建议先保存该 run 为回归用例，再用新 trace 验证修复效果。",
            ],
            "can_apply_reason": (
                "已定位到可修复通道，但需要用户确认后再接入写文件/MR。"
                if can_apply
                else "该问题被归因为 non-skill 或人工分流，不建议自动修改 Skill。"
            ),
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
            "source": case.source,
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

    def _filled_expectation(
        self,
        expectation: DiagnosticExpectation,
        state: dict[str, Any],
        attribution: dict[str, Any],
    ) -> DiagnosticExpectation:
        return expectation.model_copy(
            update={
                "status": expectation.status or state.get("status"),
                "cause": expectation.cause or attribution.get("cause"),
                "fault_type": expectation.fault_type or attribution.get("fault_type"),
                "action": expectation.action or attribution.get("action"),
                "should_repair": (
                    expectation.should_repair
                    if expectation.should_repair is not None
                    else state.get("repair_patch") is not None
                ),
                "should_call_llm": (
                    expectation.should_call_llm
                    if expectation.should_call_llm is not None
                    else attribution.get("agent_source") == "llm"
                ),
            }
        )

    @staticmethod
    def _case_id_from_state(
        state: dict[str, Any],
        attribution: dict[str, Any],
    ) -> str:
        raw = "-".join(
            str(part)
            for part in [
                state.get("skill_id", "skill"),
                attribution.get("fault_type") or state.get("status", "trace"),
                state.get("run_id", "run"),
            ]
            if part
        )
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw).strip("-._").lower()
        return slug[:96] or "saved-run-case"

    @staticmethod
    def _case_name_from_state(
        state: dict[str, Any],
        attribution: dict[str, Any],
    ) -> str:
        fault_type = attribution.get("fault_type") or state.get("status", "trace")
        return f"真实 Trace 回归：{state.get('skill_id', 'unknown')} / {fault_type}"

    @staticmethod
    def _case_description_from_state(
        state: dict[str, Any],
        attribution: dict[str, Any],
    ) -> str:
        explanation = attribution.get("agent_reason") or attribution.get("explanation")
        if explanation:
            return str(explanation)[:240]
        execution = state.get("execution") or {}
        return str(execution.get("summary") or "由真实 Aime Skill Trace 保存的回归用例。")[:240]

    @staticmethod
    def _skill_preview(skill_content: str, limit: int = 900) -> str:
        content = skill_content.strip()
        if len(content) <= limit:
            return content
        return content[:limit].rstrip() + "\n...（已截断，预览接口未展示完整 Skill）"

    @staticmethod
    def _repair_instruction(principle: str, attribution: dict[str, Any]) -> str:
        t_star = attribution.get("t_star")
        scope = f"t*={t_star}" if isinstance(t_star, int) else "定位到的失败步骤"
        return (
            "## Skill Doctor 修复建议\n"
            f"- 针对 {scope} 补充约束：{principle}\n"
            "- 在执行相关 tool 前显式校验关键入参、时间窗口和回退条件。\n"
            "- 当上游返回空数据或低置信度结果时，输出可解释错误并保留证据。"
        )

    @staticmethod
    def _repair_risk(attribution: dict[str, Any]) -> str:
        confidence = float(attribution.get("confidence") or 0)
        if attribution.get("cause") not in {"skill", "loader", "routing"}:
            return "high"
        if confidence >= 0.85:
            return "medium"
        return "high"

    @staticmethod
    def _repair_summary(action: str, principle: str) -> str:
        action_copy = {
            "patch_skill": "修改 Skill 内容",
            "patch_loader": "修复 Skill 加载逻辑",
            "patch_routing": "修复 Skill 路由策略",
            "split_non_skill": "分流为非 Skill 问题",
        }.get(action, action)
        return f"{action_copy}：{principle}"

    @staticmethod
    def _simple_diff(before: str, after: str) -> str:
        if before == after:
            return ""
        lines = []
        if before:
            lines.extend(f"- {line}" for line in before.splitlines())
        if after:
            lines.extend(f"+ {line}" for line in after.splitlines())
        return "\n".join(lines)

    @staticmethod
    def _verification_plan(
        state: dict[str, Any],
        attribution: dict[str, Any],
    ) -> list[str]:
        skill_id = state.get("skill_id", "目标 Skill")
        action = attribution.get("action")
        if action == "split_non_skill":
            return [
                "不要直接修改 Skill；先确认平台、tool 或外部依赖是否恢复。",
                "重新导入同一任务的新 Trace，确认失败不再出现或已被正确分流。",
                "检查诊断套件中 Non-Skill 用例没有被误判为 Skill 问题。",
            ]
        return [
            f"保存当前 run 为 {skill_id} 的回归用例。",
            "在 Aime 侧应用修复草案或等价人工修改。",
            "重新执行同一用户任务并上传新 Trace。",
            "确认失败断言转为通过，pass_rate 提升且 fault_type 不再是原故障。",
            "运行默认诊断套件，确认健康 Trace 与 Non-Skill 用例没有回归。",
        ]

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
                "source": "built-in",
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
                "source": "built-in",
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
                "source": "built-in",
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
                "source": "built-in",
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
