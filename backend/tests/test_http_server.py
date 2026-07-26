import http.client
import json
import threading
import time
from pathlib import Path

from backend.skilldoctor.http_server import make_handler
from backend.skilldoctor.service import RunService
from http.server import ThreadingHTTPServer


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
