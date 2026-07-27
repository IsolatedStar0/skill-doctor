import http.client
import json
import threading
from pathlib import Path

from backend.skilldoctor.http_server import make_handler
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
        assert state["status"] == "failed"
        assert service.get(state["run_id"])["run_id"] == state["run_id"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
