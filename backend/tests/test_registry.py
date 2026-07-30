from pathlib import Path

from backend.skilldoctor.registry import RunRegistry
from backend.skilldoctor.service import RunService
from backend.skilldoctor.storage import FileStorageBackend


def state(run_id: str, status: str = "running") -> dict:
    run_kind = "benchmark" if run_id.startswith("bm-") else "agent"
    return {
        "run_kind": run_kind,
        "run_id": run_id,
        "task": "test",
        "skill_id": "tdd-workflow",
        "skill_version": "1.0.0",
        "skill_content": "test",
        "executor": "fixture",
        "scenario": "content-gap",
        "attempt": 0,
        "max_attempts": 2,
        "status": status,
        "stop_reason": "",
        "events": [],
    }


def test_registry_shares_atomic_snapshots_across_instances(
    tmp_path: Path,
) -> None:
    writer = RunRegistry(tmp_path)
    reader = RunRegistry(tmp_path)

    first = writer.publish(state("lg-registry001"))
    writer.publish(state("lg-registry001", "passed"))

    assert first["type"] == "run.updated"
    assert reader.get("lg-registry001")["status"] == "passed"
    runs = reader.list()
    assert runs == [
        {
            "run_kind": "agent",
            "run_id": "lg-registry001",
            "parent_run_id": None,
            "skill_id": "tdd-workflow",
            "skill_version": "1.0.0",
            "executor": "fixture",
            "scenario": "content-gap",
            "condition": "standard",
            "attempt": 0,
            "max_attempts": 2,
            "status": "passed",
            "stop_reason": "",
            "event_count": 0,
            "updated_at": runs[0]["updated_at"],
        }
    ]


def test_registry_event_stream_observes_new_process_writes(
    tmp_path: Path,
) -> None:
    reader = RunRegistry(tmp_path)
    writer = RunRegistry(tmp_path)
    events = reader.events(poll_interval=0, heartbeat_seconds=60)

    writer.publish(state("lg-registry002"))
    event = next(events)

    assert event is not None
    assert event["state"]["run_id"] == "lg-registry002"
    events.close()


def test_run_list_merges_persisted_storage_and_live_registry(
    tmp_path: Path,
) -> None:
    service = RunService(storage=FileStorageBackend(tmp_path))
    service.storage.save_run(state("lg-registry003", "passed"))
    service.registry.publish(state("lg-registry004", "running"))

    runs = service.list_runs()
    run_ids = [item["run_id"] for item in runs]

    assert "lg-registry003" in run_ids
    assert "lg-registry004" in run_ids
    assert next(item for item in runs if item["run_id"] == "lg-registry004")[
        "status"
    ] == "running"


def test_run_list_prefers_live_registry_status_for_same_run(
    tmp_path: Path,
) -> None:
    service = RunService(storage=FileStorageBackend(tmp_path))
    service.storage.save_run(state("lg-registry005", "passed"))
    service.registry.publish(state("lg-registry005", "running"))

    runs = service.list_runs()

    assert [item["run_id"] for item in runs].count("lg-registry005") == 1
    assert next(item for item in runs if item["run_id"] == "lg-registry005")[
        "status"
    ] == "running"
