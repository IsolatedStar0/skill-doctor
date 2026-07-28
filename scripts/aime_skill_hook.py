#!/usr/bin/env python3
"""Aime skill on_finish hook: push execution trace to skill-doctor.

This module is the **in-process** counterpart of ``push_aime_trace.py``. Where
``push_aime_trace.py`` is a CLI that reads a pre-built JSON file, this module
is designed to be imported and called directly from an Aime skill's
``on_finish`` callback (方案 A: HTTP POST bridge).

Design goals
------------
* stdlib-only (``urllib.request`` / ``json`` / ``os``) — safe to run inside
  the Aime sandbox with no extra dependencies.
* Never raise into the caller. A failed bridge push must not crash the skill.
* Refuse empty traces (all of ``runtime_events`` / ``tool_calls`` /
  ``model_messages`` empty) — empty payloads short-circuit skill-doctor and
  skip DeepSeek, so pushing them wastes a run_id.
* Automatically pick up endpoint / api-key from ``.env`` if the caller does
  not pass them explicitly.

Payload shape follows ``TraceIngestRequest`` in
``backend/skilldoctor/models.py``.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

__all__ = ["push_to_skill_doctor"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENDPOINT = "http://127.0.0.1:8010"


# --------------------------------------------------------------------------- #
# .env / config helpers                                                       #
# --------------------------------------------------------------------------- #

def _load_dotenv(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return result


def _resolve(cli_value: str | None, env_key: str, default: str | None = None) -> str | None:
    if cli_value:
        return cli_value
    env_value = os.getenv(env_key)
    if env_value:
        return env_value
    dotenv = _load_dotenv(PROJECT_ROOT / ".env")
    return dotenv.get(env_key) or default


def _is_nonempty(value: Any) -> bool:
    """Return True if the trace-signal field carries any actual content."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    return True


# --------------------------------------------------------------------------- #
# HTTP                                                                        #
# --------------------------------------------------------------------------- #

def _post(endpoint: str, api_key: str | None, payload: dict[str, Any], timeout: float) -> tuple[int, str]:
    url = endpoint.rstrip("/") + "/traces"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8")


def _summarise(body: str) -> dict[str, Any] | None:
    """Pretty-print snapshot and return the parsed dict (or None)."""
    try:
        snapshot = json.loads(body)
    except json.JSONDecodeError:
        print(f"[skill-doctor] non-JSON response: {body[:400]}")
        return None

    run_id = snapshot.get("run_id")
    status = snapshot.get("status")
    attribution = snapshot.get("attribution") or {}
    source = attribution.get("agent_source")
    print(
        f"[skill-doctor] pushed ok — run_id={run_id} status={status} "
        f"attribution.source={source} taxonomy={attribution.get('taxonomy')}"
    )
    if source == "llm":
        conclusion = attribution.get("agent_conclusion")
        if conclusion:
            print(f"[skill-doctor] 🤖 {conclusion}")
    return snapshot


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #

def push_to_skill_doctor(
    skill_id: str,
    skill_content: str | None = None,
    runtime_events: Iterable[Mapping[str, Any]] | None = None,
    tool_calls: Iterable[Mapping[str, Any]] | None = None,
    model_messages: Iterable[Mapping[str, Any]] | None = None,
    business_result: Any = None,
    *,
    task: str | None = None,
    skill_version: str | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
    endpoint: str | None = None,
    api_key: str | None = None,
    timeout: float = 180.0,
) -> dict[str, Any] | None:
    """Package an Aime skill trace and POST it to skill-doctor.

    This is the function to wire into your skill's ``on_finish`` callback.

    Parameters
    ----------
    skill_id:
        Aime skill identifier (required — skill-doctor keys everything by it).
    skill_content:
        The raw skill body / prompt. Enables DeepSeek to reason over the skill
        when checks fail. Optional but strongly recommended.
    runtime_events:
        List of ``{stage, status, message, ...}`` dicts describing skill
        execution stages.
    tool_calls:
        List of ``{name, status, arguments, result, ...}`` dicts.
    model_messages:
        List of ``{role, content, ...}`` dicts (assistant/user/system/tool).
    business_result:
        Optional final skill output — attached under ``trace_metadata`` for
        downstream inspection.
    task:
        The user query that triggered the skill.
    skill_version, trace_metadata:
        Passed through to skill-doctor unchanged.
    endpoint, api_key:
        Override defaults (else read from env / ``.env``).

    Returns
    -------
    Parsed snapshot dict on success, or ``None`` on any failure.
    The function NEVER raises — a bridge failure must not break the skill.
    """

    try:
        events_l = list(runtime_events or [])
        tools_l = list(tool_calls or [])
        msgs_l = list(model_messages or [])

        if not (_is_nonempty(events_l) or _is_nonempty(tools_l) or _is_nonempty(msgs_l)):
            print(
                "[skill-doctor] skip: runtime_events / tool_calls / "
                "model_messages are all empty — nothing to diagnose.",
                file=sys.stderr,
            )
            return None

        if not skill_id:
            print("[skill-doctor] skip: skill_id is required.", file=sys.stderr)
            return None

        merged_metadata: dict[str, Any] = {}
        if trace_metadata:
            merged_metadata.update(dict(trace_metadata))
        if business_result is not None and "business_result" not in merged_metadata:
            merged_metadata["business_result"] = business_result

        payload: dict[str, Any] = {
            "skill_id": skill_id,
            "runtime_events": events_l,
            "tool_calls": tools_l,
            "model_messages": msgs_l,
        }
        if task:
            payload["task"] = task
        if skill_content:
            payload["skill_content"] = skill_content
        if skill_version:
            payload["skill_version"] = skill_version
        if merged_metadata:
            payload["trace_metadata"] = merged_metadata

        ep = _resolve(endpoint, "SKILL_DOCTOR_ENDPOINT", DEFAULT_ENDPOINT) or DEFAULT_ENDPOINT
        key = _resolve(api_key, "SKILL_DOCTOR_INGEST_API_KEY")

        if not key:
            print(
                "[skill-doctor] warning: no SKILL_DOCTOR_INGEST_API_KEY — "
                "request will fail if the server enforces auth.",
                file=sys.stderr,
            )

        status, body = _post(ep, key, payload, timeout=timeout)
        if status >= 400:
            print(f"[skill-doctor] HTTP {status}: {body[:400]}", file=sys.stderr)
            return None
        return _summarise(body)

    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        print(f"[skill-doctor] HTTP {exc.code} error: {err_body[:400]}", file=sys.stderr)
        return None
    except urllib.error.URLError as exc:
        print(f"[skill-doctor] cannot reach backend: {exc}", file=sys.stderr)
        return None
    except Exception as exc:  # never leak into the skill's main flow
        print(f"[skill-doctor] unexpected bridge error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# Demo / smoke run                                                            #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Minimal end-to-end example showing how an Aime skill's on_finish
    # callback would wire the bridge. Run:
    #
    #   python3 scripts/aime_skill_hook.py
    #
    # Requires a local skill-doctor backend on http://127.0.0.1:8010 with
    # SKILL_DOCTOR_INGEST_API_KEY exported (or set in ../.env).

    demo_skill_content = (
        "# demo-skill\n"
        "Always answer with the string 'pong' when asked to ping.\n"
    )

    demo_runtime_events = [
        {"stage": "load_skill", "status": "completed", "message": "skill loaded"},
        {"stage": "call_model", "status": "completed", "message": "model responded"},
        {"stage": "finalize",   "status": "completed", "message": "returned result"},
    ]

    demo_tool_calls = [
        {
            "name": "search",
            "status": "completed",
            "arguments": {"query": "ping"},
            "result": {"hits": 0},
        }
    ]

    demo_model_messages = [
        {"role": "system",    "content": demo_skill_content},
        {"role": "user",      "content": "ping"},
        {"role": "assistant", "content": "pong"},
    ]

    snapshot = push_to_skill_doctor(
        skill_id="demo-skill",
        skill_content=demo_skill_content,
        runtime_events=demo_runtime_events,
        tool_calls=demo_tool_calls,
        model_messages=demo_model_messages,
        business_result={"answer": "pong"},
        task="ping",
        skill_version="0.0.1",
        trace_metadata={"aime_session": "demo-session", "aime_assistant": "ear-agent"},
    )

    if snapshot is None:
        print("[demo] bridge push failed or was skipped — see stderr above.")
        sys.exit(1)
    sys.exit(0)
