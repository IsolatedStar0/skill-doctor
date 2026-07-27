#!/usr/bin/env python3
"""Bridge: package an Aime skill execution trace and POST it to skill-doctor.

Usage examples
--------------

  # 1) Push a hand-crafted / pre-built trace payload:
  python3 scripts/push_aime_trace.py --from-file examples/traces/puck-rule-rca-live.json

  # 2) Override endpoint / token via CLI (defaults are read from .env or env):
  python3 scripts/push_aime_trace.py \
      --from-file examples/traces/puck-rule-rca-live.json \
      --endpoint http://127.0.0.1:8010 \
      --api-key "$SKILL_DOCTOR_INGEST_API_KEY"

The script is deliberately dependency-free (stdlib only) so it can run from
any Python 3.10+ environment, including inside CI or the Aime sandbox.

Wiring into an Aime workflow
----------------------------

After each Aime skill run you want to diagnose, save the trace to a JSON
file that matches ``TraceIngestRequest`` in
``backend/skilldoctor/models.py`` and invoke this script. A minimal shape
looks like::

    {
      "task": "<user query>",
      "skill_id": "<aime skill id>",
      "skill_version": "1.0.0",
      "skill_content": "<the skill body, so DeepSeek can reason over it>",
      "runtime_events":  [{"stage": "...", "status": "completed", "message": "..."}],
      "tool_calls":      [{"name": "...", "status": "completed", "arguments": {...}}],
      "model_messages":  [{"role": "assistant|user|system|tool", "content": "..."}],
      "trace_metadata":  {"aime_session": "...", "aime_assistant": "..."}
    }

DeepSeek is triggered whenever the skill-doctor backend detects at least
one failing check in the resulting synthesized ExecutionResult. See the
``UploadedTraceWorker`` in ``backend/skilldoctor/workers.py`` for the
exact heuristics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> dict[str, str]:
    """Very small ``.env`` reader; keeps runtime dependency-free."""

    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _resolve_api_key(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value
    env_value = os.getenv("SKILL_DOCTOR_INGEST_API_KEY")
    if env_value:
        return env_value
    dotenv = _load_dotenv(PROJECT_ROOT / ".env")
    return dotenv.get("SKILL_DOCTOR_INGEST_API_KEY") or None


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"trace file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"trace file is not valid JSON: {path} ({exc})")


def _validate(payload: dict[str, Any]) -> None:
    if not payload.get("skill_id"):
        raise SystemExit("payload is missing required field 'skill_id'.")
    if not any(
        payload.get(key)
        for key in (
            "runtime_events",
            "tool_calls",
            "model_messages",
            "trace_metadata",
            "execution",
        )
    ):
        raise SystemExit(
            "payload has no trace signal. Provide at least one of: "
            "runtime_events / tool_calls / model_messages / trace_metadata / "
            "execution. Empty payloads short-circuit skill-doctor and skip DeepSeek."
        )


def _post(endpoint: str, api_key: str | None, payload: dict[str, Any]) -> str:
    url = endpoint.rstrip("/") + "/traces"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(
            f"HTTP {exc.code} while POST {url}\n{error_body}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    except urllib.error.URLError as exc:
        print(f"cannot reach {url}: {exc}", file=sys.stderr)
        raise SystemExit(3)


def _summarise(body: str) -> None:
    """Pretty-print the important fields of the returned snapshot."""

    try:
        snapshot = json.loads(body)
    except json.JSONDecodeError:
        print(body)
        return
    print("=== skill-doctor snapshot ===")
    print(f"run_id     : {snapshot.get('run_id')}")
    print(f"skill_id   : {snapshot.get('skill_id')}")
    print(f"status     : {snapshot.get('status')}")
    print(f"stop_reason: {snapshot.get('stop_reason')}")

    execution = snapshot.get("execution") or {}
    print(
        "execution  : passed=%s pass_rate=%s runtime_events=%d assertions=%d"
        % (
            execution.get("passed"),
            execution.get("pass_rate"),
            len(execution.get("runtime_events") or []),
            len(execution.get("assertions") or []),
        )
    )

    attribution = snapshot.get("attribution") or {}
    print(
        "attribution: taxonomy=%s cause=%s source=%s fault_type=%s"
        % (
            attribution.get("taxonomy"),
            attribution.get("cause"),
            attribution.get("agent_source"),
            attribution.get("fault_type"),
        )
    )
    agent_source = attribution.get("agent_source")
    conclusion = attribution.get("agent_conclusion") if agent_source == "llm" else None
    reason = attribution.get("agent_reason") if agent_source == "llm" else None
    if conclusion:
        print("--- 🤖 AI 归因结论 ---")
        print(conclusion)
    if reason:
        print("--- 归因理由 ---")
        print(reason)
    if not conclusion:
        fallback = attribution.get("improvement_principle")
        if fallback:
            print("--- 规则化归因摘要 ---")
            print(fallback)
        print(
            "note: attribution has no LLM-authored conclusion — verify that\n"
            "      DEEPSEEK_API_KEY is exported to the uvicorn process and\n"
            "      that the trace produced at least one failing check."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-file",
        required=True,
        type=Path,
        help="Path to a JSON file matching TraceIngestRequest.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("SKILL_DOCTOR_ENDPOINT", "http://127.0.0.1:8010"),
        help="skill-doctor backend base URL (default: http://127.0.0.1:8010).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            "Ingest bearer token. Falls back to SKILL_DOCTOR_INGEST_API_KEY "
            "or the value in ../.env."
        ),
    )
    args = parser.parse_args(argv)

    payload = _load_payload(args.from_file)
    _validate(payload)
    api_key = _resolve_api_key(args.api_key)
    if not api_key:
        print(
            "warning: no ingest token found — request will be rejected if\n"
            "         SKILL_DOCTOR_INGEST_API_KEY is set on the server.",
            file=sys.stderr,
        )

    body = _post(args.endpoint, api_key, payload)
    _summarise(body)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
