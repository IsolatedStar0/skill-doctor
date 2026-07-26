import json
import io
import subprocess
from pathlib import Path

from backend.skilldoctor.models import ExecutionResult, RunRequest, TokenUsage
from backend.skilldoctor.service import RunService
from backend.skilldoctor.workers import CodexExecutionWorker


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeStdin:
    def __init__(self) -> None:
        self.value = ""

    def write(self, value: str) -> None:
        self.value += value

    def close(self) -> None:
        pass


class FakeProcess:
    def __init__(
        self,
        lines: list[dict],
        *,
        returncode: int = 0,
        stderr: str = "",
    ) -> None:
        self.stdin = FakeStdin()
        self.stdout = iter(
            [f"{json.dumps(line)}\n" for line in lines]
        )
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def bridge_result() -> dict:
    return {
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


def test_codex_worker_maps_bridge_result(monkeypatch) -> None:
    captured: dict = {}

    def fake_popen(command, **options):
        captured["command"] = command
        process = FakeProcess(
            [
                {
                    "kind": "event",
                    "sequence": 1,
                    "occurredAt": "2026-07-26T00:00:00Z",
                    "event": {
                        "type": "thread.started",
                        "thread_id": "thread-test",
                    },
                },
                {
                    "kind": "event",
                    "sequence": 2,
                    "occurredAt": "2026-07-26T00:00:01Z",
                    "event": {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "cached_input_tokens": 2,
                            "reasoning_output_tokens": 1,
                        },
                    },
                },
                {"kind": "result", "result": bridge_result()},
            ]
        )
        captured["process"] = process
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    worker = CodexExecutionWorker(
        PROJECT_ROOT,
        RunRequest(executor="codex"),
        node_executable="node",
    )
    streamed_events = []
    worker.set_event_callback(streamed_events.append)
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
    assert [event["stage"] for event in streamed_events] == [
        "codex.thread",
        "codex.turn",
    ]
    assert result.runtime_events == streamed_events
    captured["payload"] = json.loads(captured["process"].stdin.value)
    assert captured["payload"]["runId"] == "lg-test"
    assert captured["payload"]["skillId"] == "test-skill"
    assert captured["payload"]["reasoningEffort"] == "medium"


def test_codex_worker_converts_bridge_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(
            [
                {
                    "kind": "bridge_error",
                    "error": "authentication failed",
                }
            ],
            returncode=2,
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


def test_run_service_streams_worker_events_before_execute_finishes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_events = [
        {
            "stage": "codex.thread",
            "status": "started",
            "message": "thread started",
            "usage": None,
            "metadata": {"thread_id": "thread-test"},
        },
        {
            "stage": "codex.turn",
            "status": "completed",
            "message": "turn completed",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 2,
                "reasoning_tokens": 1,
            },
            "metadata": {"event_type": "turn.completed"},
        },
    ]

    class StreamingWorker:
        callback = None

        def set_event_callback(self, callback) -> None:
            self.callback = callback

        def run(self, **kwargs) -> ExecutionResult:
            for event in runtime_events:
                self.callback(event)
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=True,
                pass_rate=1,
                duration_ms=123,
                usage=TokenUsage(input_tokens=10, output_tokens=5),
                summary="completed",
                runtime_events=runtime_events,
            )

    service = RunService(PROJECT_ROOT)
    service.report_directory = tmp_path
    monkeypatch.setattr(service, "_worker", lambda request: StreamingWorker())
    states = list(
        service.stream(
            RunRequest(
                executor="codex",
                skill_id="tdd-workflow",
                stream_delay_ms=0,
            )
        )
    )

    live_state = next(
        state
        for state in states
        if state["events"]
        and state["events"][-1]["stage"] == "codex.thread"
        and "execution" not in state
    )
    final_state = states[-1]
    assert live_state["status"] == "running"
    assert [event["stage"] for event in final_state["events"]] == [
        "prepare",
        "codex.thread",
        "codex.turn",
        "execute",
        "collect_evidence",
        "finalize",
    ]
    token_events = [
        event
        for event in final_state["events"]
        if event.get("usage") is not None
    ]
    assert len(token_events) == 1
    assert token_events[0]["stage"] == "codex.turn"


def test_live_request_resolves_benchmark_task_and_skill() -> None:
    service = RunService(PROJECT_ROOT)
    state = service._initial_state(
        RunRequest(executor="codex", skill_id="tdd-workflow"),
        "lg-test",
    )

    assert "src/calculator.py" in state["task"]
    assert state["skill_content"].startswith("---")
    assert "80%" in state["skill_content"]
