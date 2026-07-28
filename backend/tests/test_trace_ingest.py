import http.client
import json
import threading
from pathlib import Path

from backend.skilldoctor.http_server import make_handler
from backend.skilldoctor.models import TraceIngestRequest
from backend.skilldoctor.service import RunService
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
                        "metadata": {"source": "aime"},
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


def test_default_diagnostic_suite_covers_core_trace_routes(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
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
