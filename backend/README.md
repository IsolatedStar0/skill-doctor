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
node scripts/python.mjs -m pip install -e "backend[api,dev,observability]"
```

Run the deterministic self-repair loop:

```bash
node scripts/python.mjs -m backend.skilldoctor.cli run
```

Replay the real Codex SDK TDD benchmark through the graph:

```bash
node scripts/python.mjs -m backend.skilldoctor.cli run --executor replay --skill-id tdd-workflow
```

Run a live Codex SDK execution through the graph:

```bash
node scripts/python.mjs -m backend.skilldoctor.cli run --executor codex --skill-id tdd-workflow
```

The Python `CodexExecutionWorker` invokes the repository's Node SDK bridge and
maps the real Codex event stream, token usage, verifier assertions, and
artifacts back into `ExecutionResult`. The isolated Codex thread is read-only,
never requests approval, and cannot use task network access.

The bridge protocol is newline-delimited JSON. `event` envelopes are emitted
immediately for every `runStreamed()` SDK event; one terminal `result` envelope
contains the normalized `ExecutionResult`. During `RunService.stream()`, a
background graph producer and event queue merge these SDK events into live
state snapshots before the `execute` node completes.

Start the dependency-free local API:

```bash
node scripts/python.mjs -m backend.skilldoctor.http_server --port 8010
```

The optional FastAPI entry point exposes the same contract:

```bash
node scripts/python.mjs -m uvicorn backend.skilldoctor.api:app --host 0.0.0.0 --port 8010
```

Endpoints:

- `GET /health`
- `POST /runs`
- `POST /runs/stream` (newline-delimited JSON state snapshots)
- `POST /traces` (authenticated normalized **or raw** trace ingest)
- `POST /runs/upload` (alias for `/traces`)
- `GET /runs/{run_id}`

Set `SKILL_DOCTOR_INGEST_API_KEY` before enabling trace ingest. Clients must send either `Authorization: Bearer <token>` or `X-API-Key: <token>`; if the env var is missing, ingest returns `503` instead of accepting unauthenticated writes.

`POST /traces` accepts two payload shapes:

1. **Normalized** — a pre-computed `execution: ExecutionResult` (backwards
   compatible with the previous ingest contract).
2. **Raw** — the untransformed runtime signal from an upstream agent, using
   any combination of these top-level fields:
   - `runtime_events`: list of `{stage, status, message, metadata, ...}` entries.
   - `tool_calls`: list of `{name, status, output|error, ...}` entries.
   - `model_messages`: list of `{role, content, ...}` entries.
   - `trace_metadata`: arbitrary JSON dictionary (e.g. `puck_task_id`,
     `dispatch_id`, `indicator`, `rca_filter`, `confidence`).

At least one of `execution` or a raw channel must be present, otherwise the
server responds with `422`.

The `UploadedTraceWorker` runs a real analysis pass over the payload before
`collect_evidence` / `attribute` / `finalize`. Analysis steps
(`agent.analyze`, `agent.analyze.runtime_events`,
`agent.analyze.tool_calls`, `agent.analyze.model_messages`,
`agent.analyze.metadata`, `agent.analyze.summarize`) surface as discrete
runtime events in the resulting `GET /runs/{run_id}` response.

Completed runs are stored under `reports/langgraph/`.

## Optional LangSmith mirror

The local NDJSON stream and saved Evidence Snapshot remain the source of
truth. LangSmith is an optional second observability surface; missing
credentials or exporter failures never stop the agent loop.

Copy `.env.example` values into your shell and enable tracing:

```powershell
$env:LANGSMITH_TRACING="true"
$env:LANGSMITH_API_KEY="<your-key>"
$env:LANGSMITH_PROJECT="skill-doctor-dev"
npm run agent:api
```

Each Skill Doctor run becomes exactly one native LangGraph root trace named
`skill-doctor.run`. LangGraph records lifecycle nodes automatically; only the
otherwise invisible live Codex SDK events are added as child runs under the
active `execute` node, with attempt, source, status, metadata, and token usage.
The final `/runs/stream` snapshot includes
`observability.trace_id` and, when LangSmith resolves it, `trace_url`; the
dashboard exposes that URL as **OPEN IN LANGSMITH**.
