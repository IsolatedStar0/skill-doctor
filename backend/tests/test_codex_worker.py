import json
import subprocess
from pathlib import Path

from backend.skilldoctor.models import RunRequest
from backend.skilldoctor.service import RunService
from backend.skilldoctor.workers import CodexExecutionWorker


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_codex_worker_maps_bridge_result(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(command, **options):
        captured["command"] = command
        captured["payload"] = json.loads(options["input"])
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "executor": "codex-sdk-live",
                    "condition": "with_skill",
                    "passed": True,
                    "pass_rate": 1,
                    "duration_ms": 123,
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cached_input_tokens": 2,
                        "reasoning_tokens": 1,
                    },
                    "assertions": [
                        {
                            "id": "contract",
                            "source": "skill",
                            "passed": True,
                            "detail": "matched",
                        }
                    ],
                    "regression_rate": 0,
                    "summary": "completed",
                    "artifacts": {"codexJsonl": "reports/codex.jsonl"},
                    "error": None,
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    worker = CodexExecutionWorker(
        PROJECT_ROOT,
        RunRequest(executor="codex"),
        node_executable="node",
    )
    result = worker.run(
        run_id="lg-test",
        attempt=0,
        task="test task",
        skill_id="test-skill",
        skill_content="test instructions",
    )

    assert result.passed is True
    assert result.executor == "codex-sdk-live"
    assert result.usage.total_tokens == 15
    assert captured["payload"]["runId"] == "lg-test"
    assert captured["payload"]["skillId"] == "test-skill"
    assert captured["payload"]["reasoningEffort"] == "medium"


def test_codex_worker_converts_bridge_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            2,
            stdout="",
            stderr="authentication failed",
        ),
    )
    worker = CodexExecutionWorker(
        PROJECT_ROOT,
        RunRequest(executor="codex"),
        node_executable="node",
    )
    result = worker.run(
        run_id="lg-test",
        attempt=0,
        task="test task",
        skill_id="test-skill",
        skill_content="test instructions",
    )

    assert result.passed is False
    assert result.error == "authentication failed"


def test_live_request_resolves_benchmark_task_and_skill() -> None:
    service = RunService(PROJECT_ROOT)
    state = service._initial_state(
        RunRequest(executor="codex", skill_id="tdd-workflow"),
        "lg-test",
    )

    assert "src/calculator.py" in state["task"]
    assert state["skill_content"].startswith("---")
    assert "80%" in state["skill_content"]
