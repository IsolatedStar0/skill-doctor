# Skill Doctor Python Control Plane

This package adds a LangGraph orchestration layer around the existing
TypeScript Codex executor and benchmark artifacts.

The graph owns the lifecycle:

```text
prepare -> execute -> collect evidence
                     -> attribute -> repair -> execute
                     -> verify -> promote / reject
```

It deliberately does not replace the repository's Trace protocol,
Evidence Snapshot hashes, token accounting, or paired benchmark engine.

## Run

Install once from the repository root:

```bash
python -m pip install -e "backend[api,dev]"
```

Run the deterministic self-repair loop:

```bash
python -m backend.skilldoctor.cli run
```

Replay the real Codex SDK TDD benchmark through the graph:

```bash
python -m backend.skilldoctor.cli run --executor replay --skill-id tdd-workflow
```

Run a live Codex SDK execution through the graph:

```bash
python -m backend.skilldoctor.cli run --executor codex --skill-id tdd-workflow
```

The Python `CodexExecutionWorker` invokes the repository's Node SDK bridge and
maps the real Codex event stream, token usage, verifier assertions, and
artifacts back into `ExecutionResult`. The isolated Codex thread is read-only,
never requests approval, and cannot use task network access.

Start the dependency-free local API:

```bash
python -m backend.skilldoctor.http_server --host 127.0.0.1 --port 8010
```

The optional FastAPI entry point exposes the same contract:

```bash
python -m uvicorn backend.skilldoctor.api:app --host 127.0.0.1 --port 8010
```

Endpoints:

- `GET /health`
- `POST /runs`
- `POST /runs/stream` (newline-delimited JSON state snapshots)
- `GET /runs/{run_id}`

Completed runs are stored under `reports/langgraph/`.
