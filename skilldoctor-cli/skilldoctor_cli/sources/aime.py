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


class AimeAdapter:
    source = "aime"
    adapter_name = "aime_cli_ingest"

    def load(self, source_input: IngestInput) -> LoadedTrace:
        trace, trace_dir = validate_input(source_input)
        if trace_dir:
            payload, input_info = payload_from_standard_trace_dir(
                Path(trace_dir),
                default_task="Imported AIME execution trace.",
                compatibility_metadata_keys=(
                    "aime_session",
                    "aime_assistant",
                    "aime_trace_id",
                    "aime_run_id",
                    "run_id",
                ),
            )
            return LoadedTrace(payload=payload, input_info=input_info)
        return load_trace_json(IngestInput(trace=trace))

    def adapt(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("skill_id"):
            raise ValueError("AIME trace payload is missing required field 'skill_id'.")
        if not has_trace_signal(payload):
            raise ValueError(
                "AIME trace payload has no trace signal. Provide execution, runtime_events, "
                "tool_calls, model_messages, or trace_metadata."
            )

        adapted = dict(payload)
        metadata = dict(adapted.get("trace_metadata") or {})
        metadata.setdefault("source", self.adapter_name)
        metadata.setdefault("skill_runtime", "aime")
        adapted["trace_metadata"] = metadata
        adapted.pop("schema_version", None)
        return adapted
