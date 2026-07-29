from __future__ import annotations

import argparse
import json
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type
from urllib.parse import urlparse

from pydantic import ValidationError

from .benchmark import BenchmarkService
from .models import (
    BenchmarkRequest,
    CandidateSkillRequest,
    CandidateValidationRequest,
    DiagnosticSuiteRequest,
    RepairPreviewRequest,
    RepairVerificationRequest,
    RunRequest,
    SaveDiagnosticCaseRequest,
    TraceIngestRequest,
)
from .service import RunService


def _allowed_origins() -> set[str]:
    value = os.getenv("SKILL_DOCTOR_CORS_ORIGINS", "*")
    return {origin.strip() for origin in value.split(",") if origin.strip()}


def _default_host() -> str:
    return os.getenv("SKILL_DOCTOR_HOST", "0.0.0.0")


def _default_port() -> int:
    return int(os.getenv("PORT", "8010"))


def make_handler(service: RunService) -> Type[BaseHTTPRequestHandler]:
    allowed_origins = _allowed_origins()
    benchmarks = BenchmarkService(service)

    class SkillDoctorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SkillDoctor/0.1"

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if "*" in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", "*")
            elif origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, OPTIONS",
            )
            self.send_header(
                "Access-Control-Allow-Headers",
                "Content-Type, Authorization, X-API-Key",
            )

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _payload(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB.")
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _require_ingest_auth(self) -> bool:
            expected = os.getenv("SKILL_DOCTOR_INGEST_API_KEY")
            if not expected:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": (
                            "Trace ingest is disabled; "
                            "set SKILL_DOCTOR_INGEST_API_KEY."
                        )
                    },
                )
                return False
            candidates: list[str] = []
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                candidates.append(authorization.removeprefix("Bearer ").strip())
            api_key = self.headers.get("X-API-Key")
            if api_key:
                candidates.append(api_key.strip())
            if any(secrets.compare_digest(candidate, expected) for candidate in candidates):
                return True
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "Invalid trace ingest token."})
            return False

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._json(
                    HTTPStatus.OK,
                    {"status": "ok", "orchestrator": "langgraph"},
                )
                return
            if path == "/scenarios":
                self._json(HTTPStatus.OK, service.list_scenarios())
                return
            if path == "/runs":
                self._json(
                    HTTPStatus.OK,
                    {"runs": service.list_runs()},
                )
                return
            if path == "/runs/events":
                self.send_response(HTTPStatus.OK)
                self._cors()
                self.send_header(
                    "Content-Type",
                    "text/event-stream; charset=utf-8",
                )
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for envelope in service.registry.events():
                        if envelope is None:
                            payload = b": heartbeat\n\n"
                        else:
                            state = envelope["state"]
                            event_id = (
                                f"{state['run_id']}:{envelope['updated_at']}"
                            )
                            payload = (
                                f"id: {event_id}\n"
                                f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
                            ).encode("utf-8")
                        self.wfile.write(payload)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    self.close_connection = True
                return
            if path == "/benchmarks":
                self._json(
                    HTTPStatus.OK,
                    {"benchmarks": benchmarks.list()},
                )
                return
            if path == "/diagnostics/default":
                self._json(HTTPStatus.OK, service.run_diagnostic_suite())
                return
            if path.startswith("/repairs/rejections/"):
                skill_id = path.removeprefix("/repairs/rejections/")
                self._json(HTTPStatus.OK, service.list_rejection_history(skill_id))
                return
            if path.startswith("/benchmarks/"):
                try:
                    self._json(
                        HTTPStatus.OK,
                        benchmarks.get(path.removeprefix("/benchmarks/")),
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "Benchmark not found."},
                    )
                return
            if path.startswith("/runs/"):
                try:
                    self._json(HTTPStatus.OK, service.get(path.removeprefix("/runs/")))
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found."})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                payload = self._payload()
                if path in {"/traces", "/runs/upload"}:
                    request = TraceIngestRequest.model_validate(payload)
                elif path.startswith("/diagnostics/cases/from-run/"):
                    request = SaveDiagnosticCaseRequest.model_validate(payload)
                elif path == "/diagnostics":
                    request = DiagnosticSuiteRequest.model_validate(payload)
                elif path.startswith("/repairs/preview/"):
                    request = RepairPreviewRequest.model_validate(payload)
                elif path.startswith("/repairs/candidates/from-run/"):
                    request = CandidateSkillRequest.model_validate(payload)
                elif path.startswith("/repairs/candidates/") and path.endswith("/validate"):
                    request = CandidateValidationRequest.model_validate(payload)
                elif path == "/repairs/verify":
                    request = RepairVerificationRequest.model_validate(payload)
                elif path.startswith("/benchmarks"):
                    request = BenchmarkRequest.model_validate(payload)
                else:
                    request = RunRequest.model_validate(payload)
            except (ValueError, json.JSONDecodeError, ValidationError) as error:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(error)},
                )
                return

            if path in {"/traces", "/runs/upload"}:
                assert isinstance(request, TraceIngestRequest)
                if not self._require_ingest_auth():
                    return
                self._json(HTTPStatus.OK, service.ingest_trace(request))
                return
            if path == "/benchmarks":
                assert isinstance(request, BenchmarkRequest)
                self._json(HTTPStatus.OK, benchmarks.run(request))
                return
            if path == "/diagnostics":
                assert isinstance(request, DiagnosticSuiteRequest)
                self._json(HTTPStatus.OK, service.run_diagnostic_suite(request))
                return
            if path.startswith("/diagnostics/cases/from-run/"):
                assert isinstance(request, SaveDiagnosticCaseRequest)
                try:
                    self._json(
                        HTTPStatus.OK,
                        service.save_diagnostic_case_from_run(
                            path.removeprefix("/diagnostics/cases/from-run/"),
                            request,
                        ),
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found."})
                return
            if path.startswith("/repairs/preview/"):
                assert isinstance(request, RepairPreviewRequest)
                try:
                    self._json(
                        HTTPStatus.OK,
                        service.create_repair_preview(
                            path.removeprefix("/repairs/preview/"),
                            request,
                        ),
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found."})
                return
            if path.startswith("/repairs/candidates/from-run/"):
                assert isinstance(request, CandidateSkillRequest)
                try:
                    self._json(
                        HTTPStatus.OK,
                        service.create_candidate_skill_from_run(
                            path.removeprefix("/repairs/candidates/from-run/"),
                            request,
                        ),
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found."})
                return
            if path.startswith("/repairs/candidates/") and path.endswith("/validate"):
                assert isinstance(request, CandidateValidationRequest)
                candidate_id = path.removeprefix("/repairs/candidates/").removesuffix("/validate")
                try:
                    self._json(
                        HTTPStatus.OK,
                        service.validate_candidate_skill(candidate_id, request),
                    )
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Candidate not found."})
                return
            if path == "/repairs/verify":
                assert isinstance(request, RepairVerificationRequest)
                try:
                    self._json(HTTPStatus.OK, service.verify_repair(request))
                except ValueError as error:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except FileNotFoundError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Run not found."})
                return
            if path == "/benchmarks/stream":
                assert isinstance(request, BenchmarkRequest)
                self.send_response(HTTPStatus.OK)
                self._cors()
                self.send_header(
                    "Content-Type",
                    "application/x-ndjson; charset=utf-8",
                )
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for state in benchmarks.stream(request):
                        line = (
                            json.dumps(state, ensure_ascii=False) + "\n"
                        ).encode("utf-8")
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    self.close_connection = True
                return
            if path == "/runs":
                assert isinstance(request, RunRequest)
                self._json(HTTPStatus.OK, service.run(request))
                return
            if path == "/runs/stream":
                assert isinstance(request, RunRequest)
                self.send_response(HTTPStatus.OK)
                self._cors()
                self.send_header(
                    "Content-Type",
                    "application/x-ndjson; charset=utf-8",
                )
                self.send_header("Cache-Control", "no-cache, no-transform")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    for state in service.stream(request):
                        line = (
                            json.dumps(state, ensure_ascii=False) + "\n"
                        ).encode("utf-8")
                        self.wfile.write(line)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    self.close_connection = True
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})

        def log_message(self, format: str, *args) -> None:
            print(f"[skill-doctor-api] {self.address_string()} {format % args}")

    return SkillDoctorHandler


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        description="Run the dependency-free Skill Doctor HTTP control plane."
    )
    command.add_argument("--host", default=_default_host())
    command.add_argument("--port", default=_default_port(), type=int)
    return command


def main() -> None:
    args = parser().parse_args()
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_handler(RunService()),
    )
    print(f"Skill Doctor API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
