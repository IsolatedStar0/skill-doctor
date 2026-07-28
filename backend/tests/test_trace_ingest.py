import http.client
import json
import hashlib
import threading
from pathlib import Path

from backend.skilldoctor.http_server import make_handler
from backend.skilldoctor.models import (
    CandidateValidationRequest,
    RepairVerificationRequest,
    TraceIngestRequest,
)
from backend.skilldoctor.service import RunService
from backend.skilldoctor.storage import (
    FileStorageBackend,
    SQLiteStorageBackend,
    build_storage_backend,
)
from http.server import ThreadingHTTPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _payload() -> str:
    return json.dumps(
        {
            "task": "Diagnose uploaded trace.",
            "skill_id": "trace-skill",
            "skill_version": "1.0.0",
            "skill_content": "Follow the full procedure.",
            "repair_enabled": False,
            "execution": {
                "executor": "aime-skill-trace",
                "condition": "with_skill",
                "passed": False,
                "pass_rate": 0.5,
                "duration_ms": 1234,
                "summary": "Uploaded execution missed a skill-owned check.",
                "assertions": [
                    {
                        "id": "complete-procedure",
                        "source": "skill",
                        "passed": False,
                        "detail": "The skill skipped a required step.",
                    }
                ],
                "runtime_events": [
                    {
                        "stage": "aime.trace",
                        "status": "completed",
                        "message": "Trace imported from Aime.",
                        "usage": {
                            "inputTokens": 120,
                            "outputTokens": 30,
                            "cachedInputTokens": 40,
                            "reasoningTokens": 8,
                        },
                        "metadata": {"source": "aime"},
                    }
                ],
            },
        }
    )


def _candidate_payload(passed: bool = True) -> str:
    pass_rate = 1.0 if passed else 0.5
    return json.dumps(
        {
            "task": "Diagnose uploaded trace after repair.",
            "skill_id": "trace-skill",
            "skill_version": "1.0.1",
            "skill_content": "Follow the full procedure and validate the required step.",
            "repair_enabled": False,
            "execution": {
                "executor": "aime-skill-trace",
                "condition": "with_skill",
                "passed": passed,
                "pass_rate": pass_rate,
                "duration_ms": 1000,
                "summary": "Candidate execution completed after the proposed repair.",
                "assertions": [
                    {
                        "id": "complete-procedure",
                        "source": "skill",
                        "passed": passed,
                        "detail": "The skill completed the required step." if passed else "The skill still skipped a required step.",
                    }
                ],
                "runtime_events": [
                    {
                        "stage": "aime.trace",
                        "status": "completed",
                        "message": "Candidate trace imported from Aime.",
                    }
                ],
            },
        }
    )


def test_trace_ingest_requires_api_key_when_enabled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_INGEST_API_KEY", "secret-token")
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            "/traces",
            _payload(),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()

        assert response.status == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trace_ingest_runs_attribution_pipeline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_INGEST_API_KEY", "secret-token")
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.adaptor_llm_client = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            "/traces",
            _payload(),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )
        response = connection.getresponse()
        state = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert state["run_id"].startswith("lg-")
        assert state["executor"] == "trace-ingest"
        assert state["execution"]["executor"] == "aime-skill-trace"
        assert state["attribution"]["cause"] == "skill"
        assert state["attribution"]["agent_source"] == "rule-based"
        assert state["attribution"]["agent_conclusion"] == ""
        assert state["status"] == "failed"
        assert service.get(state["run_id"])["run_id"] == state["run_id"]
        stages = [event["stage"] for event in state["events"]]
        assert "agent.analyze" in stages
        assert "agent.analyze.summarize" in stages
        event = next(item for item in state["events"] if item["stage"] == "aime.trace")
        assert event["usage"] == {
            "input_tokens": 120,
            "output_tokens": 30,
            "cached_input_tokens": 40,
            "reasoning_tokens": 8,
        }
        assert state["execution"]["usage"]["input_tokens"] == 120
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trace_ingest_passed_aime_trace_keeps_fast_path(tmp_path: Path) -> None:
    calls: list[str] = []
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.adaptor_llm_client = lambda prompt: calls.append(prompt) or "{}"

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(
            {
                "task": "Summarize healthy Aime trace.",
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
                "trace_metadata": {"confidence": 0.9},
            }
        )
    )

    assert state["status"] == "passed"
    assert state["stop_reason"] == "initial_execution_passed"
    assert "attribution" not in state
    assert calls == []


def test_trace_ingest_passed_aime_trace_with_prior_execution_skips_llm(
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.adaptor_llm_client = lambda prompt: calls.append(prompt) or json.dumps(
        {
            "fault_type": "skill_wrong",
            "t_star": 0,
            "fault_chain": [0],
            "improvement_principle": "Should not be used for a passed trace.",
            "reason": "Healthy trace should not be attributed.",
        }
    )

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(
            {
                "task": "Summarize healthy Aime trace.",
                "skill_id": "healthy-skill",
                "skill_version": "1.0.0",
                "skill_content": "# healthy-skill\nFollow the safe path.",
                "repair_enabled": False,
                "execution": {
                    "executor": "aime-skill-trace",
                    "condition": "with_skill",
                    "passed": True,
                    "pass_rate": 1.0,
                    "duration_ms": 100,
                    "summary": "Aime skill completed successfully.",
                    "assertions": [
                        {
                            "id": "skill-result-valid",
                            "source": "skill",
                            "passed": True,
                            "detail": "The skill result matches the contract.",
                        }
                    ],
                    "runtime_events": [
                        {
                            "stage": "aime.done",
                            "status": "completed",
                            "message": "Aime skill finished successfully.",
                        }
                    ],
                },
            }
        )
    )

    assert state["status"] == "passed"
    assert state["stop_reason"] == "initial_execution_passed"
    assert "attribution" not in state
    assert calls == []


def test_trace_ingest_failed_aime_trace_prompt_includes_skill_content(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    def fake_llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(
            {
                "fault_type": "skill_wrong",
                "t_star": 0,
                "fault_chain": [0],
                "improvement_principle": "Use the injected skill rule to fix the failed branch.",
                "reason": "The supplied skill content contradicts the observed behavior.",
            }
        )

    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.adaptor_llm_client = fake_llm

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(
            {
                "task": "Diagnose injected skill content.",
                "skill_id": "trace-skill",
                "skill_version": "1.0.0",
                "skill_content": "# trace-skill\nMUST_USE_CANARY_RULE_FOR_DIAGNOSIS",
                "repair_enabled": False,
                "execution": {
                    "executor": "aime-skill-trace",
                    "condition": "with_skill",
                    "passed": False,
                    "pass_rate": 0.0,
                    "duration_ms": 100,
                    "summary": "Skill-owned rule failed.",
                    "assertions": [
                        {
                            "id": "canary-rule",
                            "source": "skill",
                            "passed": False,
                            "detail": "The canary skill rule was not followed.",
                        }
                    ],
                },
            }
        )
    )

    assert state["status"] == "failed"
    assert state["attribution"]["cause"] == "skill"
    assert state["attribution"]["fault_type"] == "skill_wrong"
    assert prompts
    assert "MUST_USE_CANARY_RULE_FOR_DIAGNOSIS" in prompts[0]


def test_trace_ingest_normalizes_skill_wrong_cause_from_llm(
    tmp_path: Path,
) -> None:
    def fake_llm(_: str) -> str:
        return json.dumps(
            {
                "fault_type": "skill_wrong",
                "t_star": 0,
                "fault_chain": [0],
                "improvement_principle": "Revise the skill-owned failing check.",
                "reason": "The failing evidence is skill-owned.",
            }
        )

    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.adaptor_llm_client = fake_llm

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(
            {
                "task": "Diagnose skill-owned mismatch.",
                "skill_id": "trace-skill",
                "skill_version": "1.0.0",
                "skill_content": "# trace-skill\nFollow all skill checks.",
                "repair_enabled": False,
                "execution": {
                    "executor": "aime-skill-trace",
                    "condition": "with_skill",
                    "passed": False,
                    "pass_rate": 0.0,
                    "duration_ms": 100,
                    "summary": "A system assertion failed but LLM maps it to skill logic.",
                    "assertions": [
                        {
                            "id": "system-observed-mismatch",
                            "source": "system",
                            "passed": False,
                            "detail": "Observed output violates the skill semantics.",
                        }
                    ],
                },
            }
        )
    )

    assert state["attribution"]["fault_type"] == "skill_wrong"
    assert state["attribution"]["cause"] == "skill"
    assert state["attribution"]["action"] == "patch_skill"


def test_default_diagnostic_suite_covers_core_trace_routes(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None

    report = service.run_diagnostic_suite()

    assert report["status"] == "passed"
    assert report["summary"] == {
        "total": 4,
        "passed": 4,
        "failed": 0,
        "pass_rate": 1.0,
        "repairable": 1,
        "non_skill": 1,
        "llm_authored": 0,
        "saved_cases": 0,
    }
    by_id = {item["case_id"]: item for item in report["cases"]}
    assert by_id["healthy-aime-fast-path"]["status"] == "passed"
    assert by_id["healthy-aime-fast-path"]["repairable"] is False
    assert by_id["skill-content-gap"]["attribution"]["cause"] == "skill"
    assert by_id["skill-content-gap"]["repairable"] is True
    assert by_id["loader-missing-skill"]["attribution"]["cause"] == "loader"
    assert by_id["platform-network-failure"]["attribution"]["cause"] == "platform"
    assert "# Core Trace Regression Suite" in report["markdown"]


def test_diagnostic_suite_http_endpoint(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request("GET", "/diagnostics/default")
        response = connection.getresponse()
        report = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert report["status"] == "passed"
        assert report["summary"]["total"] == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_save_run_as_diagnostic_case_and_loads_in_suite(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    saved = service.save_diagnostic_case_from_run(state["run_id"])

    assert saved["status"] == "saved"
    assert saved["case"]["source"] == "saved_run"
    assert saved["case"]["expectation"]["cause"] == "skill"
    assert (service.diagnostic_case_directory / f"{saved['case']['case_id']}.json").is_file()

    cases = service.load_saved_diagnostic_cases()
    assert len(cases) == 1
    assert cases[0].source == "saved_run"

    report = service.run_diagnostic_suite()
    assert report["summary"]["total"] == 5
    assert report["summary"]["saved_cases"] == 1
    assert any(item["source"] == "saved_run" for item in report["cases"])


def test_file_storage_backend_persists_runtime_assets(tmp_path: Path) -> None:
    storage = FileStorageBackend(tmp_path)
    service = RunService(PROJECT_ROOT, storage=storage)
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    saved = service.save_diagnostic_case_from_run(state["run_id"])
    created = service.create_candidate_skill_from_run(state["run_id"])

    assert storage.get_run(state["run_id"])["run_id"] == state["run_id"]
    assert saved["path"].startswith("diagnostic_cases/")
    assert len(storage.list_diagnostic_cases()) == 1
    assert storage.get_candidate_skill(
        created["candidate"]["candidate_id"]
    )["candidate_id"] == created["candidate"]["candidate_id"]


def test_sqlite_storage_backend_persists_runtime_assets(tmp_path: Path) -> None:
    storage = SQLiteStorageBackend(tmp_path)
    service = RunService(PROJECT_ROOT, storage=storage)
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    saved = service.save_diagnostic_case_from_run(state["run_id"])
    created = service.create_candidate_skill_from_run(state["run_id"])
    candidate = created["candidate"]
    record = {
        "schema_version": "1.0",
        "rejection_id": "rej-sqlite001",
        "created_at": "2026-07-28T00:00:00Z",
        "candidate_id": candidate["candidate_id"],
        "skill_id": candidate["skill_id"],
        "decision": "REJECT",
        "fault_type": "skill_wrong",
        "action": "patch_skill",
        "patch_sha256": "sqlite-seed",
    }
    rejection_path = storage.save_rejection_memory(record["rejection_id"], record)

    assert storage.database_path.is_file()
    assert storage.get_run(state["run_id"])["run_id"] == state["run_id"]
    assert saved["path"].startswith("sqlite://")
    assert len(storage.list_diagnostic_cases()) == 1
    assert storage.get_candidate_skill(
        candidate["candidate_id"]
    )["candidate_id"] == candidate["candidate_id"]
    assert rejection_path.endswith("#rejection_memory/rej-sqlite001")
    assert storage.list_rejection_memory(
        candidate["skill_id"]
    )[0]["rejection_id"] == "rej-sqlite001"


def test_build_storage_backend_uses_sqlite_from_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SKILL_DOCTOR_SQLITE_PATH", "custom/storage.sqlite3")

    storage = build_storage_backend(tmp_path)

    assert isinstance(storage, SQLiteStorageBackend)
    assert storage.database_path == tmp_path / "custom" / "storage.sqlite3"


def test_repair_preview_from_attributed_trace_run(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    preview = service.create_repair_preview(state["run_id"])

    assert preview["schema_version"] == "1.0"
    assert preview["run_id"] == state["run_id"]
    assert preview["repair_type"] == "skill_revision"
    assert preview["status"] == "preview_only"
    assert preview["can_apply"] is False
    assert "Skill Doctor 修复建议" in preview["suggested_patch"]["after"]
    assert preview["verification_plan"]


def test_candidate_skill_created_and_validated_from_trace_run(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.candidate_skill_directory = tmp_path / "candidate_skills"
    service.rejection_memory_directory = tmp_path / "rejection_memory"
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    created = service.create_candidate_skill_from_run(state["run_id"])
    candidate = created["candidate"]

    assert created["status"] == "created"
    assert candidate["candidate_id"].startswith("cand-")
    assert candidate["created_from_run_id"] == state["run_id"]
    assert "Skill Doctor 修复建议" in candidate["skill_content_after"]
    assert (service.candidate_skill_directory / f"{candidate['candidate_id']}.json").is_file()

    validation = service.validate_candidate_skill(
        candidate["candidate_id"],
        CandidateValidationRequest(include_saved_cases=False),
    )
    assert validation["status"] == "validated"
    assert validation["candidate_id"] == candidate["candidate_id"]
    assert validation["decision"] in {"ADOPT", "REJECT"}
    assert validation["checks"]
    assert validation["rejection_memory"]["matched_count"] == 0
    assert "Skill Doctor Candidate Validation" in validation["markdown"]


def test_rejection_memory_records_and_blocks_duplicate_candidate(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.candidate_skill_directory = tmp_path / "candidate_skills"
    service.rejection_memory_directory = tmp_path / "rejection_memory"
    service.adaptor_llm_client = None

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    created = service.create_candidate_skill_from_run(state["run_id"])
    candidate = created["candidate"]
    service.rejection_memory_directory.mkdir(parents=True, exist_ok=True)
    (service.rejection_memory_directory / "rej-seeded001.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "rejection_id": "rej-seeded001",
                "created_at": "2026-07-28T00:00:00Z",
                "candidate_id": "cand-seeded001",
                "skill_id": candidate["skill_id"],
                "decision": "REJECT",
                "fault_type": "skill_wrong",
                "action": "patch_skill",
                "failed_checks": ["candidate_passed"],
                "reasons": ["历史候选未修复源失败。"],
                "regressed_cases": [],
                "patch_summary": "重复补丁",
                "patch_sha256": hashlib.sha256(
                    candidate["skill_content_after"].encode("utf-8")
                ).hexdigest(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = service.validate_candidate_skill(
        candidate["candidate_id"],
        CandidateValidationRequest(include_saved_cases=False),
    )

    assert validation["decision"] == "REJECT"
    duplicate_check = next(
        item
        for item in validation["checks"]
        if item["name"] == "not_duplicate_rejected_candidate"
    )
    assert duplicate_check["passed"] is False
    assert validation["rejection_memory"]["matched_count"] >= 1
    assert validation["rejection_memory"]["recorded"]["rejection_id"].startswith("rej-")
    history = service.list_rejection_history(candidate["skill_id"])
    assert history["count"] == 2


def test_rejection_memory_injects_constraints_into_new_candidate(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.candidate_skill_directory = tmp_path / "candidate_skills"
    service.rejection_memory_directory = tmp_path / "rejection_memory"
    service.adaptor_llm_client = None
    service.rejection_memory_directory.mkdir(parents=True, exist_ok=True)
    (service.rejection_memory_directory / "rej-samefault001.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "rejection_id": "rej-samefault001",
                "created_at": "2026-07-28T00:00:00Z",
                "candidate_id": "cand-old001",
                "skill_id": "trace-skill",
                "decision": "REJECT",
                "fault_type": "skill_wrong",
                "action": "patch_skill",
                "failed_checks": ["no_regressed_cases"],
                "reasons": ["引入了新的回归用例。"],
                "regressed_cases": ["healthy-aime-fast-path"],
                "patch_summary": "旧失败补丁",
                "patch_sha256": "seed",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    created = service.create_candidate_skill_from_run(state["run_id"])
    candidate = created["candidate"]

    assert candidate["rejection_memory"]["matched_count"] == 1
    assert candidate["rejection_memory"]["constraints"]
    assert "Reject Memory 约束" in candidate["skill_content_after"]


def test_candidate_skill_http_endpoints(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.candidate_skill_directory = tmp_path / "candidate_skills"
    service.rejection_memory_directory = tmp_path / "rejection_memory"
    service.adaptor_llm_client = None
    state = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            f"/repairs/candidates/from-run/{state['run_id']}",
            json.dumps({}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        created = json.loads(response.read().decode("utf-8"))
        assert response.status == 200, created

        candidate_id = created["candidate"]["candidate_id"]
        connection.request(
            "POST",
            f"/repairs/candidates/{candidate_id}/validate",
            json.dumps({"include_saved_cases": False}),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        validation = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 200, validation
        assert validation["candidate_id"] == candidate_id
        assert validation["status"] == "validated"

        connection.request("GET", "/repairs/rejections/trace-skill")
        response = connection.getresponse()
        history = json.loads(response.read().decode("utf-8"))

        assert response.status == 200, history
        assert history["skill_id"] == "trace-skill"
        assert "records" in history
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_verify_repair_adopts_improved_candidate(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None

    baseline = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    candidate = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_candidate_payload(passed=True)))
    )
    report = service.verify_repair(
        RepairVerificationRequest(
            baseline_run_id=baseline["run_id"],
            candidate_run_id=candidate["run_id"],
        )
    )

    assert report["schema_version"] == "1.0"
    assert report["decision"] == "ADOPT"
    assert report["baseline"]["run_id"] == baseline["run_id"]
    assert report["candidate"]["run_id"] == candidate["run_id"]
    assert report["delta"]["pass_rate_delta"] == 0.5
    assert all(item["passed"] for item in report["checks"])
    assert "Skill Doctor Repair Verification" in report["markdown"]
    updated = service.get(candidate["run_id"])
    assert updated["verification"]["decision"] == "ADOPT"
    assert updated["repair_verification"]["decision"] == "ADOPT"


def test_verify_repair_rejects_unfixed_candidate(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None

    baseline = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    candidate = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_candidate_payload(passed=False)))
    )
    report = service.verify_repair(
        RepairVerificationRequest(
            baseline_run_id=baseline["run_id"],
            candidate_run_id=candidate["run_id"],
        )
    )

    assert report["decision"] == "REJECT"
    assert any(not item["passed"] for item in report["checks"])
    assert any("未满足" in reason for reason in report["reasons"])


def test_verify_repair_http_endpoint(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "reports"
    service.diagnostic_case_directory = tmp_path / "diagnostic_cases"
    service.adaptor_llm_client = None
    baseline = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_payload()))
    )
    candidate = service.ingest_trace(
        TraceIngestRequest.model_validate(json.loads(_candidate_payload(passed=True)))
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            "/repairs/verify",
            json.dumps(
                {
                    "baseline_run_id": baseline["run_id"],
                    "candidate_run_id": candidate["run_id"],
                }
            ),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        report = json.loads(response.read().decode("utf-8"))
        connection.close()

        assert response.status == 200
        assert report["decision"] == "ADOPT"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _raw_puck_trace_payload() -> str:
    """A raw puck-rule-rca style trace with no normalized execution."""

    return json.dumps(
        {
            "task": "Diagnose puck-rule-rca uploaded trace.",
            "skill_id": "puck-rule-rca",
            "skill_version": "1.0.0",
            "skill_content": "Follow the noise-judge procedure.",
            "repair_enabled": False,
            "runtime_events": [
                {
                    "stage": "puck.fetch_metric_info",
                    "status": "completed",
                    "message": "Loaded metric detail for indicator 1128.",
                    "metadata": {"indicator": 1128},
                },
                {
                    "stage": "puck.timeseries_query",
                    "status": "completed",
                    "message": "Retrieved three-window timeseries.",
                    "metadata": {"windows": ["today", "day-1", "day-2"]},
                },
                {
                    "stage": "puck.noise_judge",
                    "status": "completed",
                    "message": "rca_filter=true confidence=0.64.",
                    "metadata": {"rca_filter": True, "confidence": 0.64},
                },
            ],
            "tool_calls": [
                {
                    "name": "fetch_rule_metric_info",
                    "status": "completed",
                    "output": "ok",
                }
            ],
            "model_messages": [
                {"role": "assistant", "content": "Analysis complete."}
            ],
            "trace_metadata": {
                "puck_task_id": 185179,
                "dispatch_id": 12992029,
                "indicator": 1128,
                "rca_filter": True,
                "confidence": 0.64,
            },
        }
    )


def test_trace_ingest_accepts_raw_puck_rule_rca_trace(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_INGEST_API_KEY", "secret-token")
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            "/traces",
            _raw_puck_trace_payload(),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )
        response = connection.getresponse()
        state = json.loads(response.read().decode("utf-8"))

        assert response.status == 200, state
        assert state["executor"] == "trace-ingest"
        assert state["execution"]["executor"] == "aime-skill-trace"
        # Confidence 0.64 < 0.75 → skill assertion fails → overall passed=False
        assert state["execution"]["passed"] is False
        stages = [event["stage"] for event in state["events"]]
        assert "agent.analyze" in stages
        assert "agent.analyze.runtime_events" in stages
        assert "agent.analyze.summarize" in stages
        # Attribution should hold a skill-owned cause.
        assert state["attribution"]["cause"] in {"skill", "loader"}
        # The original puck runtime events must have been preserved.
        recorded_stages = {event["stage"] for event in state["events"]}
        assert "puck.noise_judge" in recorded_stages
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_trace_ingest_rejects_empty_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_INGEST_API_KEY", "secret-token")
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request(
            "POST",
            "/traces",
            json.dumps({"skill_id": "puck-rule-rca"}),
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
            },
        )
        response = connection.getresponse()

        assert response.status == 422
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
