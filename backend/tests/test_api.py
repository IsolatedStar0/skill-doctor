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


def test_scenario_catalog_api() -> None:
    client = TestClient(app)

    response = client.get("/scenarios")

    assert response.status_code == 200
    payload = response.json()
    scenarios = {item["id"]: item for item in payload["scenarios"]}
    assert payload["schema_version"] == "1.0"
    assert scenarios["content-gap"]["repair_action"] == "patch_skill"
    assert scenarios["loading-miss"]["skill_id"] == "release-checklist"
    assert scenarios["platform-error"]["category"] == "platform"


def test_dynamic_benchmark_api(tmp_path: Path) -> None:
    service.report_directory = tmp_path / "langgraph"
    client = TestClient(app)

    response = client.post(
        "/benchmarks",
        json={
            "skill_id": "tdd-workflow",
            "executor": "fixture",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["report"]["summary"]["averagePassRateDelta"] == 0.5

    stored = client.get(f"/benchmarks/{payload['run_id']}")
    assert stored.status_code == 200
    assert stored.json()["control_run_id"] == payload["control_run_id"]

    listed = client.get("/benchmarks")
    assert listed.status_code == 200
    assert listed.json()["benchmarks"][0]["run_id"] == payload["run_id"]
