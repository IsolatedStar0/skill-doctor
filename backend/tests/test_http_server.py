import http.client
import json
import threading
import time
from pathlib import Path

from backend.skilldoctor import start
from backend.skilldoctor.http_server import make_handler, parser
from backend.skilldoctor.service import RunService
from http.server import ThreadingHTTPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dependency_free_server_defaults_to_cloud_friendly_host(monkeypatch) -> None:
    monkeypatch.delenv("SKILL_DOCTOR_HOST", raising=False)

    args = parser().parse_args([])

    assert args.host == "0.0.0.0"


def test_dependency_free_server_host_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("SKILL_DOCTOR_HOST", "127.0.0.1")

    args = parser().parse_args([])

    assert args.host == "127.0.0.1"


def test_dependency_free_server_port_can_use_cloud_env(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "9876")

    args = parser().parse_args([])

    assert args.port == 9876


def test_hosted_fastapi_start_uses_cloud_port(monkeypatch) -> None:
    calls = []
    monkeypatch.setenv("PORT", "9877")
    monkeypatch.setattr(start.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    start.main()

    assert calls == [
        (
            ("backend.skilldoctor.api:app",),
            {"host": "0.0.0.0", "port": 9877},
        )
    ]


def test_dependency_free_server_returns_scenario_catalog() -> None:
    service = RunService(PROJECT_ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request("GET", "/scenarios")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert payload["schema_version"] == "1.0"
        assert [item["id"] for item in payload["scenarios"]] == [
            "content-gap",
            "loading-miss",
            "platform-error",
            "network-error",
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dependency_free_server_streams_graph_states(tmp_path: Path) -> None:
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
        body = json.dumps(
            {
                "executor": "fixture",
                "scenario": "content-gap",
                "skill_id": "spreadsheet-summary",
                "stream_delay_ms": 0,
            }
        )
        connection.request(
            "POST",
            "/runs/stream",
            body,
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        states = [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line
        ]

        assert response.status == 200
        assert len(states) == 10
        assert states[0]["events"] == []
        assert states[-1]["status"] == "passed"
        assert states[-1]["verification"]["decision"] == "ADOPT"

        list_connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        list_connection.request("GET", "/runs")
        list_response = list_connection.getresponse()
        listed = json.loads(list_response.read().decode("utf-8"))

        assert list_response.status == 200
        assert listed["runs"][0]["run_id"] == states[-1]["run_id"]
        assert listed["runs"][0]["status"] == "passed"

        event_connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        event_connection.request("GET", "/runs/events")
        event_response = event_connection.getresponse()
        event_id = event_response.readline().decode("utf-8").strip()
        event_data = event_response.readline().decode("utf-8").strip()

        assert event_response.status == 200
        assert event_response.getheader("Content-Type").startswith(
            "text/event-stream"
        )
        assert event_id.startswith(f"id: {states[-1]['run_id']}:")
        assert json.loads(event_data.removeprefix("data: "))["state"][
            "run_id"
        ] == states[-1]["run_id"]

        event_connection.close()
        service.registry.publish(states[-1])
        time.sleep(0.3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dependency_free_server_run_list_accepts_limit(tmp_path: Path) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        for index in range(2):
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=10,
            )
            connection.request(
                "POST",
                "/runs",
                body=json.dumps(
                    {
                        "skill_id": f"demo-skill-{index}",
                        "executor": "fixture",
                        "scenario": "content-gap",
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            assert response.status == 200

        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=10,
        )
        connection.request("GET", "/runs?limit=1")
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))

        assert response.status == 200
        assert len(payload["runs"]) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dependency_free_server_streams_dynamic_benchmark(
    tmp_path: Path,
) -> None:
    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path / "langgraph"
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
            "/benchmarks/stream",
            json.dumps(
                {
                    "executor": "fixture",
                    "skill_id": "tdd-workflow",
                }
            ),
            {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        states = [
            json.loads(line)
            for line in response.read().decode("utf-8").splitlines()
            if line
        ]

        assert response.status == 200
        assert states[0]["status"] == "pending"
        assert states[-1]["status"] == "completed"
        assert states[-1]["report"]["pairs"][0]["comparison"][
            "outcome"
        ] == "improved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
