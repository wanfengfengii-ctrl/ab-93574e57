"""HTTP front-end for the momentum-plan compiler (standard library only)."""

from __future__ import annotations

import json
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app.planner import Infeasible, Plan, StateSpaceLimitExceeded, compile_plan
from app.validation import validate_request

COMPILE_PATH = "/api/momentum-plans/compile"
HEALTH_PATH = "/health"
ROOT_PATH = "/"
MAX_BODY_BYTES = 1 << 20  # 1 MiB

SERVICE_INFO = {
    "service": "momentum-plans",
    "version": "1.0.0",
    "endpoints": {"health": HEALTH_PATH, "compile": COMPILE_PATH},
}


def _reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def compile_response(payload):
    """Translate a decoded JSON body into ``(http_status, response_body)``."""

    request, errors = validate_request(payload)
    if errors:
        return 400, {
            "status": "INVALID_INPUT",
            "message": "request failed validation; "
            "'errors' pinpoints each offending field",
            "errors": errors,
        }
    try:
        result = compile_plan(request)
    except StateSpaceLimitExceeded as exc:
        return 422, {
            "status": "INFEASIBLE",
            "reason": "STATE_SPACE_LIMIT_EXCEEDED",
            "message": str(exc),
            "lastReachableSlot": exc.slot - 1,
            "reachableStateCount": None,
        }
    if isinstance(result, Infeasible):
        return 422, {
            "status": "INFEASIBLE",
            "reason": result.reason,
            "lastReachableSlot": result.last_reachable_slot,
            "reachableStateCount": result.reachable_state_count,
        }
    assert isinstance(result, Plan)
    selected = []
    for index, (command_id, mode) in enumerate(zip(result.command_ids, result.modes)):
        command = next(
            c for c in request["slots"][index]["commands"] if c["id"] == command_id
        )
        selected.append(
            {
                "slot": index + 1,
                "commandId": command_id,
                "mode": mode,
                "correction": list(command["correction"]),
                "energy": command["energy"],
            }
        )
    return 200, {
        "status": "OK",
        "selectedCommands": selected,
        "momenta": [list(momentum) for momentum in result.momenta],
        "objectives": {
            "totalEnergy": result.total_energy,
            "modeSwitches": result.mode_switches,
            "commandIdSequence": list(result.command_ids),
        },
    }


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "MomentumPlans/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ------------------------------------------------------

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _not_found(self, path):
        self._send_json(
            404, {"status": "NOT_FOUND", "message": f"unknown path: {path}"}
        )

    def _method_not_allowed(self, path):
        allow = "POST" if path == COMPILE_PATH else "GET"
        self._send_json(
            405,
            {"status": "METHOD_NOT_ALLOWED", "message": f"use {allow} on this path"},
            {"Allow": allow},
        )

    def _read_json_body(self):
        """Return ``(payload, None)`` or ``(None, (status, error_body))``."""

        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return None, (
                400,
                {"status": "BAD_REQUEST", "message": "missing Content-Length header"},
            )
        try:
            length = int(length_header)
        except ValueError:
            return None, (
                400,
                {"status": "BAD_REQUEST", "message": "invalid Content-Length header"},
            )
        if length < 0 or length > MAX_BODY_BYTES:
            return None, (
                413,
                {
                    "status": "PAYLOAD_TOO_LARGE",
                    "message": f"body must be at most {MAX_BODY_BYTES} bytes",
                },
            )
        raw = self.rfile.read(length)
        try:
            return json.loads(raw, parse_constant=_reject_constant), None
        except ValueError:
            return None, (
                400,
                {"status": "BAD_REQUEST", "message": "body is not valid JSON"},
            )

    # -- routes ---------------------------------------------------------

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == HEALTH_PATH:
            self._send_json(200, {"status": "ok"})
        elif path == ROOT_PATH:
            self._send_json(200, SERVICE_INFO)
        elif path == COMPILE_PATH:
            self._method_not_allowed(path)
        else:
            self._not_found(path)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != COMPILE_PATH:
            self._not_found(path)
            return
        payload, error = self._read_json_body()
        if error is not None:
            status, body = error
            self._send_json(status, body)
            return
        try:
            status, body = compile_response(payload)
        except Exception:  # pragma: no cover - defensive catch-all
            traceback.print_exc()
            self._send_json(
                500,
                {"status": "INTERNAL_ERROR", "message": "unexpected server error"},
            )
            return
        self._send_json(status, body)

    def do_PUT(self):
        self._method_not_allowed(urlsplit(self.path).path)

    def do_PATCH(self):
        self._method_not_allowed(urlsplit(self.path).path)

    def do_DELETE(self):
        self._method_not_allowed(urlsplit(self.path).path)


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"momentum-plans API listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
