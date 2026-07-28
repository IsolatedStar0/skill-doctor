from __future__ import annotations

import json
import os

import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .benchmark import BenchmarkService
from .models import (
    BenchmarkRequest,
    DiagnosticSuiteRequest,
    RunRequest,
    TraceIngestRequest,
)
from .service import RunService

app = FastAPI(
    title="Skill Doctor Control Plane",
    version="0.1.0",
    description="LangGraph orchestration for observable Skill repair runs.",
)
service = RunService()
benchmarks = BenchmarkService(service)
allowed_origins = [
    origin.strip()
    for origin in os.getenv("SKILL_DOCTOR_CORS_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


def require_ingest_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> None:
    expected = os.getenv("SKILL_DOCTOR_INGEST_API_KEY")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Trace ingest is disabled; set SKILL_DOCTOR_INGEST_API_KEY.",
        )
    candidates: list[str] = []
    if authorization and authorization.startswith("Bearer "):
        candidates.append(authorization.removeprefix("Bearer ").strip())
    if x_api_key:
        candidates.append(x_api_key.strip())
    if not any(secrets.compare_digest(candidate, expected) for candidate in candidates):
        raise HTTPException(status_code=401, detail="Invalid trace ingest token.")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "orchestrator": "langgraph"}


@app.post("/runs")
def create_run(request: RunRequest) -> dict:
    try:
        return service.run(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/runs/stream")
def stream_run(request: RunRequest) -> StreamingResponse:
    def generate():
        try:
            for state in service.stream(request):
                yield f"{json.dumps(state, ensure_ascii=False)}\n"
        except ValueError as error:
            yield json.dumps({"error": str(error)}, ensure_ascii=False) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/traces", dependencies=[Depends(require_ingest_auth)])
def ingest_trace(request: TraceIngestRequest) -> dict:
    try:
        return service.ingest_trace(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/runs/upload", dependencies=[Depends(require_ingest_auth)])
def upload_run_trace(request: TraceIngestRequest) -> dict:
    return ingest_trace(request)


@app.get("/runs")
def list_runs() -> dict:
    return {"runs": service.list_runs()}


@app.get("/runs/events")
def stream_run_events() -> StreamingResponse:
    def generate():
        for envelope in service.registry.events():
            if envelope is None:
                yield ": heartbeat\n\n"
                continue
            state = envelope["state"]
            event_id = f"{state['run_id']}:{envelope['updated_at']}"
            yield (
                f"id: {event_id}\n"
                f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return service.get(run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found.") from error


@app.post("/benchmarks")
def create_benchmark(request: BenchmarkRequest) -> dict:
    return benchmarks.run(request)


@app.post("/benchmarks/stream")
def stream_benchmark(request: BenchmarkRequest) -> StreamingResponse:
    def generate():
        for state in benchmarks.stream(request):
            yield f"{json.dumps(state, ensure_ascii=False)}\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/benchmarks")
def list_benchmarks() -> dict:
    return {"benchmarks": benchmarks.list()}


@app.get("/benchmarks/{benchmark_id}")
def get_benchmark(benchmark_id: str) -> dict:
    try:
        return benchmarks.get(benchmark_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="Benchmark not found.",
        ) from error


@app.post("/diagnostics")
def run_diagnostics(request: DiagnosticSuiteRequest) -> dict:
    return service.run_diagnostic_suite(request)


@app.get("/diagnostics/default")
def run_default_diagnostics() -> dict:
    return service.run_diagnostic_suite(DiagnosticSuiteRequest())
