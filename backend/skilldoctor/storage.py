from __future__ import annotations

import json
from abc import ABC, abstractmethod
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
        return str(Path(path).relative_to(self.project_root))

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
