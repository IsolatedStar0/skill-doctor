from __future__ import annotations

from argparse import Namespace, SUPPRESS
from pathlib import Path
from typing import Any

from ..backend import backend_modules, new_run_service
from ..exit_codes import EXIT_DIAGNOSIS_FAILED, EXIT_OK
from ..output.console import print_run_summary
from ..output.json_writer import write_json_report
from ..output.markdown_writer import write_markdown_report
from ..workspace import default_report_path, load_json, load_jsonl


SUPPORTED_SOURCES = ("aime",)


def register(subcommands) -> None:
    command = subcommands.add_parser(
        "ingest",
        help="Ingest an external agent-platform trace through a source adapter.",
    )
    command.add_argument("trace", nargs="?", help="Path to a source trace JSON file.")
    command.add_argument(
        "--source",
        required=True,
        choices=SUPPORTED_SOURCES,
        help="Trace source adapter. Currently supports: aime.",
    )
    command.add_argument("--project-root", type=Path, default=SUPPRESS)
    command.add_argument(
        "--trace-dir",
        type=Path,
        help=(
            "Directory containing raw platform trace channels. For AIME, supported files are "
            "metadata.json, runtime_events.jsonl/json, tool_calls.jsonl/json, "
            "model_messages.jsonl/json, business_result.json, and skill_content.md."
        ),
    )
    command.add_argument("--json-out", type=Path)
    command.add_argument("--md-out", type=Path)
    command.add_argument("--quiet", action="store_true")
    command.set_defaults(handler=handle)


def _has_trace_signal(payload: dict[str, Any]) -> bool:
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


def _load_optional_json(path: Path) -> Any:
    return load_json(path) if path.exists() else None


def _load_channel(directory: Path, stem: str) -> list[dict[str, Any]]:
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


def _load_skill_content(directory: Path) -> str:
    for name in ("skill_content.md", "skill.md", "SKILL.md"):
        path = directory / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _payload_from_aime_trace_dir(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        raise ValueError(f"AIME trace directory not found: {directory}")
    metadata = _load_optional_json(directory / "metadata.json") or {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata.json must be a JSON object when provided.")

    trace_metadata = dict(metadata.get("trace_metadata") or {})
    for key in ("aime_session", "aime_assistant", "aime_trace_id", "run_id"):
        if key in metadata and key not in trace_metadata:
            trace_metadata[key] = metadata[key]

    payload: dict[str, Any] = {
        "task": metadata.get("task") or "Imported AIME execution trace.",
        "skill_id": metadata.get("skill_id") or directory.name,
        "skill_version": metadata.get("skill_version") or "unknown",
        "skill_content": metadata.get("skill_content") or _load_skill_content(directory),
        "runtime_events": _load_channel(directory, "runtime_events"),
        "tool_calls": _load_channel(directory, "tool_calls"),
        "model_messages": _load_channel(directory, "model_messages"),
        "trace_metadata": trace_metadata,
    }
    for key in ("condition", "parent_run_id", "repair_enabled", "max_attempts"):
        if key in metadata:
            payload[key] = metadata[key]
    business_result = _load_optional_json(directory / "business_result.json")
    if business_result is not None:
        payload["business_result"] = business_result
    elif "business_result" in metadata:
        payload["business_result"] = metadata["business_result"]
    return payload


def _adapt_aime_trace(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("skill_id"):
        raise ValueError("AIME trace payload is missing required field 'skill_id'.")
    if not _has_trace_signal(payload):
        raise ValueError(
            "AIME trace payload has no trace signal. Provide execution, runtime_events, "
            "tool_calls, model_messages, or trace_metadata."
        )

    adapted = dict(payload)
    metadata = dict(adapted.get("trace_metadata") or {})
    metadata.setdefault("source", "aime_cli_ingest")
    metadata.setdefault("skill_runtime", "aime")
    adapted["trace_metadata"] = metadata
    return adapted


def _load_source_payload(args: Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.trace and args.trace_dir:
        raise ValueError("ingest accepts either a trace JSON path or --trace-dir, not both.")
    if not args.trace and not args.trace_dir:
        raise ValueError("ingest requires a trace JSON path or --trace-dir.")
    if args.trace_dir:
        directory = Path(args.trace_dir).expanduser()
        if args.source == "aime":
            return _payload_from_aime_trace_dir(directory), {
                "input_mode": "trace_dir",
                "trace_dir": str(directory),
            }
        raise ValueError(f"--trace-dir is not supported for source: {args.source}")
    trace_path = Path(args.trace).expanduser()
    return load_json(trace_path), {
        "input_mode": "trace_file",
        "trace_path": str(trace_path),
    }


def _adapt_trace(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    if source == "aime":
        return _adapt_aime_trace(payload)
    raise ValueError(f"unsupported trace source: {source}")


def handle(args: Namespace) -> int:
    payload, source_input = _load_source_payload(args)
    adapted_payload = _adapt_trace(args.source, payload)
    modules = backend_modules(args.project_root)
    request = modules["TraceIngestRequest"].model_validate(adapted_payload)
    state = new_run_service(args.project_root).ingest_trace(request)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "diagnose",
        "ingest": {
            "source": args.source,
            "adapter": f"{args.source}_cli_ingest",
            **source_input,
        },
        "state": state,
    }
    json_path = args.json_out or default_report_path(args.project_root, f"ingest-{args.source}")
    report["report_path"] = str(write_json_report(report, json_path))
    if args.md_out:
        report["markdown_path"] = str(write_markdown_report(report, args.md_out, kind="diagnose"))
        write_json_report(report, json_path)
    if not args.quiet:
        print_run_summary(state, title=f"Skill Doctor Ingest ({args.source})")
        print(f"report: {report['report_path']}")
        if report.get("markdown_path"):
            print(f"markdown: {report['markdown_path']}")
    return EXIT_OK if state.get("status") == "passed" else EXIT_DIAGNOSIS_FAILED
