from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from backend.skilldoctor.api import app, service


def test_health_and_run_api(tmp_path: Path) -> None:
    service.report_directory = tmp_path
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "orchestrator": "langgraph"}

    response = client.post(
        "/runs",
        json={
            "skill_id": "spreadsheet-summary",
            "executor": "fixture",
            "scenario": "content-gap",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "passed"

    stored = client.get(f"/runs/{payload['run_id']}")
    assert stored.status_code == 200
    assert stored.json()["verification"]["decision"] == "ADOPT"

    listed = client.get("/runs")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["run_id"] == payload["run_id"]
