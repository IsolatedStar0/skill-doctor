from __future__ import annotations

import json
import os
import sqlite3
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StorageBackend(ABC):
    """Persistence boundary for Skill Doctor runtime data.

    The first implementation is filesystem-backed JSON so current behavior stays
    unchanged. Future SQLite/Postgres backends can implement the same methods
    without leaking path reads/writes back into services.
    """

    @abstractmethod
    def save_run(self, run: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_run(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def list_run_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_benchmark_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError

    def run_artifact_uri(self, run_id: str) -> str:
        """Return the durable evidence reference for a persisted run."""

        raise NotImplementedError

    @abstractmethod
    def save_benchmark(self, benchmark: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save_diagnostic_case(self, case_id: str, case: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_diagnostic_cases(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def save_candidate_skill(self, candidate_id: str, candidate: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def get_candidate_skill(self, candidate_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def save_rejection_memory(self, rejection_id: str, record: dict[str, Any]) -> str:
        raise NotImplementedError

    @abstractmethod
    def list_rejection_memory(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @staticmethod
    def snapshot(payload: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON-serializable deep copy for publishing."""

        return json.loads(json.dumps(payload))


class FileStorageBackend(StorageBackend):
    """JSON-file storage backend compatible with the original local layout."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.run_directory = self.project_root / "reports" / "langgraph"
        self.benchmark_directory = self.project_root / "reports" / "benchmarks"
        self.diagnostic_case_directory = self.project_root / "diagnostic_cases"
        self.candidate_skill_directory = self.project_root / "candidate_skills"
        self.rejection_memory_directory = self.project_root / "rejection_memory"

    @property
    def registry_directory(self) -> Path:
        return self.run_directory / ".registry"

    def save_run(self, run: dict[str, Any]) -> str:
        return self._write_json(self.run_directory / f"{run['run_id']}.json", run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._read_json(self.run_directory / f"{run_id}.json")

    def list_run_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        records: list[tuple[int, dict[str, Any]]] = []
        for directory in (self.run_directory, self.benchmark_directory):
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                try:
                    state = self._read_json(path)
                    updated_at = _datetime_from_ns(path.stat().st_mtime_ns)
                    records.append(
                        (
                            path.stat().st_mtime_ns,
                            _run_summary_from_state(state, updated_at),
                        )
                    )
                except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    continue
        records.sort(key=lambda item: item[0], reverse=True)
        return [summary for _, summary in records[:limit]]

    def list_benchmark_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        records: list[tuple[int, dict[str, Any]]] = []
        if not self.benchmark_directory.is_dir():
            return []
        for path in self.benchmark_directory.glob("*.json"):
            try:
                state = self._read_json(path)
                updated_at = _datetime_from_ns(path.stat().st_mtime_ns)
                records.append(
                    (
                        path.stat().st_mtime_ns,
                        _run_summary_from_state(state, updated_at),
                    )
                )
            except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        records.sort(key=lambda item: item[0], reverse=True)
        return [summary for _, summary in records[:limit]]

    def run_artifact_uri(self, run_id: str) -> str:
        return self.relative_path(self.run_directory / f"{run_id}.json").replace("\\", "/")

    def save_benchmark(self, benchmark: dict[str, Any]) -> str:
        return self._write_json(
            self.benchmark_directory / f"{benchmark['run_id']}.json",
            benchmark,
        )

    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        return self._read_json(self.benchmark_directory / f"{benchmark_id}.json")

    def save_diagnostic_case(self, case_id: str, case: dict[str, Any]) -> str:
        return self._write_json(self.diagnostic_case_directory / f"{case_id}.json", case)

    def list_diagnostic_cases(self) -> list[dict[str, Any]]:
        return self._list_json(self.diagnostic_case_directory)

    def save_candidate_skill(self, candidate_id: str, candidate: dict[str, Any]) -> str:
        return self._write_json(
            self.candidate_skill_directory / f"{candidate_id}.json",
            candidate,
        )

    def get_candidate_skill(self, candidate_id: str) -> dict[str, Any]:
        return self._read_json(self.candidate_skill_directory / f"{candidate_id}.json")

    def save_rejection_memory(self, rejection_id: str, record: dict[str, Any]) -> str:
        return self._write_json(
            self.rejection_memory_directory / f"{rejection_id}.json",
            record,
        )

    def list_rejection_memory(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        records = self._list_json(self.rejection_memory_directory)
        if skill_id is None:
            return records
        return [record for record in records if record.get("skill_id") == skill_id]

    def relative_path(self, path: str | Path) -> str:
        try:
            return str(Path(path).relative_to(self.project_root))
        except ValueError:
            return str(Path(path))

    def _write_json(self, path: Path, payload: dict[str, Any]) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        return self.relative_path(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path.stem)
        return json.loads(path.read_text(encoding="utf-8"))

    def _list_json(self, directory: Path) -> list[dict[str, Any]]:
        if not directory.is_dir():
            return []
        return [self._read_json(path) for path in sorted(directory.glob("*.json"))]


class SQLiteStorageBackend(StorageBackend):
    """SQLite storage backend that keeps indexed metadata plus full JSON payloads."""

    def __init__(self, project_root: Path, database_path: str | Path | None = None) -> None:
        self.project_root = project_root.resolve()
        configured_path = database_path or self.project_root / "reports" / "skill-doctor.sqlite3"
        self.database_path = Path(configured_path)
        if not self.database_path.is_absolute():
            self.database_path = self.project_root / self.database_path
        self.run_directory = self.project_root / "reports" / "langgraph"
        self.benchmark_directory = self.project_root / "reports" / "benchmarks"
        self.diagnostic_case_directory = self.project_root / "diagnostic_cases"
        self.candidate_skill_directory = self.project_root / "candidate_skills"
        self.rejection_memory_directory = self.project_root / "rejection_memory"
        self._ensure_schema()

    @property
    def registry_directory(self) -> Path:
        return self.run_directory / ".registry"

    def save_run(self, run: dict[str, Any]) -> str:
        run_id = str(run["run_id"])
        self._upsert_payload(
            "runs",
            "run_id",
            run_id,
            run,
            {
                "run_kind": run.get("run_kind", "agent"),
                "skill_id": run.get("skill_id", ""),
                "status": run.get("status", ""),
                "updated_at": self._timestamp(run),
            },
        )
        return self._uri("runs", run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._get_payload("runs", "run_id", run_id)

    def list_run_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        rows: list[tuple[str, str]] = []
        with self._connect() as connection:
            rows.extend(
                connection.execute(
                    "SELECT updated_at, payload FROM runs ORDER BY updated_at DESC, run_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
            rows.extend(
                connection.execute(
                    "SELECT updated_at, payload FROM benchmarks ORDER BY updated_at DESC, benchmark_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            )
        rows.sort(key=lambda item: item[0], reverse=True)
        summaries: list[dict[str, Any]] = []
        for updated_at, payload in rows[:limit]:
            try:
                summaries.append(_run_summary_from_state(json.loads(payload), updated_at))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return summaries

    def list_benchmark_summaries(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT updated_at, payload FROM benchmarks ORDER BY updated_at DESC, benchmark_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        for updated_at, payload in rows:
            try:
                summaries.append(_run_summary_from_state(json.loads(payload), updated_at))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return summaries

    def run_artifact_uri(self, run_id: str) -> str:
        return self._uri("runs", run_id)

    def save_benchmark(self, benchmark: dict[str, Any]) -> str:
        benchmark_id = str(benchmark["run_id"])
        self._upsert_payload(
            "benchmarks",
            "benchmark_id",
            benchmark_id,
            benchmark,
            {
                "skill_id": benchmark.get("skill_id", ""),
                "status": benchmark.get("status", ""),
                "updated_at": self._timestamp(benchmark),
            },
        )
        return self._uri("benchmarks", benchmark_id)

    def get_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        return self._get_payload("benchmarks", "benchmark_id", benchmark_id)

    def save_diagnostic_case(self, case_id: str, case: dict[str, Any]) -> str:
        trace_metadata = ((case.get("trace") or {}).get("trace_metadata") or {})
        self._upsert_payload(
            "diagnostic_cases",
            "case_id",
            case_id,
            case,
            {
                "source": case.get("source", ""),
                "created_at": trace_metadata.get("saved_at") or self._timestamp(case),
            },
        )
        return self._uri("diagnostic_cases", case_id)

    def list_diagnostic_cases(self) -> list[dict[str, Any]]:
        return self._list_payloads("diagnostic_cases", "created_at, case_id")

    def save_candidate_skill(self, candidate_id: str, candidate: dict[str, Any]) -> str:
        self._upsert_payload(
            "candidate_skills",
            "candidate_id",
            candidate_id,
            candidate,
            {
                "skill_id": candidate.get("skill_id", ""),
                "created_at": candidate.get("created_at") or self._timestamp(candidate),
            },
        )
        return self._uri("candidate_skills", candidate_id)

    def get_candidate_skill(self, candidate_id: str) -> dict[str, Any]:
        return self._get_payload("candidate_skills", "candidate_id", candidate_id)

    def save_rejection_memory(self, rejection_id: str, record: dict[str, Any]) -> str:
        self._upsert_payload(
            "rejection_memory",
            "rejection_id",
            rejection_id,
            record,
            {
                "skill_id": record.get("skill_id", ""),
                "fault_type": record.get("fault_type", ""),
                "action": record.get("action", ""),
                "patch_sha256": record.get("patch_sha256", ""),
                "created_at": record.get("created_at") or self._timestamp(record),
            },
        )
        return self._uri("rejection_memory", rejection_id)

    def list_rejection_memory(self, skill_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM rejection_memory"
        parameters: tuple[Any, ...] = ()
        if skill_id is not None:
            sql += " WHERE skill_id = ?"
            parameters = (skill_id,)
        sql += " ORDER BY created_at DESC, rejection_id DESC"
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(sql, parameters)]

    def relative_path(self, path: str | Path) -> str:
        try:
            return str(Path(path).relative_to(self.project_root))
        except ValueError:
            return str(Path(path))

    def _ensure_schema(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    run_kind TEXT NOT NULL DEFAULT 'agent',
                    skill_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runs_skill_updated
                    ON runs(skill_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS benchmarks (
                    benchmark_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_benchmarks_skill_updated
                    ON benchmarks(skill_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS diagnostic_cases (
                    case_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_diagnostic_cases_source_created
                    ON diagnostic_cases(source, created_at DESC);

                CREATE TABLE IF NOT EXISTS candidate_skills (
                    candidate_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_skills_skill_created
                    ON candidate_skills(skill_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS rejection_memory (
                    rejection_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL DEFAULT '',
                    fault_type TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    patch_sha256 TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rejection_memory_skill_created
                    ON rejection_memory(skill_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rejection_memory_patch
                    ON rejection_memory(patch_sha256);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _upsert_payload(
        self,
        table: str,
        primary_key: str,
        primary_value: str,
        payload: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        columns = [primary_key, *metadata.keys(), "payload"]
        values = [
            primary_value,
            *[str(value or "") for value in metadata.values()],
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
        ]
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in columns
            if column != primary_key
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({primary_key}) DO UPDATE SET {updates}"
        )
        with self._connect() as connection:
            connection.execute(sql, values)

    def _get_payload(self, table: str, primary_key: str, primary_value: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload FROM {table} WHERE {primary_key} = ?",
                (primary_value,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError(primary_value)
        return json.loads(row[0])

    def _list_payloads(self, table: str, order_by: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(f"SELECT payload FROM {table} ORDER BY {order_by}")
            ]

    def _uri(self, table: str, record_id: str) -> str:
        return f"sqlite://{self.relative_path(self.database_path)}#{table}/{record_id}"

    @staticmethod
    def _timestamp(payload: dict[str, Any]) -> str:
        return str(
            payload.get("updated_at")
            or payload.get("created_at")
            or payload.get("generated_at")
            or _now()
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _datetime_from_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, UTC).isoformat()


def _run_summary_from_state(state: dict[str, Any], updated_at: str) -> dict[str, Any]:
    return {
        "run_kind": state.get("run_kind", "agent"),
        "run_id": state["run_id"],
        "parent_run_id": state.get("parent_run_id"),
        "skill_id": state.get("skill_id", ""),
        "skill_version": state.get("skill_version", ""),
        "executor": state.get("executor", ""),
        "scenario": state.get("scenario", ""),
        "condition": state.get("condition", "standard"),
        "attempt": state.get("attempt", 0),
        "max_attempts": state.get("max_attempts", 0),
        "status": state["status"],
        "stop_reason": state.get("stop_reason", ""),
        "event_count": len(state.get("events", [])),
        "updated_at": updated_at,
    }


def build_storage_backend(project_root: Path) -> StorageBackend:
    """Build the configured storage backend.

    Defaults to file-backed JSON for compatibility. Set
    SKILL_DOCTOR_STORAGE_BACKEND=sqlite to persist runtime assets in SQLite.
    """

    backend = os.getenv("SKILL_DOCTOR_STORAGE_BACKEND", "file").strip().lower()
    if backend in {"", "file", "json", "filesystem"}:
        return FileStorageBackend(project_root)
    if backend == "sqlite":
        return SQLiteStorageBackend(
            project_root,
            os.getenv("SKILL_DOCTOR_SQLITE_PATH"),
        )
    raise ValueError(
        "Unsupported SKILL_DOCTOR_STORAGE_BACKEND "
        f"{backend!r}; expected 'file' or 'sqlite'."
    )
