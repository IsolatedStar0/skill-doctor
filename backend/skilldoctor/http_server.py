from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Type
from urllib.parse import urlparse

from pydantic import ValidationError

from .models import RunRequest
from .service import RunService


def _allowed_origins() -> set[str]:
    value = os.getenv(
        "SKILL_DOCTOR_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001",
    )
    return {origin.strip() for origin in value.split(",") if origin.strip()}


def make_handler(service: RunService) -> Type[BaseHTTPRequestHandler]:
    allowed_origins = _allowed_origins()

    class SkillDoctorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "SkillDoctor/0.1"

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, OPTIONS",
            )
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _request(self) -> RunRequest:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return RunRequest.model_validate(payload)

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
                request = self._request()
            except (ValueError, json.JSONDecodeError, ValidationError) as error:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": str(error)},
                )
                return

            if path == "/runs":
                self._json(HTTPStatus.OK, service.run(request))
                return
            if path == "/runs/stream":
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
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", default=8010, type=int)
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
