from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from skilldoctor_cli import main as cli_main
from skilldoctor_cli.exit_codes import (
    EXIT_BENCH_FAILED,
    EXIT_CODE_DESCRIPTIONS,
    EXIT_COMPARE_REJECTED,
    EXIT_DIAGNOSIS_FAILED,
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_QUALITY_GATE_FAILED,
)
from skilldoctor_cli.workspace import default_report_path, run_records_dir


def test_exit_codes_are_stable_for_platform_integrations() -> None:
    assert EXIT_OK == 0
    assert EXIT_ERROR == 1
    assert EXIT_DIAGNOSIS_FAILED == 10
    assert EXIT_QUALITY_GATE_FAILED == 20
    assert EXIT_BENCH_FAILED == 30
    assert EXIT_COMPARE_REJECTED == 40
    assert EXIT_INTERRUPTED == 130
    assert EXIT_CODE_DESCRIPTIONS == {
        EXIT_OK: "success",
        EXIT_ERROR: "general_error",
        EXIT_DIAGNOSIS_FAILED: "diagnosis_failed",
        EXIT_QUALITY_GATE_FAILED: "quality_gate_failed",
        EXIT_BENCH_FAILED: "bench_failed",
        EXIT_COMPARE_REJECTED: "compare_rejected",
        EXIT_INTERRUPTED: "interrupted",
    }


def test_default_reports_are_saved_under_unified_run_records_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("SKILL_DOCTOR_RUNS_DIR", raising=False)

    report_path = default_report_path(tmp_path, "ingest-aime")

    assert run_records_dir(tmp_path) == tmp_path / ".skilldoctor" / "runs"
    assert report_path.parent == tmp_path / ".skilldoctor" / "runs"
    assert report_path.name.startswith("ingest-aime-")
    assert report_path.suffix == ".json"


def test_run_records_dir_can_be_overridden_for_agent_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact_dir = tmp_path / "aime-artifacts" / "skilldoctor-runs"
    monkeypatch.setenv("SKILL_DOCTOR_RUNS_DIR", str(artifact_dir))

    assert run_records_dir(tmp_path) == artifact_dir.resolve()
    assert default_report_path(tmp_path, "diagnose", suffix="md").parent == artifact_dir.resolve()


class _TraceRequest:
    @classmethod
    def model_validate(cls, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class _DiagnosticCaseRequest:
    @classmethod
    def model_validate(cls, payload: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(**payload)


class _DiagnosticSuiteRequest:
    def __init__(self, **payload: Any) -> None:
        self.__dict__.update(payload)


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_jsonl(path: Path, payloads: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in payloads), encoding="utf-8")
    return path


def _fake_backend_modules(project_root: Path) -> dict[str, Any]:
    return {
        "TraceIngestRequest": _TraceRequest,
        "DiagnosticCaseRequest": _DiagnosticCaseRequest,
        "DiagnosticSuiteRequest": _DiagnosticSuiteRequest,
    }


def test_diagnose_writes_reports_and_returns_skill_failure_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import diagnose

    trace_path = _write_json(tmp_path / "failed-trace.json", {"skill_id": "demo"})
    json_out = tmp_path / "diagnose.json"
    md_out = tmp_path / "diagnose.md"
    state = {
        "run_id": "lg-test-failed",
        "status": "failed",
        "skill_id": "demo",
        "skill_version": "1.0.0",
        "execution": {"pass_rate": 0.5},
        "attribution": {"cause": "skill", "action": "patch_skill", "confidence": 0.9},
    }
    state["attribution"]["t_star"] = 0
    state["attribution"]["fault_chain"] = [0]
    state["attribution"]["steps"] = [
        {
            "index": 0,
            "source": "runtime",
            "label": "tool.call",
            "passed": False,
            "detail": "Tool call failed before final answer.",
        }
    ]

    monkeypatch.setattr(diagnose, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        diagnose,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=lambda request: state),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "diagnose",
            str(trace_path),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 10
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["kind"] == "diagnose"
    assert report["state"]["attribution"]["cause"] == "skill"
    assert report["markdown_path"] == str(md_out)
    markdown = md_out.read_text(encoding="utf-8")
    assert "Skill Doctor Report" in markdown
    assert "Step-Level Attribution" in markdown
    assert "tool.call" in markdown


def test_ingest_aime_reads_platform_trace_and_marks_source_adapter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import ingest

    trace_path = _write_json(
        tmp_path / "aime-trace.json",
        {
            "task": "diagnose AIME skill run",
            "skill_id": "aime-demo-skill",
            "skill_version": "1.0.0",
            "runtime_events": [
                {
                    "stage": "aime.noise_judge",
                    "status": "completed",
                    "message": "confidence=0.64 below threshold",
                }
            ],
            "tool_calls": [
                {"name": "fetch_metric", "status": "completed", "arguments": {"metric": "uv"}}
            ],
            "model_messages": [{"role": "assistant", "content": "rca_filter=true confidence=0.64"}],
            "trace_metadata": {"aime_session": "session-1"},
        },
    )
    json_out = tmp_path / "ingest.json"
    captured: dict[str, Any] = {}
    state = {
        "run_id": "lg-aime-ingest",
        "status": "failed",
        "skill_id": "aime-demo-skill",
        "skill_version": "1.0.0",
        "execution": {"executor": "aime-skill-trace", "pass_rate": 0.75},
        "attribution": {
            "cause": "skill",
            "action": "patch_skill",
            "t_star": 0,
            "fault_chain": [0],
            "steps": [
                {
                    "index": 0,
                    "source": "runtime",
                    "label": "aime.noise_judge",
                    "passed": False,
                    "detail": "confidence=0.64 below threshold",
                }
            ],
        },
    }

    def ingest_trace(request: dict[str, Any]) -> dict[str, Any]:
        captured["request"] = request
        return state

    monkeypatch.setattr(ingest, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        ingest,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=ingest_trace),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "ingest",
            str(trace_path),
            "--source",
            "aime",
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == EXIT_DIAGNOSIS_FAILED
    assert captured["request"]["trace_metadata"] == {
        "aime_session": "session-1",
        "source": "aime_cli_ingest",
        "skill_runtime": "aime",
    }
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["kind"] == "diagnose"
    assert report["ingest"] == {
        "source": "aime",
        "adapter": "aime_cli_ingest",
        "input_mode": "trace_file",
        "trace_path": str(trace_path),
    }
    assert report["state"]["execution"]["executor"] == "aime-skill-trace"


def test_ingest_aime_reads_raw_trace_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import ingest

    trace_dir = tmp_path / "aime-run-001"
    _write_json(
        trace_dir / "metadata.json",
        {
            "schema_version": "1.0",
            "task": "diagnose AIME run directory",
            "skill_id": "aime-directory-skill",
            "skill_version": "2.0.0",
            "aime_session": "session-dir",
            "aime_assistant": "assistant-dir",
            "trace_metadata": {"tenant": "demo"},
        },
    )
    (trace_dir / "skill_content.md").write_text("# AIME skill\nFollow confidence threshold.", encoding="utf-8")
    _write_jsonl(
        trace_dir / "runtime_events.jsonl",
        [
            {"stage": "aime.start", "status": "completed", "message": "started"},
            {"stage": "aime.noise_judge", "status": "completed", "message": "confidence=0.64"},
        ],
    )
    _write_json(
        trace_dir / "tool_calls.json",
        [{"name": "fetch_metric", "status": "completed", "arguments": {"metric": "uv"}}],
    )
    _write_jsonl(
        trace_dir / "model_messages.jsonl",
        [{"role": "assistant", "content": "rca_filter=true confidence=0.64"}],
    )
    _write_json(trace_dir / "business_result.json", {"rca_filter": True, "confidence": 0.64})
    json_out = tmp_path / "ingest-dir.json"
    captured: dict[str, Any] = {}
    state = {
        "run_id": "lg-aime-dir-ingest",
        "status": "failed",
        "skill_id": "aime-directory-skill",
        "skill_version": "2.0.0",
        "execution": {"executor": "aime-skill-trace", "pass_rate": 0.75},
        "attribution": {"cause": "skill", "action": "patch_skill", "confidence": 0.88},
    }

    def ingest_trace(request: dict[str, Any]) -> dict[str, Any]:
        captured["request"] = request
        return state

    monkeypatch.setattr(ingest, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        ingest,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=ingest_trace),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "ingest",
            "--source",
            "aime",
            "--trace-dir",
            str(trace_dir),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == EXIT_DIAGNOSIS_FAILED
    assert captured["request"]["skill_id"] == "aime-directory-skill"
    assert captured["request"]["skill_version"] == "2.0.0"
    assert captured["request"]["skill_content"] == "# AIME skill\nFollow confidence threshold."
    assert [item["stage"] for item in captured["request"]["runtime_events"]] == [
        "aime.start",
        "aime.noise_judge",
    ]
    assert captured["request"]["tool_calls"][0]["name"] == "fetch_metric"
    assert captured["request"]["model_messages"][0]["role"] == "assistant"
    assert captured["request"]["business_result"] == {"rca_filter": True, "confidence": 0.64}
    assert captured["request"]["trace_metadata"] == {
        "tenant": "demo",
        "aime_session": "session-dir",
        "aime_assistant": "assistant-dir",
        "source": "aime_cli_ingest",
        "skill_runtime": "aime",
    }
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["ingest"] == {
        "source": "aime",
        "adapter": "aime_cli_ingest",
        "input_mode": "trace_dir",
        "trace_dir": str(trace_dir),
        "trace_schema_version": "1.0",
    }


def test_ingest_generic_reads_standard_trace_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import ingest

    trace_dir = tmp_path / "generic-run-001"
    _write_json(
        trace_dir / "metadata.json",
        {
            "schema_version": "1.0",
            "task": "diagnose generic agent run",
            "skill_id": "generic-directory-skill",
            "skill_version": "3.0.0",
            "trace_metadata": {"host": "custom-agent"},
        },
    )
    (trace_dir / "skill_content.md").write_text("# Generic skill\nFollow the contract.", encoding="utf-8")
    _write_jsonl(
        trace_dir / "runtime_events.jsonl",
        [{"stage": "agent.done", "status": "completed", "message": "done"}],
    )
    _write_jsonl(
        trace_dir / "tool_calls.jsonl",
        [{"name": "lookup", "status": "completed", "arguments": {"id": 1}}],
    )
    _write_json(trace_dir / "business_result.json", {"verdict": "ok", "verdict_type": "pass"})
    json_out = tmp_path / "ingest-generic.json"
    captured: dict[str, Any] = {}
    state = {
        "run_id": "lg-generic-dir-ingest",
        "status": "passed",
        "skill_id": "generic-directory-skill",
        "skill_version": "3.0.0",
        "execution": {"executor": "generic-skill-trace", "pass_rate": 1.0},
        "attribution": {"cause": "none", "action": "none"},
    }

    def ingest_trace(request: dict[str, Any]) -> dict[str, Any]:
        captured["request"] = request
        return state

    monkeypatch.setattr(ingest, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        ingest,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=ingest_trace),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "ingest",
            "--source",
            "generic",
            "--trace-dir",
            str(trace_dir),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == EXIT_OK
    assert captured["request"]["skill_id"] == "generic-directory-skill"
    assert captured["request"]["skill_content"] == "# Generic skill\nFollow the contract."
    assert captured["request"]["runtime_events"][0]["stage"] == "agent.done"
    assert captured["request"]["tool_calls"][0]["name"] == "lookup"
    assert captured["request"]["business_result"] == {"verdict": "ok", "verdict_type": "pass"}
    assert captured["request"]["trace_metadata"] == {
        "host": "custom-agent",
        "source": "generic_cli_ingest",
        "skill_runtime": "generic",
    }
    assert "schema_version" not in captured["request"]
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["ingest"] == {
        "source": "generic",
        "adapter": "generic_cli_ingest",
        "input_mode": "trace_dir",
        "trace_dir": str(trace_dir),
        "trace_schema_version": "1.0",
    }


def test_evaluate_returns_quality_gate_code_when_score_is_low(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import evaluate

    trace_path = _write_json(tmp_path / "low-quality-trace.json", {"skill_id": "demo"})
    json_out = tmp_path / "evaluate.json"
    state = {
        "run_id": "lg-test-low-quality",
        "status": "passed",
        "skill_id": "demo",
        "skill_version": "1.0.0",
        "execution": {
            "passed": True,
            "pass_rate": 0.4,
            "duration_ms": 1_000,
            "assertions": [
                {"id": "a", "passed": False},
                {"id": "b", "passed": True},
            ],
        },
    }

    monkeypatch.setattr(evaluate, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        evaluate,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=lambda request: state),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "evaluate",
            str(trace_path),
            "--min-score",
            "0.95",
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == 20
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["kind"] == "evaluate"
    assert report["quality"]["overall_score"] < 0.95
    assert "output_quality" in report["quality"]["dimensions"]
    assert report["quality_gate"]["passed"] is False
    assert report["quality_gate"]["failures"][0]["name"] == "overall_score"
    assert "score_breakdown" in report["quality"]
    assert report["quality"]["reasons"]["output_quality"]
    assert report["quality"]["evidence_refs"] == [
        "assertion:a:fail",
        "assertion:b:pass",
    ]


def test_evaluate_can_gate_on_dimension_threshold_and_render_reasons(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import evaluate

    trace_path = _write_json(tmp_path / "weak-evidence-trace.json", {"skill_id": "demo"})
    json_out = tmp_path / "evaluate.json"
    md_out = tmp_path / "evaluate.md"
    state = {
        "run_id": "lg-test-weak-evidence",
        "status": "passed",
        "skill_id": "demo",
        "skill_version": "1.0.0",
        "execution": {
            "passed": True,
            "pass_rate": 1.0,
            "duration_ms": 1_000,
            "assertions": [{"id": "runtime-events-clean", "passed": True}],
            "runtime_events": [
                {"stage": "skill.execute", "status": "completed"},
            ],
        },
    }

    monkeypatch.setattr(evaluate, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        evaluate,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=lambda request: state),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "evaluate",
            str(trace_path),
            "--min-score",
            "0.50",
            "--min-evidence-support",
            "0.90",
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 20
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["dimension_thresholds"] == {"evidence_support": 0.9}
    assert report["quality_gate"]["failures"] == [
        {
            "name": "evidence_support",
            "expected": 0.9,
            "actual": report["quality"]["dimensions"]["evidence_support"],
            "message": f"evidence_support {report['quality']['dimensions']['evidence_support']:.4f} is below required 0.9000.",
        }
    ]
    markdown = md_out.read_text(encoding="utf-8")
    assert "Score Breakdown" in markdown
    assert "Dimension Reasons" in markdown
    assert "Quality Gate" in markdown


def test_evaluate_scores_puck_rule_rca_domain_quality(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import evaluate

    trace_path = _write_json(tmp_path / "puck-rule-rca-trace.json", {"skill_id": "puck-rule-rca"})
    json_out = tmp_path / "evaluate-domain.json"
    md_out = tmp_path / "evaluate-domain.md"
    state = {
        "run_id": "lg-test-puck-rca",
        "status": "passed",
        "skill_id": "puck-rule-rca",
        "skill_version": "1.0.0",
        "business_result": {
            "verdict": "当前异常建议降噪：历史同期波动一致，且影响面有限。",
            "verdict_type": "pass",
            "confidence": 0.82,
            "details": [
                {
                    "name": "history_pattern",
                    "status": "pass",
                    "reason": "历史同期存在相同波动，工具证据支持降噪。",
                }
            ],
            "extra": {
                "raw_business_result": {
                    "rca_filter": True,
                    "rca_content": "当前异常建议降噪：历史同期波动一致，且影响面有限。",
                    "confidence": 0.82,
                    "rca_detail": [{"group_detail_name": "history_pattern"}],
                }
            },
        },
        "execution": {
            "passed": True,
            "pass_rate": 1.0,
            "duration_ms": 1_000,
            "assertions": [
                {"id": "runtime-events-clean", "passed": True},
                {"id": "tool-calls-healthy", "passed": True},
            ],
            "runtime_events": [
                {
                    "stage": "agent.analyze.tool_calls",
                    "status": "completed",
                    "metadata": {"total": 2, "failed": 0},
                }
            ],
        },
        "attribution": {"cause": "none", "action": "none", "evidence_refs": ["tool:history"]},
    }

    monkeypatch.setattr(evaluate, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        evaluate,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=lambda request: state),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "evaluate",
            str(trace_path),
            "--min-score",
            "0.75",
            "--min-domain-quality",
            "0.90",
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == EXIT_OK
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["dimension_thresholds"] == {"domain_quality": 0.9}
    quality = report["quality"]
    assert quality["dimensions"]["domain_quality"] == 1.0
    assert quality["domain_quality"]["passed"] is True
    assert [item["name"] for item in quality["domain_quality"]["checks"]] == [
        "has_clear_verdict",
        "has_valid_confidence",
        "has_reasoning",
        "has_detail_evidence",
        "contract_shape",
        "trace_evidence_available",
        "confidence_evidence_match",
    ]
    assert report["quality_gate"] == {"passed": True, "failures": []}
    markdown = md_out.read_text(encoding="utf-8")
    assert "Domain Quality" in markdown
    assert "confidence_evidence_match" in markdown


def test_evaluate_fails_domain_gate_for_weak_puck_rule_rca_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import evaluate

    trace_path = _write_json(tmp_path / "weak-puck-rule-rca-trace.json", {"skill_id": "puck-rule-rca"})
    json_out = tmp_path / "evaluate-weak-domain.json"
    state = {
        "run_id": "lg-test-weak-puck-rca",
        "status": "passed",
        "skill_id": "puck-rule-rca",
        "skill_version": "1.0.0",
        "business_result": {
            "verdict": "{}",
            "verdict_type": "warning",
            "confidence": 0.95,
            "details": [],
            "extra": {"raw_business_result": {"rca_filter": True, "confidence": 0.95}},
        },
        "execution": {
            "passed": True,
            "pass_rate": 1.0,
            "duration_ms": 1_000,
            "assertions": [],
            "runtime_events": [],
        },
        "attribution": {"cause": "none", "action": "none"},
    }

    monkeypatch.setattr(evaluate, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        evaluate,
        "new_run_service",
        lambda project_root: SimpleNamespace(ingest_trace=lambda request: state),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "evaluate",
            str(trace_path),
            "--min-score",
            "0.50",
            "--min-domain-quality",
            "0.75",
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == EXIT_QUALITY_GATE_FAILED
    report = json.loads(json_out.read_text(encoding="utf-8"))
    domain = report["quality"]["domain_quality"]
    assert domain["passed"] is False
    assert domain["score"] < 0.75
    assert report["quality_gate"]["failures"] == [
        {
            "name": "domain_quality",
            "expected": 0.75,
            "actual": report["quality"]["dimensions"]["domain_quality"],
            "message": f"domain_quality {report['quality']['dimensions']['domain_quality']:.4f} is below required 0.7500.",
        }
    ]
    assert any(
        "puck-rule-rca 领域质量不足" in finding
        for finding in report["quality"]["findings"]
    )


def test_bench_loads_jsonl_skips_comments_and_returns_failure_code(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import bench

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "\n".join(
            [
                "# comment",
                json.dumps(
                    {
                        "case_id": "case-1",
                        "name": "case 1",
                        "trace": {"skill_id": "demo", "execution": {"passed": False}},
                        "expectation": {"status": "failed"},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    json_out = tmp_path / "bench.json"
    md_out = tmp_path / "bench.md"
    captured: dict[str, Any] = {}

    def run_suite(request: Any) -> dict[str, Any]:
        captured["case_count"] = len(request.cases)
        return {
            "schema_version": "1.0",
            "suite_id": request.suite_id,
            "name": request.name,
            "status": "failed",
            "summary": {"total": 1, "passed": 0, "failed": 1, "pass_rate": 0.0},
            "cases": [{"case_id": "case-1", "passed": False, "category": "skill"}],
        }

    monkeypatch.setattr(bench, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        bench,
        "new_run_service",
        lambda project_root: SimpleNamespace(run_diagnostic_suite=run_suite),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "bench",
            str(cases_path),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 30
    assert captured["case_count"] == 1
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["kind"] == "bench"
    assert report["summary"]["pass_rate"] == 0.0
    assert "case-1" in md_out.read_text(encoding="utf-8")


def test_bench_filters_tags_and_skips_flaky_cases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import bench

    cases_path = tmp_path / "cases.jsonl"
    payloads = [
        {
            "schema_version": "1.1",
            "case_id": "release-case",
            "name": "release case",
            "tags": ["release", "p0"],
            "trace": {"skill_id": "demo", "execution": {"passed": True}},
            "expectation": {"status": "passed"},
        },
        {
            "case_id": "dev-case",
            "name": "dev case",
            "tags": ["dev"],
            "trace": {"skill_id": "demo", "execution": {"passed": True}},
            "expectation": {"status": "passed"},
        },
        {
            "case_id": "flaky-case",
            "name": "flaky case",
            "tags": ["release"],
            "flaky": True,
            "trace": {"skill_id": "demo", "execution": {"passed": True}},
            "expectation": {"status": "passed"},
        },
    ]
    cases_path.write_text("\n".join(json.dumps(item) for item in payloads), encoding="utf-8")
    json_out = tmp_path / "bench.json"
    md_out = tmp_path / "bench.md"
    captured: dict[str, Any] = {}

    def run_suite(request: Any) -> dict[str, Any]:
        captured["case_ids"] = [case.case_id for case in request.cases]
        return {
            "schema_version": "1.0",
            "suite_id": request.suite_id,
            "name": request.name,
            "status": "passed",
            "summary": {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0},
            "cases": [
                {
                    "case_id": "release-case",
                    "name": "release case",
                    "passed": True,
                    "category": "healthy",
                    "repairable": False,
                    "agent_source": "none",
                }
            ],
        }

    monkeypatch.setattr(bench, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        bench,
        "new_run_service",
        lambda project_root: SimpleNamespace(run_diagnostic_suite=run_suite),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "bench",
            str(cases_path),
            "--include-tag",
            "release",
            "--exclude-tag",
            "dev",
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert captured["case_ids"] == ["release-case"]
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["summary"]["skipped"] == 2
    assert report["cases"][0]["tags"] == ["p0", "release"]
    assert report["cases"][0]["schema_version"] == "1.1"
    assert report["case_set"]["skipped"] == [
        {
            "case_id": "dev-case",
            "schema_version": "1.0",
            "tags": ["dev"],
            "flaky": False,
            "regression_risk": None,
            "reason": "include_tag_filter",
        },
        {
            "case_id": "flaky-case",
            "schema_version": "1.0",
            "tags": ["release"],
            "flaky": True,
            "regression_risk": None,
            "reason": "flaky_excluded",
        },
    ]
    assert "Skipped Cases" in md_out.read_text(encoding="utf-8")


def test_bench_fail_fast_stops_after_first_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from skilldoctor_cli.commands import bench

    cases_path = tmp_path / "cases.jsonl"
    payloads = [
        {
            "case_id": "first-case",
            "name": "first case",
            "tags": "release",
            "trace": {"skill_id": "demo", "execution": {"passed": False}},
            "expectation": {"status": "failed"},
        },
        {
            "case_id": "second-case",
            "name": "second case",
            "tags": ["release"],
            "trace": {"skill_id": "demo", "execution": {"passed": True}},
            "expectation": {"status": "passed"},
        },
    ]
    cases_path.write_text("\n".join(json.dumps(item) for item in payloads), encoding="utf-8")
    json_out = tmp_path / "bench.json"
    calls: list[str] = []

    def run_case(case: Any) -> dict[str, Any]:
        calls.append(case.case_id)
        return {
            "case_id": case.case_id,
            "name": case.name,
            "source": "custom",
            "passed": False,
            "category": "skill",
            "repairable": False,
            "run_id": f"lg-{case.case_id}",
            "status": "failed",
            "stop_reason": "synthetic",
            "skill_id": "demo",
            "agent_source": "none",
            "attribution": {"cause": "skill"},
            "verification": {},
            "checks": [],
        }

    monkeypatch.setattr(bench, "backend_modules", _fake_backend_modules)
    monkeypatch.setattr(
        bench,
        "new_run_service",
        lambda project_root: SimpleNamespace(_run_diagnostic_case=run_case),
    )

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "bench",
            str(cases_path),
            "--fail-fast",
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == 30
    assert calls == ["first-case"]
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["fail_fast"] == {"enabled": True, "stopped_early": True}
    assert report["summary"]["total"] == 1
    assert report["cases"][0]["tags"] == ["release"]


def test_bench_rejects_malformed_case_metadata(
    tmp_path: Path,
    capsys,
) -> None:
    cases_path = tmp_path / "cases.jsonl"
    payloads = [
        {
            "case_id": "",
            "name": "missing case id",
            "tags": ["release", 1],
            "flaky": "false",
            "regression_risk": "critical",
            "trace": {"skill_id": "demo", "execution": {"passed": True}},
            "expectation": {"status": "passed"},
        }
    ]
    cases_path.write_text("\n".join(json.dumps(item) for item in payloads), encoding="utf-8")

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "bench",
            str(cases_path),
            "--quiet",
        ]
    )

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "invalid bench case set" in error
    assert "case[1].case_id must be a non-empty string" in error
    assert "case[1].tags must be a string or a list of strings" in error
    assert "case[1].flaky must be a boolean" in error
    assert "case[1].regression_risk must be one of: high, low, medium" in error


def test_compare_detects_regressed_case_and_writes_markdown(tmp_path: Path) -> None:
    old_report = _write_json(
        tmp_path / "old.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [
                {"case_id": "case-1", "passed": True},
                {"case_id": "case-fixed", "passed": False},
                {"case_id": "case-persistent", "passed": False},
            ],
        },
    )
    new_report = _write_json(
        tmp_path / "new.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 0.0},
            "cases": [
                {"case_id": "case-1", "passed": False, "category": "skill"},
                {"case_id": "case-fixed", "passed": True, "category": "healthy"},
                {"case_id": "case-persistent", "passed": False, "category": "skill"},
            ],
        },
    )
    json_out = tmp_path / "compare.json"
    md_out = tmp_path / "compare.md"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            str(old_report),
            str(new_report),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 40
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["decision"] == "REJECT"
    assert report["delta"]["regressed_cases"] == ["case-1"]
    assert report["case_diff"]["fixed_cases"] == ["case-fixed"]
    assert report["case_diff"]["persistent_failures"] == ["case-persistent"]
    markdown = md_out.read_text(encoding="utf-8")
    assert "Case Diff" in markdown
    assert "Regressed Cases" in markdown
    assert "Fixed Cases" in markdown
    assert "Persistent Failures" in markdown


def test_compare_rejects_new_skill_failure_and_quality_cost_safety_regression(
    tmp_path: Path,
) -> None:
    old_report = _write_json(
        tmp_path / "old-evaluate.json",
        {
            "kind": "evaluate",
            "quality": {
                "overall_score": 0.95,
                "dimensions": {"safety_boundary": 0.95, "evidence_support": 0.9},
            },
            "state": {
                "run_id": "old-run",
                "case_id": "skill-a",
                "status": "passed",
                "skill_id": "skill-a",
                "execution": {
                    "passed": True,
                    "pass_rate": 1.0,
                    "duration_ms": 1000,
                    "usage": {"input_tokens": 100, "output_tokens": 100},
                },
            },
        },
    )
    new_report = _write_json(
        tmp_path / "new-evaluate.json",
        {
            "kind": "evaluate",
            "quality": {
                "overall_score": 0.70,
                "dimensions": {"safety_boundary": 0.60, "evidence_support": 0.7},
            },
            "state": {
                "run_id": "new-run",
                "case_id": "skill-a",
                "status": "failed",
                "skill_id": "skill-a",
                "attribution": {"cause": "skill"},
                "execution": {
                    "passed": False,
                    "pass_rate": 0.0,
                    "duration_ms": 5000,
                    "usage": {"input_tokens": 700, "output_tokens": 500},
                },
            },
        },
    )
    json_out = tmp_path / "compare.json"
    md_out = tmp_path / "compare.md"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            str(old_report),
            str(new_report),
            "--max-quality-drop",
            "0.1",
            "--max-cost-increase-rate",
            "1.0",
            "--max-safety-drop",
            "0.1",
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 40
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["delta"]["quality_delta"] == -0.25
    assert report["delta"]["safety_boundary_delta"] == -0.35
    assert report["delta"]["token_increase_rate"] == 5.0
    assert report["delta"]["duration_increase_rate"] == 4.0
    assert report["case_diff"]["regressed_cases"] == ["skill-a"]
    assert report["gate_summary"] == {
        "passed": False,
        "failure_count": 6,
        "regressed_case_count": 1,
        "new_failure_count": 0,
        "new_skill_failure_count": 0,
        "quality_delta": -0.25,
        "safety_boundary_delta": -0.35,
        "token_increase_rate": 5.0,
        "duration_increase_rate": 4.0,
    }
    assert [item["name"] for item in report["gate_failures"]] == [
        "pass_rate_delta",
        "regressed_cases",
        "quality_delta",
        "token_increase_rate",
        "duration_increase_rate",
        "safety_boundary_delta",
    ]
    assert report["blocking_regressions"] == {
        "regressed_cases": ["skill-a"],
        "new_skill_failures": [],
    }
    assert any("quality_delta" in reason for reason in report["reasons"])
    assert any("token_increase_rate" in reason for reason in report["reasons"])
    assert any("safety_boundary_delta" in reason for reason in report["reasons"])
    markdown = md_out.read_text(encoding="utf-8")
    assert "CI Gate Summary" in markdown
    assert "CI Gate Failures" in markdown
    assert "Quality Diff" in markdown
    assert "Cost Diff" in markdown


def test_compare_accepts_explicit_baseline_path(tmp_path: Path) -> None:
    baseline_report = _write_json(
        tmp_path / "baseline.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    new_report = _write_json(
        tmp_path / "new.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    json_out = tmp_path / "compare.json"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            "--baseline",
            str(baseline_report),
            str(new_report),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["decision"] == "ADOPT"
    assert report["old"]["path"] == str(baseline_report)
    assert report["new"]["path"] == str(new_report)
    assert report["baseline"] == {
        "enabled": True,
        "path": str(baseline_report),
        "source": "explicit",
    }
    assert report["gate_summary"]["passed"] is True


def test_compare_accepts_named_baseline_path(tmp_path: Path) -> None:
    baseline_report = _write_json(
        tmp_path / ".skilldoctor" / "baselines" / "main.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    new_report = _write_json(
        tmp_path / "new.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    json_out = tmp_path / "compare.json"
    md_out = tmp_path / "compare.md"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            "--baseline-name",
            "main",
            str(new_report),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["old"]["path"] == str(baseline_report)
    assert report["new"]["path"] == str(new_report)
    assert report["baseline"] == {
        "enabled": True,
        "path": str(baseline_report),
        "source": "named",
        "name": "main",
    }
    markdown = md_out.read_text(encoding="utf-8")
    assert "## Baseline" in markdown
    assert "Source: `named`" in markdown


def test_compare_auto_discovers_main_baseline(tmp_path: Path) -> None:
    baseline_report = _write_json(
        tmp_path / ".skilldoctor" / "baselines" / "main.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    new_report = _write_json(
        tmp_path / "new.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    json_out = tmp_path / "compare.json"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            str(new_report),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    report = json.loads(json_out.read_text(encoding="utf-8"))
    assert report["old"]["path"] == str(baseline_report)
    assert report["new"]["path"] == str(new_report)
    assert report["baseline"] == {
        "enabled": True,
        "path": str(baseline_report),
        "source": "auto",
        "name": "main",
    }


def test_baseline_save_list_and_compare_auto_discovery(tmp_path: Path, capsys) -> None:
    source_report = _write_json(
        tmp_path / "bench.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    new_report = _write_json(
        tmp_path / "new.json",
        {
            "kind": "bench",
            "summary": {"pass_rate": 1.0},
            "cases": [{"case_id": "case-1", "passed": True}],
        },
    )
    json_out = tmp_path / "compare.json"

    save_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "baseline",
            "save",
            str(source_report),
            "--name",
            "main",
        ]
    )
    assert save_code == 0
    saved_path = tmp_path / ".skilldoctor" / "baselines" / "main.json"
    assert json.loads(saved_path.read_text(encoding="utf-8")) == json.loads(source_report.read_text(encoding="utf-8"))
    save_output = capsys.readouterr().out
    assert "baseline saved:" in save_output
    assert "source kind: bench" in save_output

    duplicate_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "baseline",
            "save",
            str(source_report),
            "--name",
            "main",
        ]
    )
    assert duplicate_code == 1
    assert "baseline already exists" in capsys.readouterr().err

    list_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "baseline",
            "list",
        ]
    )
    assert list_code == 0
    list_output = capsys.readouterr().out
    assert "Baselines:" in list_output
    assert "main:" in list_output
    assert "kind=bench" in list_output
    assert "pass_rate=1.0" in list_output

    compare_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "compare",
            str(new_report),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )
    assert compare_code == 0
    compare_report = json.loads(json_out.read_text(encoding="utf-8"))
    assert compare_report["baseline"] == {
        "enabled": True,
        "path": str(saved_path),
        "source": "auto",
        "name": "main",
    }


def test_repair_preview_generates_auditable_skill_patch_plan(tmp_path: Path) -> None:
    diagnose_report = _write_json(
        tmp_path / "diagnose.json",
        {
            "kind": "diagnose",
            "state": {
                "run_id": "lg-failed",
                "status": "failed",
                "skill_id": "release-checklist",
                "skill_version": "1.0.0",
                "attribution": {
                    "cause": "skill",
                    "action": "patch_skill",
                    "fault_type": "skill_wrong",
                    "t_star": 1,
                    "fault_chain": [1],
                    "improvement_principle": "Require rollback validation before finalizing release checklist output.",
                    "explanation": "Rollback validation was required but missing.",
                    "evidence_refs": ["rollback-gate-present"],
                    "steps": [
                        {
                            "index": 1,
                            "source": "skill",
                            "label": "rollback-gate-present",
                            "passed": False,
                            "detail": "Rollback validation was required but not included.",
                        }
                    ],
                },
            },
        },
    )
    json_out = tmp_path / "preview.json"
    md_out = tmp_path / "preview.md"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "repair-preview",
            str(diagnose_report),
            "--json-out",
            str(json_out),
            "--md-out",
            str(md_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    preview = json.loads(json_out.read_text(encoding="utf-8"))
    assert preview["kind"] == "repair_preview"
    assert preview["repairable"] is True
    assert preview["target"] == {"skill_id": "release-checklist", "skill_version": "1.0.0"}
    assert preview["diagnosis"]["failed_step"]["label"] == "rollback-gate-present"
    assert preview["proposal"]["mode"] == "revise"
    assert preview["proposal"]["suggested_change"] == "Require rollback validation before finalizing release checklist output."
    assert preview["mutation"] == {
        "applies_changes": False,
        "apply_policy": "manual_review_required",
        "message": "repair-preview is read-only; it does not modify skills or project files.",
        "allowed_next_actions": [
            "review_preview",
            "run_required_validation",
            "create_manual_skill_patch",
        ],
    }
    assert preview["risk"]["level"] == "medium"
    assert preview["validation"]["required"] is True
    markdown = md_out.read_text(encoding="utf-8")
    assert "Repair Preview" in markdown
    assert "rollback-gate-present" in markdown
    assert "Mutation Policy" in markdown
    assert "manual_review_required" in markdown
    assert "Required Validation" in markdown


def test_repair_preview_does_not_recommend_non_skill_mutation(tmp_path: Path) -> None:
    diagnose_report = _write_json(
        tmp_path / "platform.json",
        {
            "kind": "diagnose",
            "state": {
                "run_id": "lg-platform",
                "status": "failed",
                "skill_id": "demo",
                "skill_version": "1.0.0",
                "attribution": {
                    "cause": "platform",
                    "action": "split_non_skill",
                    "fault_type": "reasoning_wrong",
                    "explanation": "The execution failed at the platform boundary.",
                },
            },
        },
    )
    json_out = tmp_path / "preview.json"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "repair-preview",
            str(diagnose_report),
            "--json-out",
            str(json_out),
            "--quiet",
        ]
    )

    assert exit_code == 0
    preview = json.loads(json_out.read_text(encoding="utf-8"))
    assert preview["repairable"] is False
    assert preview["proposal"]["mode"] == "none"
    assert preview["mutation"] == {
        "applies_changes": False,
        "apply_policy": "manual_review_required",
        "message": "repair-preview is read-only; it does not modify skills or project files.",
        "allowed_next_actions": ["route_to_non_skill_owner"],
    }
    assert preview["risk"] == {
        "level": "low",
        "reasons": ["No skill mutation is recommended for this report."],
    }
    assert preview["validation"] == {"required": False, "commands": []}


def test_report_renders_existing_json_report(tmp_path: Path, capsys) -> None:
    json_report = _write_json(
        tmp_path / "diagnose.json",
        {
            "kind": "diagnose",
            "state": {
                "run_id": "lg-test",
                "status": "passed",
                "skill_id": "demo",
                "skill_version": "1.0.0",
                "execution": {"pass_rate": 1.0},
            },
        },
    )
    md_out = tmp_path / "rendered.md"

    exit_code = cli_main.main(
        [
            "--project-root",
            str(tmp_path),
            "report",
            str(json_report),
            "--md-out",
            str(md_out),
        ]
    )

    assert exit_code == 0
    assert "markdown:" in capsys.readouterr().out
    assert "lg-test" in md_out.read_text(encoding="utf-8")
