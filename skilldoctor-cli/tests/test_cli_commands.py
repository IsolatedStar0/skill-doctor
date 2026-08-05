from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from skilldoctor_cli import main as cli_main


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
    assert "Skill Doctor Report" in md_out.read_text(encoding="utf-8")


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


def test_compare_detects_regressed_case_and_writes_markdown(tmp_path: Path) -> None:
    old_report = _write_json(
        tmp_path / "old.json",
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
            "summary": {"pass_rate": 0.0},
            "cases": [{"case_id": "case-1", "passed": False}],
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
    assert "Regressed Cases" in md_out.read_text(encoding="utf-8")


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
