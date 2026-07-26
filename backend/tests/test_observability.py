from pathlib import Path
from uuid import uuid4

import langsmith
from langchain_core.tracers.base import BaseTracer
from langsmith.run_trees import RunTree

from backend.skilldoctor.graph import build_agent_graph
from backend.skilldoctor.models import ExecutionResult, RunRequest
from backend.skilldoctor.observability import LangSmithRunExporter
from backend.skilldoctor.service import RunService
from backend.skilldoctor.workers import FixtureWorker


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CollectingTracer(BaseTracer):
    def __init__(self) -> None:
        super().__init__()
        self.runs = []

    def _persist_run(self, run) -> None:
        pass

    def _on_run_update(self, run) -> None:
        self.runs.append(run)


class RecordingExporter:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.events: list[dict] = []
        self.finished = False

    def snapshot(self) -> dict:
        return {
            "provider": "langsmith",
            "enabled": True,
            "status": "completed" if self.finished else "active",
            "project": "test-project",
            "trace_id": "trace-test",
            "trace_url": "https://smith.langchain.com/test-run",
        }

    def graph_config(self) -> dict:
        return {"run_name": "skill-doctor.run"}

    def record_event(self, event: dict, config=None) -> None:
        self.events.append(event)

    def finish(self, result=None, error=None) -> None:
        self.finished = True


def test_langsmith_is_disabled_without_configuration(
    monkeypatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    exporter = LangSmithRunExporter("lg-test", RunRequest())

    assert exporter.snapshot() == {
        "provider": "langsmith",
        "enabled": False,
        "status": "disabled",
        "project": "skill-doctor-dev",
    }


def test_langsmith_startup_failure_is_non_fatal(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")

    def fail_client(**kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(langsmith, "Client", fail_client)
    exporter = LangSmithRunExporter("lg-test", RunRequest())

    assert exporter.snapshot()["status"] == "degraded"
    assert exporter.snapshot()["error"] == "RuntimeError: offline"


def test_codex_usage_maps_to_langsmith_token_schema() -> None:
    usage = LangSmithRunExporter._usage_metadata(
        {
            "input_tokens": 100,
            "output_tokens": 30,
            "cached_input_tokens": 40,
            "reasoning_tokens": 12,
        }
    )

    assert LangSmithRunExporter._run_type("codex.turn") == "llm"
    assert usage == {
        "input_tokens": 100,
        "output_tokens": 30,
        "total_tokens": 130,
        "input_token_details": {"cache_read": 40},
        "output_token_details": {"reasoning": 12},
    }


def test_graph_config_names_the_single_native_root(monkeypatch) -> None:
    class FakeClient:
        pass

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(langsmith, "Client", lambda **kwargs: FakeClient())
    exporter = LangSmithRunExporter(
        "lg-test",
        RunRequest(executor="codex", skill_id="tdd-workflow"),
    )

    config = exporter.graph_config()

    assert config["run_name"] == "skill-doctor.run"
    assert str(config["run_id"]) == exporter.snapshot()["trace_id"]
    assert config["metadata"]["thread_id"] == "lg-test"
    assert config["metadata"]["ls_agent_type"] == "root"


def test_langgraph_emits_one_named_root_trace() -> None:
    tracer = CollectingTracer()
    trace_id = uuid4()
    request = RunRequest(
        executor="fixture",
        scenario="network-error",
    )
    service = RunService(PROJECT_ROOT)
    state = service._initial_state(request, "lg-single-root")
    graph = build_agent_graph(FixtureWorker(request.scenario))

    graph.invoke(
        state,
        config={
            "callbacks": [tracer],
            "run_id": trace_id,
            "run_name": "skill-doctor.run",
            "configurable": {"thread_id": "lg-single-root"},
        },
    )

    roots = [run for run in tracer.runs if run.parent_run_id is None]
    assert len(roots) == 1
    assert roots[0].id == trace_id
    assert roots[0].name == "skill-doctor.run"
    assert any(run.name == "execute" for run in tracer.runs)


def test_codex_event_is_posted_as_child_of_graph_run(monkeypatch) -> None:
    posted: list[dict] = []

    class FakeClient:
        pass

    class FakeChild:
        def end(self, *, outputs=None, error=None) -> None:
            posted.append({"outputs": outputs, "error": error})

        def post(self) -> None:
            posted[-1]["posted"] = True

    class FakeParent:
        def create_child(self, **kwargs):
            posted.append({"child": kwargs})
            return FakeChild()

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setattr(langsmith, "Client", lambda **kwargs: FakeClient())
    monkeypatch.setattr(
        RunTree,
        "from_runnable_config",
        lambda config: FakeParent(),
    )
    exporter = LangSmithRunExporter("lg-test", RunRequest(executor="codex"))

    exporter.record_event(
        {
            "stage": "codex.turn",
            "status": "completed",
            "message": "Turn completed.",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_input_tokens": 2,
                "reasoning_tokens": 1,
            },
            "metadata": {"event_type": "turn.completed"},
        },
        {"callbacks": object()},
    )

    assert posted[0]["child"]["name"] == "codex.turn"
    assert posted[0]["child"]["run_type"] == "llm"
    assert posted[1]["outputs"]["usage_metadata"]["total_tokens"] == 15
    assert posted[1]["posted"] is True


def test_run_service_uses_native_graph_trace_without_mirroring_nodes(
    tmp_path: Path,
) -> None:
    exporters: list[RecordingExporter] = []

    def factory(run_id: str, request: RunRequest) -> RecordingExporter:
        exporter = RecordingExporter(run_id)
        exporters.append(exporter)
        return exporter

    service = RunService(PROJECT_ROOT, exporter_factory=factory)
    service.report_directory = tmp_path
    result = service.run(
        RunRequest(
            skill_id="spreadsheet-summary",
            executor="fixture",
            scenario="content-gap",
        )
    )

    assert exporters[0].finished is True
    assert exporters[0].events == []
    assert result["observability"]["status"] == "completed"
    assert result["observability"]["trace_id"] == "trace-test"
    assert result["observability"]["trace_url"].endswith("/test-run")


def test_stream_finishes_exporter_before_terminal_snapshot(
    tmp_path: Path,
) -> None:
    exporters: list[RecordingExporter] = []

    def factory(run_id: str, request: RunRequest) -> RecordingExporter:
        exporter = RecordingExporter(run_id)
        exporters.append(exporter)
        return exporter

    service = RunService(PROJECT_ROOT, exporter_factory=factory)
    service.report_directory = tmp_path
    states = list(
        service.stream(
            RunRequest(
                executor="fixture",
                scenario="network-error",
                stream_delay_ms=0,
            )
        )
    )

    assert states[-1]["status"] == "failed"
    assert states[-1]["observability"]["status"] == "completed"
    assert exporters[0].finished is True


def test_codex_runtime_events_are_exported_inside_graph_context(
    monkeypatch,
    tmp_path: Path,
) -> None:
    exporters: list[RecordingExporter] = []

    class StreamingWorker:
        callback = None

        def set_event_callback(self, callback) -> None:
            self.callback = callback

        def run(self, **kwargs) -> ExecutionResult:
            event = {
                "stage": "codex.turn",
                "status": "completed",
                "message": "Codex turn completed.",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cached_input_tokens": 2,
                    "reasoning_tokens": 1,
                },
                "metadata": {"event_type": "turn.completed"},
            }
            self.callback(event)
            return ExecutionResult(
                executor="codex-sdk-live",
                condition="with_skill",
                passed=True,
                pass_rate=1,
                duration_ms=10,
                summary="completed",
                runtime_events=[event],
            )

    def factory(run_id: str, request: RunRequest) -> RecordingExporter:
        exporter = RecordingExporter(run_id)
        exporters.append(exporter)
        return exporter

    service = RunService(PROJECT_ROOT, exporter_factory=factory)
    service.report_directory = tmp_path
    monkeypatch.setattr(service, "_worker", lambda request: StreamingWorker())
    states = list(
        service.stream(
            RunRequest(
                executor="codex",
                stream_delay_ms=0,
            )
        )
    )

    assert states[-1]["status"] == "passed"
    assert [event["stage"] for event in exporters[0].events] == [
        "codex.turn"
    ]
