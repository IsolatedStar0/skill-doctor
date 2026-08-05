from __future__ import annotations

from pathlib import Path
from typing import Any

from ..workspace import dump_json


def write_json_report(report: dict[str, Any], path: str | Path) -> Path:
    return dump_json(report, path)
