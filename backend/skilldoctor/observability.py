from __future__ import annotations

import os
from typing import Any

from .models import RunRequest


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class LangSmithRunExporter:
    """Best-effort LangSmith mirror for the local source-of-truth trace."""

    def __init__(self, run_id: str, request: RunRequest) -> None:
        self.run_id = run_id
        self.project = os.getenv("LANGSMITH_PROJECT", "skill-doctor-dev")
        self.enabled = _truthy(os.getenv("LANGSMITH_TRACING"))
        self.status = "disabled"
        self.trace_id: str | None = None
        self.trace_url: str | None = None
        self.error: str | None = None
        self._root: Any | None = None
        self._finished = False

        api_key = os.getenv("LANGSMITH_API_KEY", "").strip()
        if not self.enabled or not api_key:
            self.enabled = False
            return

        try:
            from langsmith import Client
            from langsmith.run_trees import RunTree

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
            client = Client(**client_options)
            self._root = RunTree(
                name="skill-doctor.run",
                run_type="chain",
                inputs={
                    "run_id": run_id,
                    "task": request.task,
                    "skill_id": request.skill_id,
                    "skill_version": request.skill_version,
                    "executor": request.executor,
                    "scenario": request.scenario,
                },
                project_name=self.project,
                tags=["skill-doctor", request.executor, request.skill_id],
                extra={
                    "metadata": {
                        "thread_id": run_id,
                        "skill_id": request.skill_id,
                        "executor": request.executor,
                    }
                },
                ls_client=client,
            )
            self.trace_id = str(self._root.trace_id)
            self._root.post()
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

    def record_event(self, event: dict[str, Any]) -> None:
        if self._root is None or self._finished or self.status == "degraded":
            return
        try:
            stage = str(event.get("stage", "unknown"))
            status = str(event.get("status", "completed"))
            child = self._root.create_child(
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
        if self._root is None or self._finished:
            return
        self._finished = True
        if self.status == "degraded":
            return
        try:
            outputs = self._summary(result) if result is not None else None
            self._root.end(
                outputs=outputs,
                error=str(error) if error is not None else None,
            )
            self._root.patch()
            self.trace_url = self._root.get_url()
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

    @staticmethod
    def _summary(result: dict[str, Any]) -> dict[str, Any]:
        execution = result.get("execution", {})
        verification = result.get("verification", {})
        return {
            "status": result.get("status"),
            "stop_reason": result.get("stop_reason"),
            "attempt": result.get("attempt"),
            "pass_rate": execution.get("pass_rate"),
            "duration_ms": execution.get("duration_ms"),
            "usage": execution.get("usage"),
            "verification": {
                "decision": verification.get("decision"),
                "pass_rate_delta": verification.get("pass_rate_delta"),
                "regression_rate": verification.get("regression_rate"),
            },
        }


def create_observability_exporter(
    run_id: str,
    request: RunRequest,
) -> LangSmithRunExporter:
    return LangSmithRunExporter(run_id, request)
