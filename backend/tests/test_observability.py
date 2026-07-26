from pathlib import Path

import langsmith

from backend.skilldoctor.models import RunRequest
from backend.skilldoctor.observability import LangSmithRunExporter
from backend.skilldoctor.service import RunService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    def record_event(self, event: dict) -> None:
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


def test_run_service_mirrors_events_and_exposes_trace_link(
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
    assert [event["stage"] for event in exporters[0].events] == [
        event["stage"] for event in result["events"]
    ]
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
