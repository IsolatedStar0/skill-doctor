from pathlib import Path

from backend.skilldoctor.registry import RunRegistry


def state(run_id: str, status: str = "running") -> dict:
    return {
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
