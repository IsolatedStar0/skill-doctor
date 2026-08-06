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

__all__ = [
    "push_to_skill_doctor",
    "write_trace_dir",
    "get_skill_content",
    "normalize_business_result",
]

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


# --------------------------------------------------------------------------- #
# Skill content auto-loader                                                    #
# --------------------------------------------------------------------------- #

# Ordered list of files (relative to a skill's directory) that make up the
# canonical "skill content" bundle. ``SKILL.md`` first, then the two
# well-known references, then anything else under ``references/``.
_PRIMARY_SKILL_FILES = (
    "SKILL.md",
    "references/noise-judge-rules.md",
    "references/output-contract.md",
)


def get_skill_content(skill_id: str) -> str:
    """Assemble the full skill body for ``skill_id`` from ``user_skills/``.

    Reads, in order:

    * ``user_skills/<skill_id>/SKILL.md``
    * ``user_skills/<skill_id>/references/noise-judge-rules.md``
    * ``user_skills/<skill_id>/references/output-contract.md``
    * Any remaining ``user_skills/<skill_id>/references/*.md`` (sorted).

    Each existing file is appended to the returned string prefixed by a
    ``# <relative-path>`` heading so DeepSeek can see the section boundaries.
    Missing files are skipped silently. If the skill directory itself does
    not exist (or ``skill_id`` is empty), returns ``""`` — the function
    never raises so the on_finish bridge stays crash-free.
    """

    if not skill_id:
        return ""

    skill_dir = PROJECT_ROOT / "user_skills" / skill_id
    try:
        if not skill_dir.is_dir():
            return ""
    except OSError:
        return ""

    ordered: list[Path] = []
    seen: set[Path] = set()

    for rel in _PRIMARY_SKILL_FILES:
        candidate = skill_dir / rel
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if candidate.is_file() and resolved not in seen:
            ordered.append(candidate)
            seen.add(resolved)

    references_dir = skill_dir / "references"
    if references_dir.is_dir():
        try:
            extras = sorted(references_dir.glob("*.md"))
        except OSError:
            extras = []
        for extra in extras:
            try:
                resolved = extra.resolve()
            except OSError:
                continue
            if extra.is_file() and resolved not in seen:
                ordered.append(extra)
                seen.add(resolved)

    sections: list[str] = []
    for path in ordered:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel_name = path.relative_to(skill_dir).as_posix()
        sections.append(f"# {rel_name}\n\n{body.rstrip()}\n")

    return "\n".join(sections)


def _is_nonempty(value: Any) -> bool:
    """Return True if the trace-signal field carries any actual content."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    return True


def _clean_detail_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    name = str(item.get("name") or "business_result")
    status = item.get("status")
    if status not in {"pass", "fail", "warning"}:
        status = "warning"
    reason = item.get("reason")
    if reason is None:
        reason = ""
    return {"name": name, "status": status, "reason": str(reason)}


def _truncate_text(value: str, limit: int = 200) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_business_result(business_result: Any) -> Any:
    """Normalize arbitrary skill business output into skill-doctor contract.

    If ``business_result`` already matches the backend ``BusinessResult`` shape,
    keep it unchanged. Otherwise, wrap it into a compatible structure and keep
    the original payload under ``extra.raw_business_result`` for later inspection.
    """

    if business_result is None:
        return None

    if isinstance(business_result, Mapping):
        verdict = business_result.get("verdict")
        verdict_type = business_result.get("verdict_type")
        if isinstance(verdict, str) and verdict.strip() and verdict_type in {"pass", "fail", "warning"}:
            normalized = dict(business_result)
            details = normalized.get("details")
            if isinstance(details, list):
                cleaned_details = [detail for item in details if (detail := _clean_detail_item(item)) is not None]
                normalized["details"] = cleaned_details
            elif details is None:
                normalized["details"] = []
            else:
                normalized["details"] = []
            extra = normalized.get("extra")
            normalized["extra"] = dict(extra) if isinstance(extra, Mapping) else {}
            confidence = normalized.get("confidence")
            if not isinstance(confidence, (int, float)):
                normalized["confidence"] = None
            return normalized

        summary_parts: list[str] = []
        for key in ("summary", "message", "answer", "rca_content"):
            value = business_result.get(key)
            if isinstance(value, str) and value.strip():
                summary_parts.append(value.strip().splitlines()[0])
                break
        if not summary_parts:
            try:
                summary_parts.append(json.dumps(business_result, ensure_ascii=False, sort_keys=True))
            except TypeError:
                summary_parts.append(str(dict(business_result)))

        inferred_type = "warning"
        if isinstance(business_result.get("rca_filter"), bool):
            inferred_type = "pass" if business_result.get("rca_filter") else "warning"
        elif isinstance(business_result.get("passed"), bool):
            inferred_type = "pass" if business_result.get("passed") else "fail"
        elif isinstance(business_result.get("success"), bool):
            inferred_type = "pass" if business_result.get("success") else "fail"
        elif isinstance(business_result.get("ok"), bool):
            inferred_type = "pass" if business_result.get("ok") else "fail"

        confidence = business_result.get("confidence")
        normalized_confidence = confidence if isinstance(confidence, (int, float)) else None
        return {
            "verdict": _truncate_text(summary_parts[0]),
            "verdict_type": inferred_type,
            "confidence": normalized_confidence,
            "details": [
                {
                    "name": "business_result",
                    "status": inferred_type,
                    "reason": _truncate_text(summary_parts[0]),
                }
            ],
            "extra": {"raw_business_result": dict(business_result)},
        }

    try:
        summary = json.dumps(business_result, ensure_ascii=False)
    except TypeError:
        summary = str(business_result)

    return {
        "verdict": _truncate_text(summary),
        "verdict_type": "warning",
        "confidence": None,
        "details": [
            {
                "name": "business_result",
                "status": "warning",
                "reason": "auto-wrapped non-dict business_result",
            }
        ],
        "extra": {"raw_business_result": business_result},
    }


# --------------------------------------------------------------------------- #
# Trace-dir recording                                                          #
# --------------------------------------------------------------------------- #

def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_trace_dir(
    trace_dir: str | Path,
    *,
    skill_id: str,
    skill_content: str | None = None,
    runtime_events: Iterable[Mapping[str, Any]] | None = None,
    tool_calls: Iterable[Mapping[str, Any]] | None = None,
    model_messages: Iterable[Mapping[str, Any]] | None = None,
    business_result: Any = None,
    task: str | None = None,
    skill_version: str | None = None,
    trace_metadata: Mapping[str, Any] | None = None,
) -> Path | None:
    """Write an AIME run directory readable by ``skilldoctor ingest``.

    This helper is intentionally stdlib-only and safe for AIME callbacks. It
    records raw execution channels to separate files instead of POSTing to the
    backend, so platform integration can be:

    1. AIME writes ``$AIME_RUN_DIR`` via this function.
    2. A platform post-step runs ``skilldoctor ingest --source aime --trace-dir``.

    Returns the written directory on success. Returns ``None`` and logs to
    stderr on invalid input or I/O errors; it never raises into the skill flow.
    """

    try:
        events_l = list(runtime_events or [])
        tools_l = list(tool_calls or [])
        msgs_l = list(model_messages or [])

        if not skill_id:
            print("[skill-doctor] skip trace-dir: skill_id is required.", file=sys.stderr)
            return None
        if not (_is_nonempty(events_l) or _is_nonempty(tools_l) or _is_nonempty(msgs_l)):
            print(
                "[skill-doctor] skip trace-dir: runtime_events / tool_calls / "
                "model_messages are all empty.",
                file=sys.stderr,
            )
            return None

        if skill_content is None:
            skill_content = get_skill_content(skill_id)
        normalized_business_result = normalize_business_result(business_result)

        target = Path(trace_dir).expanduser()
        target.mkdir(parents=True, exist_ok=True)

        metadata: dict[str, Any] = {
            "skill_id": skill_id,
            "trace_metadata": {
                "source": "aime_trace_dir",
                "skill_runtime": "aime",
            },
        }
        if task:
            metadata["task"] = task
        if skill_version:
            metadata["skill_version"] = skill_version
        if trace_metadata:
            merged_metadata = dict(metadata["trace_metadata"])
            merged_metadata.update(dict(trace_metadata))
            merged_metadata.setdefault("source", "aime_trace_dir")
            merged_metadata.setdefault("skill_runtime", "aime")
            metadata["trace_metadata"] = merged_metadata

        _write_json(target / "metadata.json", metadata)
        if skill_content:
            (target / "skill_content.md").write_text(skill_content, encoding="utf-8")
        if events_l:
            _write_jsonl(target / "runtime_events.jsonl", events_l)
        if tools_l:
            _write_jsonl(target / "tool_calls.jsonl", tools_l)
        if msgs_l:
            _write_jsonl(target / "model_messages.jsonl", msgs_l)
        if normalized_business_result is not None:
            _write_json(target / "business_result.json", normalized_business_result)

        return target
    except Exception as exc:  # never leak into the skill's main flow
        print(f"[skill-doctor] unexpected trace-dir write error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


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
        when checks fail. If left as ``None``, the bridge auto-loads the skill
        body from ``user_skills/<skill_id>/`` via :func:`get_skill_content`.
        Pass ``""`` (empty string) to explicitly opt out of the auto-load.
    runtime_events:
        List of ``{stage, status, message, ...}`` dicts describing skill
        execution stages.
    tool_calls:
        List of ``{name, status, arguments, result, ...}`` dicts.
    model_messages:
        List of ``{role, content, ...}`` dicts (assistant/user/system/tool).
    business_result:
        Optional final skill output — sent as top-level ``business_result`` so
        the backend can surface it directly in the run snapshot/UI.
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

        # Auto-load skill body from user_skills/<skill_id>/ when the caller
        # did not pass one explicitly. An empty string is treated as an
        # explicit opt-out (do NOT auto-fill in that case).
        if skill_content is None:
            skill_content = get_skill_content(skill_id)
        normalized_business_result = normalize_business_result(business_result)

        merged_metadata: dict[str, Any] = {
            "source": "aime_on_finish_hook",
            "skill_runtime": "aime",
        }
        if trace_metadata:
            explicit_metadata = dict(trace_metadata)
            merged_metadata.update(explicit_metadata)
            merged_metadata.setdefault("source", "aime_on_finish_hook")
            merged_metadata.setdefault("skill_runtime", "aime")

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
        if normalized_business_result is not None:
            payload["business_result"] = normalized_business_result
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

    # --- Part 1: exercise get_skill_content() against a real user_skills/ ---
    # Auto-load path. We create a throwaway user_skills/<demo>/ tree, verify
    # the loader returns a non-empty bundle, and clean up afterwards.
    import shutil

    demo_skill_id = "aime-skill-hook-selftest"
    demo_skill_dir = PROJECT_ROOT / "user_skills" / demo_skill_id
    demo_skill_dir_created = not demo_skill_dir.exists()
    try:
        (demo_skill_dir / "references").mkdir(parents=True, exist_ok=True)
        (demo_skill_dir / "SKILL.md").write_text(
            "# demo-skill\nAlways answer with 'pong' when asked to ping.\n",
            encoding="utf-8",
        )
        (demo_skill_dir / "references" / "noise-judge-rules.md").write_text(
            "# noise judge rules\n- ignore transient flakes\n", encoding="utf-8"
        )
        (demo_skill_dir / "references" / "output-contract.md").write_text(
            "# output contract\nReturn strict JSON.\n", encoding="utf-8"
        )
        (demo_skill_dir / "references" / "extra-notes.md").write_text(
            "# extra\nMore context.\n", encoding="utf-8"
        )

        loaded = get_skill_content(demo_skill_id)
        assert loaded, "get_skill_content returned empty for a populated skill dir"
        print(
            f"[demo] get_skill_content('{demo_skill_id}') → "
            f"{len(loaded)} chars, "
            f"sections={loaded.count('# ')}"
        )

        # Also confirm the missing-dir path returns an empty string safely.
        missing = get_skill_content("does-not-exist-xyz")
        assert missing == "", "get_skill_content should return '' for missing dirs"
        print("[demo] get_skill_content('does-not-exist-xyz') → '' (as expected)")
    finally:
        if demo_skill_dir_created and demo_skill_dir.exists():
            shutil.rmtree(demo_skill_dir, ignore_errors=True)
            # Also drop an empty user_skills/ we may have just created.
            parent = demo_skill_dir.parent
            try:
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass

    # --- Part 2: end-to-end bridge push against a local skill-doctor ---
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
        business_result=normalize_business_result({"answer": "pong"}),
        task="ping",
        skill_version="0.0.1",
        trace_metadata={"aime_session": "demo-session", "aime_assistant": "ear-agent"},
    )

    if snapshot is None:
        print("[demo] bridge push failed or was skipped — see stderr above.")
        sys.exit(1)
    sys.exit(0)
