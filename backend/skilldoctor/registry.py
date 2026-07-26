from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4


class RunRegistry:
    """Cross-process registry backed by atomic JSON snapshot files."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def publish(self, state: dict[str, Any]) -> dict[str, Any]:
        self.directory.mkdir(parents=True, exist_ok=True)
        updated_at = datetime.now(UTC).isoformat()
        envelope = {
            "type": "run.updated",
            "updated_at": updated_at,
            "state": state,
        }
        target = self._path(state["run_id"])
        temporary = self.directory / (
            f".{state['run_id']}.{uuid4().hex}.tmp"
        )
        temporary.write_text(
            f"{json.dumps(envelope, ensure_ascii=False)}\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return envelope

    def get(self, run_id: str) -> dict[str, Any]:
        envelope = self._read(self._path(run_id))
        return envelope["state"]

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        records: list[tuple[int, dict[str, Any]]] = []
        for path in self.directory.glob("lg-*.json"):
            try:
                envelope = self._read(path)
                state = envelope["state"]
                records.append(
                    (
                        path.stat().st_mtime_ns,
                        {
                            "run_id": state["run_id"],
                            "skill_id": state["skill_id"],
                            "skill_version": state["skill_version"],
                            "executor": state["executor"],
                            "scenario": state["scenario"],
                            "attempt": state["attempt"],
                            "max_attempts": state["max_attempts"],
                            "status": state["status"],
                            "stop_reason": state["stop_reason"],
                            "event_count": len(state.get("events", [])),
                            "updated_at": envelope["updated_at"],
                        },
                    )
                )
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        records.sort(key=lambda item: item[0], reverse=True)
        return [summary for _, summary in records[:limit]]

    def events(
        self,
        *,
        poll_interval: float = 0.2,
        heartbeat_seconds: float = 15,
    ) -> Iterator[dict[str, Any] | None]:
        observed: dict[Path, tuple[int, int]] = {}
        last_heartbeat = time.monotonic()
        while True:
            emitted = False
            if self.directory.is_dir():
                paths = sorted(
                    self.directory.glob("lg-*.json"),
                    key=lambda path: path.stat().st_mtime_ns,
                )
                for path in paths:
                    try:
                        stat = path.stat()
                        fingerprint = (stat.st_mtime_ns, stat.st_size)
                        if observed.get(path) == fingerprint:
                            continue
                        envelope = self._read(path)
                    except (
                        OSError,
                        TypeError,
                        ValueError,
                        json.JSONDecodeError,
                    ):
                        continue
                    observed[path] = fingerprint
                    emitted = True
                    yield envelope
            now = time.monotonic()
            if not emitted and now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                yield None
            time.sleep(poll_interval)

    def _path(self, run_id: str) -> Path:
        if not run_id.startswith("lg-") or not run_id[3:].isalnum():
            raise ValueError("Invalid run id.")
        return self.directory / f"{run_id}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path.stem)
        return json.loads(path.read_text(encoding="utf-8"))
