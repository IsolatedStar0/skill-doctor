from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

from .models import RunRequest


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class LangSmithRunExporter:
    """Attach Codex events to LangGraph's single native LangSmith trace."""

    def __init__(self, run_id: str, request: RunRequest) -> None:
        self.run_id = run_id
        self.project = os.getenv("LANGSMITH_PROJECT", "skill-doctor-dev")
        self.enabled = _truthy(os.getenv("LANGSMITH_TRACING"))
        self.status = "disabled"
        self.trace_id: str | None = None
        self.trace_url: str | None = None
        self.error: str | None = None
        self._trace_uuid: UUID | None = None
        self._client: Any | None = None
        self._finished = False

        api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
        if not self.enabled or not api_key:
            self.enabled = False
            return

        try:
            from langsmith import Client
            client_options: dict[str, Any] = {
                "api_key": api_key,
                "api_url": os.getenv(
                    "LANGSMITH_ENDPOINT",
                    "https://api.smith.langchain.com",
                ),
            }
            workspace_id = os.getenv("LANGSMITH_WORKSPACE_ID", "").strip()
            if workspace_id:
                client_options["workspace_id"] = workspace_id
            self._client = Client(**client_options)
            self._trace_uuid = uuid4()
            self.trace_id = str(self._trace_uuid)
            self._request = request
            self.status = "active"
        except Exception as error:  # tracing must never break the agent
            self._degrade(error)

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": "langsmith",
            "enabled": self.enabled,
            "status": self.status,
            "project": self.project,
        }
        if self.trace_id:
            result["trace_id"] = self.trace_id
        if self.trace_url:
            result["trace_url"] = self.trace_url
        if self.error:
            result["error"] = self.error
        return result

    def graph_config(self) -> dict[str, Any]:
        if self._trace_uuid is None:
            return {}
        return {
            "run_id": self._trace_uuid,
            "run_name": "skill-doctor.run",
            "tags": [
                "skill-doctor",
                self._request.executor,
                self._request.skill_id,
            ],
            "metadata": {
                "thread_id": self.run_id,
                "skill_id": self._request.skill_id,
                "skill_version": self._request.skill_version,
                "executor": self._request.executor,
                "scenario": self._request.scenario,
                "ls_agent_type": "root",
            },
        }

    def record_event(
        self,
        event: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> None:
        if (
            self._trace_uuid is None
            or self._finished
            or self.status == "degraded"
        ):
            return
        try:
            from langsmith.run_trees import RunTree

            parent = RunTree.from_runnable_config(config)
            if parent is None:
                raise RuntimeError(
                    "Codex event has no active LangGraph trace parent."
                )
            stage = str(event.get("stage", "unknown"))
            status = str(event.get("status", "completed"))
            child = parent.create_child(
                name=stage,
                run_type=self._run_type(stage),
                inputs={
                    "attempt": event.get("attempt", 0),
                    "source": (
                        "codex-sdk" if stage.startswith("codex.") else "langgraph"
                    ),
                    "metadata": event.get("metadata", {}),
                },
                tags=[stage, status],
                extra={
                    "metadata": {
                        "thread_id": self.run_id,
                        "sequence": event.get("sequence"),
                    }
                },
            )
            output = {
                "status": status,
                "message": event.get("message", ""),
            }
            if event.get("usage") is not None:
                output["usage"] = event["usage"]
                output["usage_metadata"] = self._usage_metadata(
                    event["usage"]
                )
            error = output["message"] if status == "failed" else None
            child.end(outputs=output, error=error)
            child.post()
        except Exception as error:  # tracing must never break the agent
            self._degrade(error)

    def finish(
        self,
        result: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self._trace_uuid is None or self._finished:
            return
        self._finished = True
        if self.status == "degraded":
            return
        try:
            from langchain_core.tracers.langchain import (
                wait_for_all_tracers,
            )

            wait_for_all_tracers()
            self._client.flush(timeout=5)
            run = self._client.read_run(self._trace_uuid)
            self.trace_url = self._client.get_run_url(run=run)
            self.status = "completed" if error is None else "failed"
        except Exception as trace_error:  # tracing must never break the agent
            self._degrade(trace_error)

    def _degrade(self, error: BaseException) -> None:
        self.status = "degraded"
        self.error = f"{type(error).__name__}: {error}"

    @staticmethod
    def _run_type(stage: str) -> str:
        if stage == "codex.turn":
            return "llm"
        if stage in {
            "codex.command_execution",
            "codex.file_change",
            "codex.mcp_tool_call",
            "codex.web_search",
        }:
            return "tool"
        return "chain"

    @staticmethod
    def _usage_metadata(usage: dict[str, Any]) -> dict[str, Any]:
        input_tokens = int(usage.get("input_tokens", 0))
        output_tokens = int(usage.get("output_tokens", 0))
        result: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        cached_tokens = int(usage.get("cached_input_tokens", 0))
        if cached_tokens:
            result["input_token_details"] = {"cache_read": cached_tokens}
        reasoning_tokens = int(usage.get("reasoning_tokens", 0))
        if reasoning_tokens:
            result["output_token_details"] = {
                "reasoning": reasoning_tokens
            }
        return result

def create_observability_exporter(
    run_id: str,
    request: RunRequest,
) -> LangSmithRunExporter:
    return LangSmithRunExporter(run_id, request)
