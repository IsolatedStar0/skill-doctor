from __future__ import annotations

import json
import queue
import re
import threading
import time
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

from .graph import build_agent_graph
from .llm import build_deepseek_client
from .models import (
    AgentState,
    CandidateSkillRequest,
    CandidateValidationRequest,
    DiagnosticCaseRequest,
    DiagnosticSuiteRequest,
    DiagnosticExpectation,
    RepairPreviewRequest,
    RepairVerificationRequest,
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
from .storage import StorageBackend, build_storage_backend
from .workers import (
    BenchmarkReplayWorker,
    CodexExecutionWorker,
    ExecutionWorker,
    FixtureWorker,
    UploadedTraceWorker,
)


SCENARIO_CATALOG: list[dict[str, Any]] = [
    {
        "id": "content-gap",
        "name": "内容缺口",
        "summary": "Skill 已加载但遗漏关键约束，期望进入 Skill 修订链路。",
        "category": "skill",
        "skill_id": "spreadsheet-summary",
        "task": "读取 100 行订单 CSV，汇总总营收并输出 Markdown 报告。",
        "expected": "¥428,650（基于全部 100 行订单）",
        "actual": "¥82,410（错误地只统计预览的 20 行）",
        "executor": "fixture",
        "repair_action": "patch_skill",
    },
    {
        "id": "loading-miss",
        "name": "加载遗漏",
        "summary": "Skill 被选中但引用资源缺失，期望归因为 loader 问题。",
        "category": "loader",
        "skill_id": "release-checklist",
        "task": "按仓库规范生成发布检查清单，并包含安全回滚步骤。",
        "expected": "依据 release-policy references 生成 8 项检查清单",
        "actual": "只生成 4 项通用检查，缺少回滚和审批门禁",
        "executor": "fixture",
        "repair_action": "patch_loader",
    },
    {
        "id": "platform-error",
        "name": "平台异常",
        "summary": "执行失败来自外部服务边界，期望拒绝修改 Skill。",
        "category": "platform",
        "skill_id": "skill-release",
        "task": "把验证通过的候选 Skill 发布到远端注册表。",
        "expected": "注册表返回 release id，候选版本进入 staged 状态",
        "actual": "registry.publish 返回 403 insufficient_scope",
        "executor": "fixture",
        "repair_action": "split_non_skill",
    },
]


class RunService:
    def __init__(
        self,
        project_root: Path | None = None,
        exporter_factory: Callable[
            [str, RunRequest],
            LangSmithRunExporter,
        ] = create_observability_exporter,
        storage: StorageBackend | None = None,
    ) -> None:
        self.project_root = (
            project_root
            or Path(__file__).resolve().parents[2]
        ).resolve()
        self.storage = storage or build_storage_backend(self.project_root)
        self.exporter_factory = exporter_factory
        # Build a shared DeepSeek LLM client once per service. Returns None
        # gracefully when DEEPSEEK_API_KEY / openai SDK is unavailable, in
        # which case Skill-Adaptor stages keep using their rule-based
        # deterministic fallbacks.
        self.adaptor_llm_client = build_deepseek_client()

    def list_scenarios(self) -> dict[str, Any]:
        """Return supported failure scenarios for UI launchers and catalog views."""

        return {
            "schema_version": "1.0",
            "scenarios": deepcopy(SCENARIO_CATALOG),
        }

    @property
    def report_directory(self) -> Path:
        return self.storage.run_directory

    @report_directory.setter
    def report_directory(self, value: Path) -> None:
        self.storage.run_directory = value
        self.storage.benchmark_directory = (
            value.parent / "benchmarks"
            if value.name == "langgraph"
            else value / "benchmarks"
        )

    @property
    def diagnostic_case_directory(self) -> Path:
        return self.storage.diagnostic_case_directory

    @diagnostic_case_directory.setter
    def diagnostic_case_directory(self, value: Path) -> None:
        self.storage.diagnostic_case_directory = value

    @property
    def candidate_skill_directory(self) -> Path:
        return self.storage.candidate_skill_directory

    @candidate_skill_directory.setter
    def candidate_skill_directory(self, value: Path) -> None:
        self.storage.candidate_skill_directory = value

    @property
    def rejection_memory_directory(self) -> Path:
        return self.storage.rejection_memory_directory

    @rejection_memory_directory.setter
    def rejection_memory_directory(self, value: Path) -> None:
        self.storage.rejection_memory_directory = value

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

        return [
            DiagnosticCaseRequest.model_validate(payload)
            for payload in self.storage.list_diagnostic_cases()
        ]

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
        path = self.storage.save_diagnostic_case(
            case.case_id,
            case.model_dump(mode="json"),
        )
        return {
            "status": "saved",
            "path": path,
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

    def create_candidate_skill_from_run(
        self,
        run_id: str,
        request: CandidateSkillRequest | None = None,
    ) -> dict[str, Any]:
        """Persist a non-mutating candidate Skill revision from one run."""

        options = request or CandidateSkillRequest()
        state = self.get(run_id)
        preview = self.create_repair_preview(
            run_id,
            RepairPreviewRequest(include_full_skill=options.include_full_skill),
        )
        before = state.get("skill_content") or preview["suggested_patch"].get("before", "")
        after = preview["suggested_patch"].get("after", "")
        rejection_memory = self._matching_rejection_history(
            str(state.get("skill_id") or ""),
            preview.get("attribution", {}),
        )
        if rejection_memory:
            after = self._skill_with_rejection_constraints(after, rejection_memory)
        if not after or after == before:
            raise ValueError("Repair preview did not produce a candidate skill change.")
        candidate_id = f"cand-{uuid4().hex[:12]}"
        base_version = state.get("skill_version", "unknown")
        candidate = {
            "schema_version": "1.0",
            "candidate_id": candidate_id,
            "status": "candidate_only",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "created_from_run_id": run_id,
            "skill_id": state.get("skill_id"),
            "base_version": base_version,
            "candidate_version": self._candidate_version(base_version),
            "repair_type": preview.get("repair_type"),
            "risk": preview.get("risk"),
            "note": options.note or "",
            "diagnosis": preview.get("diagnosis", ""),
            "principle": preview.get("principle", ""),
            "attribution": preview.get("attribution", {}),
            "skill_content_before": before,
            "skill_content_after": after,
            "suggested_patch": preview.get("suggested_patch", {}),
            "verification_plan": preview.get("verification_plan", []),
            "rejection_memory": {
                "matched_count": len(rejection_memory),
                "constraints": self._rejection_constraints(rejection_memory),
                "matches": rejection_memory,
            },
            "can_apply": False,
            "can_apply_reason": "候选 Skill 仅用于验证，不会覆盖生产 Skill；通过验证后再人工采纳。",
        }
        path = self.storage.save_candidate_skill(candidate_id, candidate)
        return {
            "status": "created",
            "path": path,
            "candidate": candidate,
        }

    def validate_candidate_skill(
        self,
        candidate_id: str,
        request: CandidateValidationRequest | None = None,
    ) -> dict[str, Any]:
        """Run a suite-level validation gate for a candidate Skill revision."""

        options = request or CandidateValidationRequest()
        candidate = self._load_candidate_skill(candidate_id)
        base_cases = self._diagnostic_cases_for_validation(
            include_default_cases=options.include_default_cases,
            include_saved_cases=options.include_saved_cases,
        )
        baseline_report = self.run_diagnostic_suite(
            DiagnosticSuiteRequest(
                suite_id="candidate-baseline-suite",
                name="Candidate Baseline Suite",
                include_default_cases=False,
                include_saved_cases=False,
                cases=base_cases,
            )
        )
        candidate_cases = self._candidate_diagnostic_cases(base_cases, candidate)
        candidate_report = self.run_diagnostic_suite(
            DiagnosticSuiteRequest(
                suite_id="candidate-validation-suite",
                name="Candidate Validation Suite",
                include_default_cases=False,
                include_saved_cases=False,
                cases=candidate_cases,
            )
        )
        baseline_by_id = {item["case_id"]: item for item in baseline_report["cases"]}
        candidate_by_id = {item["case_id"]: item for item in candidate_report["cases"]}
        fixed_cases = [
            case_id
            for case_id, item in candidate_by_id.items()
            if not baseline_by_id.get(case_id, {}).get("passed", True) and item["passed"]
        ]
        regressed_cases = [
            case_id
            for case_id, item in candidate_by_id.items()
            if baseline_by_id.get(case_id, {}).get("passed") and not item["passed"]
        ]
        baseline_pass_rate = baseline_report["summary"]["pass_rate"]
        candidate_pass_rate = candidate_report["summary"]["pass_rate"]
        pass_rate_delta = candidate_pass_rate - baseline_pass_rate
        validation_rejection_matches = self._matching_rejection_history(
            str(candidate.get("skill_id") or ""),
            candidate.get("attribution", {}),
            candidate,
        )
        checks = self._candidate_validation_checks(
            candidate,
            baseline_report,
            candidate_report,
            fixed_cases,
            regressed_cases,
            options.decision_policy,
            validation_rejection_matches,
        )
        decision = "ADOPT" if all(item["passed"] for item in checks) else "REJECT"
        reasons = self._candidate_validation_reasons(
            decision,
            checks,
            fixed_cases,
            regressed_cases,
            pass_rate_delta,
        )
        report = {
            "schema_version": "1.0",
            "status": "validated",
            "decision": decision,
            "policy": options.decision_policy,
            "candidate_id": candidate_id,
            "skill_id": candidate.get("skill_id"),
            "base_version": candidate.get("base_version"),
            "candidate_version": candidate.get("candidate_version"),
            "baseline": baseline_report["summary"],
            "candidate": candidate_report["summary"],
            "delta": {
                "pass_rate_delta": pass_rate_delta,
                "fixed_cases": fixed_cases,
                "regressed_cases": regressed_cases,
            },
            "checks": checks,
            "reasons": reasons,
            "rejection_memory": {
                "matched_count": len(validation_rejection_matches),
                "constraints": (candidate.get("rejection_memory") or {}).get("constraints", []),
                "matches": validation_rejection_matches,
                "recorded": None,
            },
            "baseline_report": baseline_report,
            "candidate_report": candidate_report,
        }
        if decision == "REJECT":
            report["rejection_memory"]["recorded"] = self._record_rejection_history(
                candidate,
                report,
                fixed_cases,
                regressed_cases,
            )
        report["markdown"] = self._candidate_validation_markdown(
            candidate,
            decision,
            checks,
            reasons,
            baseline_pass_rate,
            candidate_pass_rate,
            report["rejection_memory"],
        )
        candidate["last_validation"] = report
        self.storage.save_candidate_skill(candidate_id, candidate)
        return report

    def list_rejection_history(
        self,
        skill_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return persisted rejected candidate memories for review / reuse."""

        records = self._load_rejection_history(skill_id)
        records = sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)
        return {
            "schema_version": "1.0",
            "skill_id": skill_id,
            "count": len(records),
            "records": records[:limit],
        }

    def verify_repair(
        self,
        request: RepairVerificationRequest,
    ) -> dict[str, Any]:
        """Compare baseline and candidate runs to decide ADOPT / REJECT."""

        baseline = self.get(request.baseline_run_id)
        candidate = self.get(request.candidate_run_id)
        baseline_execution = baseline.get("execution")
        candidate_execution = candidate.get("execution")
        if not baseline_execution or not candidate_execution:
            raise ValueError("Both baseline and candidate runs must include execution payloads.")
        if baseline.get("skill_id") != candidate.get("skill_id"):
            raise ValueError("Baseline and candidate runs must target the same skill_id.")

        baseline_pass_rate = float(baseline_execution.get("pass_rate") or 0)
        candidate_pass_rate = float(candidate_execution.get("pass_rate") or 0)
        pass_rate_delta = candidate_pass_rate - baseline_pass_rate
        baseline_regression = float(baseline_execution.get("regression_rate") or 0)
        candidate_regression = float(candidate_execution.get("regression_rate") or 0)
        regression_rate_delta = candidate_regression - baseline_regression
        baseline_attribution = baseline.get("attribution") or {}
        candidate_attribution = candidate.get("attribution") or {}
        saved_case_count = len(self.load_saved_diagnostic_cases()) if request.include_saved_cases else 0

        checks = self._repair_verification_checks(
            baseline,
            candidate,
            baseline_pass_rate,
            candidate_pass_rate,
            regression_rate_delta,
            request.decision_policy,
            saved_case_count,
        )
        decision = "ADOPT" if all(item["passed"] for item in checks) else "REJECT"
        reasons = self._repair_verification_reasons(
            decision,
            checks,
            baseline_attribution,
            candidate_attribution,
            pass_rate_delta,
            regression_rate_delta,
        )
        report = {
            "schema_version": "1.0",
            "status": "verified",
            "decision": decision,
            "policy": request.decision_policy,
            "baseline": self._verification_run_summary(baseline, baseline_execution),
            "candidate": self._verification_run_summary(candidate, candidate_execution),
            "delta": {
                "pass_rate_delta": pass_rate_delta,
                "regression_rate_delta": regression_rate_delta,
                "status_changed": baseline.get("status") != candidate.get("status"),
            },
            "checks": checks,
            "reasons": reasons,
            "saved_cases": {
                "included": request.include_saved_cases,
                "count": saved_case_count,
            },
            "attribution": {
                "baseline_cause": baseline_attribution.get("cause", "none"),
                "baseline_fault_type": baseline_attribution.get("fault_type", "none"),
                "candidate_cause": candidate_attribution.get("cause", "none"),
                "candidate_fault_type": candidate_attribution.get("fault_type", "none"),
            },
            "markdown": self._repair_verification_markdown(
                baseline,
                candidate,
                decision,
                checks,
                reasons,
                pass_rate_delta,
                regression_rate_delta,
            ),
        }
        candidate["verification"] = {
            "decision": decision,
            "baseline_pass_rate": baseline_pass_rate,
            "candidate_pass_rate": candidate_pass_rate,
            "pass_rate_delta": pass_rate_delta,
            "regression_rate": candidate_regression,
            "reasons": reasons,
            "regression_detected": regression_rate_delta > 0,
            "sample_size": 2 + saved_case_count,
            "qualifier_reason": "; ".join(reasons),
        }
        candidate["repair_verification"] = report
        self.registry.publish(candidate)
        self._save(candidate)
        return report

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

    def _diagnostic_cases_for_validation(
        self,
        *,
        include_default_cases: bool,
        include_saved_cases: bool,
    ) -> list[DiagnosticCaseRequest]:
        return [
            *(_default_diagnostic_cases() if include_default_cases else []),
            *(self.load_saved_diagnostic_cases() if include_saved_cases else []),
        ]

    @staticmethod
    def _candidate_diagnostic_cases(
        cases: list[DiagnosticCaseRequest],
        candidate: dict[str, Any],
    ) -> list[DiagnosticCaseRequest]:
        updated: list[DiagnosticCaseRequest] = []
        skill_id = candidate.get("skill_id")
        for case in cases:
            trace = case.trace
            if trace.skill_id != skill_id:
                updated.append(case)
                continue
            updated_trace = trace.model_copy(
                update={
                    "skill_version": candidate.get("candidate_version")
                    or trace.skill_version,
                    "skill_content": candidate.get("skill_content_after")
                    or trace.skill_content,
                    "trace_metadata": {
                        **trace.trace_metadata,
                        "candidate_id": candidate.get("candidate_id"),
                        "candidate_validation": True,
                    },
                },
                deep=True,
            )
            updated.append(case.model_copy(update={"trace": updated_trace}, deep=True))
        return updated

    @staticmethod
    def _candidate_validation_checks(
        candidate: dict[str, Any],
        baseline_report: dict[str, Any],
        candidate_report: dict[str, Any],
        fixed_cases: list[str],
        regressed_cases: list[str],
        policy: str,
        rejection_matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        threshold = 0.0 if policy == "strict" else -0.05
        baseline_pass_rate = baseline_report["summary"]["pass_rate"]
        candidate_pass_rate = candidate_report["summary"]["pass_rate"]
        source_case_id = str(candidate.get("created_from_run_id") or "")
        source_related_fixed = bool(fixed_cases) or candidate_pass_rate >= baseline_pass_rate
        exact_duplicates = [
            item
            for item in rejection_matches
            if item.get("match_reason") == "exact_rejected_patch"
        ]
        return [
            {
                "name": "candidate_has_skill_change",
                "label": "候选 Skill 内容存在变更",
                "expected": True,
                "actual": candidate.get("skill_content_before")
                != candidate.get("skill_content_after"),
                "passed": candidate.get("skill_content_before")
                != candidate.get("skill_content_after"),
            },
            {
                "name": "suite_pass_rate_not_worse",
                "label": "候选套件通过率未低于 baseline",
                "expected": f">= {baseline_pass_rate + threshold:.2f}",
                "actual": candidate_pass_rate,
                "passed": candidate_pass_rate - baseline_pass_rate >= threshold,
            },
            {
                "name": "no_regressed_cases",
                "label": "无新增回归用例失败",
                "expected": [],
                "actual": regressed_cases,
                "passed": not regressed_cases,
            },
            {
                "name": "source_failure_addressed",
                "label": "源失败样本得到修复或整体指标不退化",
                "expected": True,
                "actual": source_related_fixed,
                "passed": source_related_fixed,
                "metadata": {"created_from_run_id": source_case_id},
            },
            {
                "name": "not_duplicate_rejected_candidate",
                "label": "未重复提交历史已拒候选补丁",
                "expected": [],
                "actual": [item.get("rejection_id") for item in exact_duplicates],
                "passed": not exact_duplicates,
            },
            {
                "name": "candidate_suite_completed",
                "label": "候选诊断套件完成",
                "expected": "terminal",
                "actual": candidate_report.get("status"),
                "passed": candidate_report.get("status") in {"passed", "failed"},
            },
        ]

    @staticmethod
    def _candidate_validation_reasons(
        decision: str,
        checks: list[dict[str, Any]],
        fixed_cases: list[str],
        regressed_cases: list[str],
        pass_rate_delta: float,
    ) -> list[str]:
        failed = [item for item in checks if not item["passed"]]
        if failed:
            return [
                f"候选验证结论为 {decision}：{item['label']} 未满足，实际值={item['actual']}。"
                for item in failed
            ]
        return [
            f"候选验证结论为 {decision}：套件通过率变化 {pass_rate_delta:.2%}。",
            f"修复用例数 {len(fixed_cases)}，新增回归用例数 {len(regressed_cases)}。",
            "候选 Skill 仍未写回生产 Skill，可在人工确认后采纳。",
        ]

    @staticmethod
    def _candidate_validation_markdown(
        candidate: dict[str, Any],
        decision: str,
        checks: list[dict[str, Any]],
        reasons: list[str],
        baseline_pass_rate: float,
        candidate_pass_rate: float,
        rejection_memory: dict[str, Any],
    ) -> str:
        lines = [
            "# Skill Doctor Candidate Validation",
            "",
            f"- Decision：{decision}",
            f"- Candidate：`{candidate.get('candidate_id')}`",
            f"- Skill：{candidate.get('skill_id')}",
            f"- Baseline Pass Rate：{baseline_pass_rate:.2%}",
            f"- Candidate Pass Rate：{candidate_pass_rate:.2%}",
            "",
            "## Checks",
        ]
        for item in checks:
            icon = "✅" if item["passed"] else "❌"
            lines.append(f"- {icon} {item['label']}：{item['actual']}")
        lines.extend(["", "## Reasons"])
        lines.extend(f"- {reason}" for reason in reasons)
        lines.extend(["", "## Reject Memory"])
        lines.append(f"- 匹配历史拒绝记录：{rejection_memory.get('matched_count', 0)}")
        recorded = rejection_memory.get("recorded")
        if recorded:
            lines.append(f"- 本次拒绝已记录：`{recorded.get('rejection_id')}`")
        constraints = rejection_memory.get("constraints") or []
        if constraints:
            lines.append("- 生成候选时已注入约束：")
            lines.extend(f"  - {item}" for item in constraints)
        return "\n".join(lines).strip() + "\n"

    def _load_rejection_history(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        return self.storage.list_rejection_memory(skill_id)

    def _matching_rejection_history(
        self,
        skill_id: str,
        attribution: dict[str, Any],
        candidate: dict[str, Any] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        candidate_patch_sha = (
            self._text_sha256(str(candidate.get("skill_content_after") or ""))
            if candidate
            else ""
        )
        fault_type = attribution.get("fault_type")
        action = attribution.get("action")
        matches: list[dict[str, Any]] = []
        for record in self._load_rejection_history(skill_id):
            reason = ""
            if candidate_patch_sha and record.get("patch_sha256") == candidate_patch_sha:
                reason = "exact_rejected_patch"
            elif fault_type and record.get("fault_type") == fault_type:
                reason = "same_fault_type"
            elif action and record.get("action") == action:
                reason = "same_action"
            if not reason:
                continue
            summary = {
                "rejection_id": record.get("rejection_id"),
                "candidate_id": record.get("candidate_id"),
                "created_at": record.get("created_at"),
                "skill_id": record.get("skill_id"),
                "fault_type": record.get("fault_type"),
                "action": record.get("action"),
                "decision": record.get("decision"),
                "failed_checks": record.get("failed_checks", []),
                "reasons": record.get("reasons", []),
                "regressed_cases": record.get("regressed_cases", []),
                "patch_summary": record.get("patch_summary", ""),
                "match_reason": reason,
            }
            matches.append(summary)
        return sorted(matches, key=lambda item: item.get("created_at", ""), reverse=True)[:limit]

    @staticmethod
    def _rejection_constraints(matches: list[dict[str, Any]]) -> list[str]:
        constraints: list[str] = []
        for item in matches[:3]:
            failed = ", ".join(str(value) for value in item.get("failed_checks", [])[:3])
            reason = str((item.get("reasons") or [""])[0])[:180]
            constraints.append(
                "避免重复历史拒绝方案 "
                f"{item.get('rejection_id')}（{item.get('match_reason')}）："
                f"失败检查={failed or 'unknown'}；原因={reason or '未记录'}"
            )
        return constraints

    def _skill_with_rejection_constraints(
        self,
        skill_content: str,
        matches: list[dict[str, Any]],
    ) -> str:
        constraints = self._rejection_constraints(matches)
        if not constraints:
            return skill_content
        block = "\n".join(f"- {item}" for item in constraints)
        return (
            f"{skill_content.rstrip()}\n\n"
            "## Reject Memory 约束\n"
            "以下历史候选已被验证门禁拒绝，生成/执行本候选时必须规避相同失败模式：\n"
            f"{block}"
        ).strip()

    def _record_rejection_history(
        self,
        candidate: dict[str, Any],
        report: dict[str, Any],
        fixed_cases: list[str],
        regressed_cases: list[str],
    ) -> dict[str, Any]:
        rejection_id = f"rej-{uuid4().hex[:12]}"
        failed_checks = [
            item["name"]
            for item in report.get("checks", [])
            if not item.get("passed")
        ]
        attribution = candidate.get("attribution") or {}
        record = {
            "schema_version": "1.0",
            "rejection_id": rejection_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "candidate_id": candidate.get("candidate_id"),
            "created_from_run_id": candidate.get("created_from_run_id"),
            "skill_id": candidate.get("skill_id"),
            "base_version": candidate.get("base_version"),
            "candidate_version": candidate.get("candidate_version"),
            "decision": report.get("decision"),
            "policy": report.get("policy"),
            "fault_type": attribution.get("fault_type"),
            "action": attribution.get("action"),
            "principle": candidate.get("principle", ""),
            "diagnosis": candidate.get("diagnosis", ""),
            "reasons": report.get("reasons", []),
            "failed_checks": failed_checks,
            "fixed_cases": fixed_cases,
            "regressed_cases": regressed_cases,
            "baseline_pass_rate": (report.get("baseline") or {}).get("pass_rate"),
            "candidate_pass_rate": (report.get("candidate") or {}).get("pass_rate"),
            "pass_rate_delta": (report.get("delta") or {}).get("pass_rate_delta"),
            "patch_sha256": self._text_sha256(str(candidate.get("skill_content_after") or "")),
            "patch_summary": (candidate.get("suggested_patch") or {}).get("summary", ""),
        }
        path = self.storage.save_rejection_memory(rejection_id, record)
        return {
            "rejection_id": rejection_id,
            "path": path,
            "failed_checks": failed_checks,
        }

    @staticmethod
    def _text_sha256(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _candidate_path(self, candidate_id: str) -> Path:
        if not candidate_id.startswith("cand-") or not candidate_id[5:].isalnum():
            raise ValueError("Invalid candidate id.")
        return self.candidate_skill_directory / f"{candidate_id}.json"

    def _load_candidate_skill(self, candidate_id: str) -> dict[str, Any]:
        self._candidate_path(candidate_id)
        return self.storage.get_candidate_skill(candidate_id)

    @staticmethod
    def _candidate_version(base_version: str) -> str:
        if base_version and base_version != "unknown":
            return f"{base_version}+candidate.1"
        return "candidate.1"

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
    def _verification_run_summary(
        state: dict[str, Any],
        execution: dict[str, Any],
    ) -> dict[str, Any]:
        attribution = state.get("attribution") or {}
        return {
            "run_id": state.get("run_id"),
            "skill_id": state.get("skill_id"),
            "status": state.get("status"),
            "stop_reason": state.get("stop_reason", ""),
            "pass_rate": float(execution.get("pass_rate") or 0),
            "regression_rate": float(execution.get("regression_rate") or 0),
            "passed": bool(execution.get("passed")),
            "summary": execution.get("summary", ""),
            "attribution": {
                "cause": attribution.get("cause", "none"),
                "fault_type": attribution.get("fault_type", "none"),
                "action": attribution.get("action", "none"),
            },
        }

    @staticmethod
    def _repair_verification_checks(
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        baseline_pass_rate: float,
        candidate_pass_rate: float,
        regression_rate_delta: float,
        policy: str,
        saved_case_count: int,
    ) -> list[dict[str, Any]]:
        candidate_attribution = candidate.get("attribution") or {}
        regression_limit = 0.0 if policy == "strict" else 0.05
        original_fault = (baseline.get("attribution") or {}).get("fault_type", "none")
        candidate_fault = candidate_attribution.get("fault_type", "none")
        no_new_non_skill_failure = not (
            candidate.get("status") == "failed"
            and candidate_attribution.get("cause") in {"tool", "platform"}
        )
        return [
            {
                "name": "baseline_has_failure",
                "label": "baseline 存在待修复失败",
                "expected": "failed",
                "actual": baseline.get("status"),
                "passed": baseline.get("status") == "failed",
            },
            {
                "name": "candidate_passed",
                "label": "candidate run 已通过",
                "expected": "passed",
                "actual": candidate.get("status"),
                "passed": candidate.get("status") == "passed",
            },
            {
                "name": "pass_rate_improved",
                "label": "通过率相比 baseline 提升",
                "expected": f"> {baseline_pass_rate:.2f}",
                "actual": candidate_pass_rate,
                "passed": candidate_pass_rate > baseline_pass_rate,
            },
            {
                "name": "no_regression_increase",
                "label": "未引入新的回归率上升",
                "expected": f"<= {regression_limit:.2f}",
                "actual": regression_rate_delta,
                "passed": regression_rate_delta <= regression_limit,
            },
            {
                "name": "original_fault_resolved",
                "label": "原故障类型不再出现",
                "expected": f"not {original_fault}",
                "actual": candidate_fault,
                "passed": candidate.get("status") == "passed" or candidate_fault != original_fault,
            },
            {
                "name": "no_new_non_skill_failure",
                "label": "未新增 tool/platform 失败",
                "expected": True,
                "actual": no_new_non_skill_failure,
                "passed": no_new_non_skill_failure,
            },
            {
                "name": "saved_cases_loaded",
                "label": "本地真实回归用例已纳入验证上下文",
                "expected": ">= 0",
                "actual": saved_case_count,
                "passed": True,
            },
        ]

    @staticmethod
    def _repair_verification_reasons(
        decision: str,
        checks: list[dict[str, Any]],
        baseline_attribution: dict[str, Any],
        candidate_attribution: dict[str, Any],
        pass_rate_delta: float,
        regression_rate_delta: float,
    ) -> list[str]:
        failed = [item for item in checks if not item["passed"]]
        if failed:
            return [
                f"验证结论为 {decision}：{item['label']} 未满足，实际值={item['actual']}。"
                for item in failed
            ]
        baseline_fault = baseline_attribution.get("fault_type", "none")
        candidate_fault = candidate_attribution.get("fault_type", "none")
        return [
            f"验证结论为 {decision}：candidate 通过率提升 {pass_rate_delta:.2%}。",
            f"原故障类型 {baseline_fault} 已消除，candidate 当前故障类型为 {candidate_fault}。",
            f"回归率变化 {regression_rate_delta:.2%}，未超过验证策略阈值。",
        ]

    @staticmethod
    def _repair_verification_markdown(
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        decision: str,
        checks: list[dict[str, Any]],
        reasons: list[str],
        pass_rate_delta: float,
        regression_rate_delta: float,
    ) -> str:
        lines = [
            "# Skill Doctor Repair Verification",
            "",
            f"- Decision：{decision}",
            f"- Baseline：`{baseline.get('run_id')}` / {baseline.get('status')}",
            f"- Candidate：`{candidate.get('run_id')}` / {candidate.get('status')}",
            f"- Pass Rate Delta：{pass_rate_delta:.2%}",
            f"- Regression Rate Delta：{regression_rate_delta:.2%}",
            "",
            "## Checks",
        ]
        for item in checks:
            icon = "✅" if item["passed"] else "❌"
            lines.append(f"- {icon} {item['label']}：{item['actual']}")
        lines.extend(["", "## Reasons"])
        lines.extend(f"- {reason}" for reason in reasons)
        return "\n".join(lines).strip() + "\n"

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
        try:
            return self.storage.get_run(run_id)
        except FileNotFoundError:
            pass
        return self.registry.get(run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.registry.list(limit)

    @property
    def registry(self) -> RunRegistry:
        return RunRegistry(self.report_directory / ".registry")

    def _save(self, result: dict[str, Any]) -> None:
        self.storage.save_run(result)


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
