from __future__ import annotations

from pathlib import Path
from typing import Any

from .workspace import add_backend_to_path


def backend_modules(project_root: Path) -> dict[str, Any]:
    """Import backend classes after making the repo importable."""

    add_backend_to_path(project_root)
    try:
        from backend.skilldoctor.benchmark import BenchmarkService
        from backend.skilldoctor.models import (
            BenchmarkRequest,
            DiagnosticCaseRequest,
            DiagnosticSuiteRequest,
            TraceIngestRequest,
        )
        from backend.skilldoctor.service import RunService
    except ModuleNotFoundError:
        from skilldoctor.benchmark import BenchmarkService
        from skilldoctor.models import (  # type: ignore[no-redef]
            BenchmarkRequest,
            DiagnosticCaseRequest,
            DiagnosticSuiteRequest,
            TraceIngestRequest,
        )
        from skilldoctor.service import RunService  # type: ignore[no-redef]

    return {
        "BenchmarkRequest": BenchmarkRequest,
        "BenchmarkService": BenchmarkService,
        "DiagnosticCaseRequest": DiagnosticCaseRequest,
        "DiagnosticSuiteRequest": DiagnosticSuiteRequest,
        "RunService": RunService,
        "TraceIngestRequest": TraceIngestRequest,
    }


def new_run_service(project_root: Path):
    modules = backend_modules(project_root)
    service = modules["RunService"](project_root)
    # MVP CLI favors deterministic local runs; rules stay active, LLM is only a
    # backend fallback when explicitly configured by the environment.
    return service
