from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def default_project_root() -> Path:
    """Resolve the parent Skill Doctor repo root.

    The CLI is intentionally a thin product layer beside the existing backend,
    so the default root is the parent of ``skilldoctor-cli``. Users can override
    this with ``--project-root`` or ``SKILL_DOCTOR_PROJECT_ROOT``.
    """

    override = os.getenv("SKILL_DOCTOR_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def add_backend_to_path(project_root: Path) -> None:
    root = str(project_root.resolve())
    backend = str((project_root / "backend").resolve())
    for candidate in (root, backend):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: str | Path) -> Any:
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: str | Path) -> list[Any]:
    records: list[Any] = []
    with Path(path).expanduser().open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                records.append(json.loads(text))
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSONL at line {line_number}: {error}") from error
    return records


def dump_json(data: Any, path: str | Path) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def default_report_path(project_root: Path, prefix: str, suffix: str = "json") -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return project_root / "reports" / "cli" / f"{prefix}-{stamp}.{suffix}"


def baseline_reports_dir(project_root: Path) -> Path:
    return project_root / ".skilldoctor" / "baselines"


def baseline_report_path(project_root: Path, name: str) -> Path:
    if not name or not name.strip():
        raise ValueError("baseline name must be a non-empty string.")
    baseline_name = name.strip()
    if Path(baseline_name).name != baseline_name:
        raise ValueError("baseline name must not contain path separators.")
    return baseline_reports_dir(project_root) / f"{baseline_name}.json"


def first_existing_path(paths: Iterable[str | Path | None]) -> Path | None:
    for raw in paths:
        if raw is None:
            continue
        path = Path(raw).expanduser()
        if path.exists():
            return path.resolve()
    return None
