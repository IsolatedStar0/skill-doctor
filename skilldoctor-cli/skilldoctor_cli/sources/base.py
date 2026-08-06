from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..workspace import load_json, load_jsonl


@dataclass(frozen=True)
class IngestInput:
    trace: str | Path | None = None
    trace_dir: str | Path | None = None


@dataclass(frozen=True)
class LoadedTrace:
    payload: dict[str, Any]
    input_info: dict[str, Any]


class SourceAdapter(Protocol):
    source: str
    adapter_name: str

    def load(self, source_input: IngestInput) -> LoadedTrace:
        """Load raw source input into an intermediate payload."""

    def adapt(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Adapt an intermediate payload into TraceIngestRequest shape."""


def has_trace_signal(payload: dict[str, Any]) -> bool:
    return any(
        payload.get(key)
        for key in (
            "execution",
            "runtime_events",
            "tool_calls",
            "model_messages",
            "trace_metadata",
        )
    )


def load_optional_json(path: Path) -> Any:
    return load_json(path) if path.exists() else None


def load_channel(directory: Path, stem: str) -> list[dict[str, Any]]:
    jsonl_path = directory / f"{stem}.jsonl"
    json_path = directory / f"{stem}.json"
    if jsonl_path.exists():
        records = load_jsonl(jsonl_path)
    elif json_path.exists():
        records = load_json(json_path)
    else:
        return []
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ValueError(f"{stem} must be a JSON array or JSONL stream of objects.")
    return records


def load_skill_content(directory: Path) -> str:
    for name in ("skill_content.md", "skill.md", "SKILL.md"):
        path = directory / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def validate_input(source_input: IngestInput) -> tuple[Path | None, Path | None]:
    trace = Path(source_input.trace).expanduser() if source_input.trace else None
    trace_dir = Path(source_input.trace_dir).expanduser() if source_input.trace_dir else None
    if trace and trace_dir:
        raise ValueError("ingest accepts either a trace JSON path or --trace-dir, not both.")
    if not trace and not trace_dir:
        raise ValueError("ingest requires a trace JSON path or --trace-dir.")
    return trace, trace_dir


def payload_from_standard_trace_dir(
    directory: Path,
    *,
    default_task: str,
    compatibility_metadata_keys: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not directory.is_dir():
        raise ValueError(f"Trace directory not found: {directory}")
    metadata = load_optional_json(directory / "metadata.json") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata.json must be a JSON object when provided.")

    trace_metadata = dict(metadata.get("trace_metadata") or {})
    for key in compatibility_metadata_keys:
        if key in metadata and key not in trace_metadata:
            trace_metadata[key] = metadata[key]

    payload: dict[str, Any] = {
        "task": metadata.get("task") or default_task,
        "skill_id": metadata.get("skill_id") or directory.name,
        "skill_version": metadata.get("skill_version") or "unknown",
        "skill_content": metadata.get("skill_content") or load_skill_content(directory),
        "runtime_events": load_channel(directory, "runtime_events"),
        "tool_calls": load_channel(directory, "tool_calls"),
        "model_messages": load_channel(directory, "model_messages"),
        "trace_metadata": trace_metadata,
    }
    for key in ("condition", "parent_run_id", "repair_enabled", "max_attempts"):
        if key in metadata:
            payload[key] = metadata[key]
    business_result = load_optional_json(directory / "business_result.json")
    if business_result is not None:
        payload["business_result"] = business_result
    elif "business_result" in metadata:
        payload["business_result"] = metadata["business_result"]

    input_info: dict[str, Any] = {
        "input_mode": "trace_dir",
        "trace_dir": str(directory),
    }
    if metadata.get("schema_version"):
        input_info["trace_schema_version"] = metadata["schema_version"]
    return payload, input_info


def load_trace_json(source_input: IngestInput) -> LoadedTrace:
    trace, trace_dir = validate_input(source_input)
    if trace_dir:
        raise ValueError("trace-dir loading must be handled by the source adapter.")
    assert trace is not None
    payload = load_json(trace)
    if not isinstance(payload, dict):
        raise ValueError("trace JSON must be a JSON object.")
    input_info: dict[str, Any] = {
        "input_mode": "trace_file",
        "trace_path": str(trace),
    }
    if payload.get("schema_version"):
        input_info["trace_schema_version"] = payload["schema_version"]
    return LoadedTrace(payload=payload, input_info=input_info)
