from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import (
    IngestInput,
    LoadedTrace,
    has_trace_signal,
    load_trace_json,
    payload_from_standard_trace_dir,
    validate_input,
)


class GenericAdapter:
    source = "generic"
    adapter_name = "generic_cli_ingest"

    def load(self, source_input: IngestInput) -> LoadedTrace:
        trace, trace_dir = validate_input(source_input)
        if trace_dir:
            payload, input_info = payload_from_standard_trace_dir(
                Path(trace_dir),
                default_task="Imported generic execution trace.",
            )
            return LoadedTrace(payload=payload, input_info=input_info)
        return load_trace_json(IngestInput(trace=trace))

    def adapt(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("skill_id"):
            raise ValueError("generic trace payload is missing required field 'skill_id'.")
        if not has_trace_signal(payload):
            raise ValueError(
                "generic trace payload has no trace signal. Provide execution, runtime_events, "
                "tool_calls, model_messages, or trace_metadata."
            )

        adapted = dict(payload)
        metadata = dict(adapted.get("trace_metadata") or {})
        metadata.setdefault("source", self.adapter_name)
        metadata.setdefault("skill_runtime", "generic")
        adapted["trace_metadata"] = metadata
        adapted.pop("schema_version", None)
        return adapted
