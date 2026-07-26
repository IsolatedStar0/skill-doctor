from __future__ import annotations

import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import RunRequest
from .service import RunService

app = FastAPI(
    title="Skill Doctor Control Plane",
    version="0.1.0",
    description="LangGraph orchestration for observable Skill repair runs.",
)
service = RunService()
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "SKILL_DOCTOR_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


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


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    try:
        return service.get(run_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="Run not found.") from error
