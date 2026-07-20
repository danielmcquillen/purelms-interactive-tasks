"""Replay-safe HTTP entrypoint for request-driven task containers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID

from purelms_itask_runtime.runtime import RuntimeConfigError
from purelms_itask_runtime.runtime import RuntimeLocation
from purelms_itask_runtime.runtime import notify_existing_output
from purelms_itask_runtime.runtime import read_existing_output
from purelms_itask_runtime.runtime import read_input_envelope

logger = logging.getLogger(__name__)

SERVICE_EXECUTION_SCHEMA = "purelms.service_execution.v1"
MAX_REQUEST_BYTES = 32 * 1024
_REQUIRED_ENVIRONMENT = frozenset(
    {
        "PURELMS_INPUT_URI",
        "PURELMS_OUTPUT_URI",
        "PURELMS_INPUT_FETCH_URL",
        "PURELMS_OUTPUT_UPLOAD_URL",
        "PURELMS_OUTPUT_VERIFY_URL",
        "PURELMS_RUN_ID",
        "PURELMS_INPUT_SHA256",
        "PURELMS_INPUT_SIZE_BYTES",
        "PURELMS_INPUT_GENERATION",
    }
)
_OPTIONAL_ENVIRONMENT = frozenset({"PURELMS_MAX_ENVELOPE_BYTES"})


class ServiceRequestError(ValueError):
    """A provider delivery does not satisfy the portable Service contract."""


@dataclass(frozen=True)
class ServiceExecutionRequest:
    """Validated provider delivery containing one run-scoped capability set."""

    run_id: str
    timeout_at: datetime
    environment: dict[str, str]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ServiceExecutionRequest:
        """Validate the narrow wire contract and reject arbitrary environment keys."""
        if set(payload) != {"schema_version", "run_id", "timeout_at", "environment"}:
            raise ServiceRequestError(
                "Service request has unexpected or missing fields."
            )
        if payload["schema_version"] != SERVICE_EXECUTION_SCHEMA:
            raise ServiceRequestError("Unsupported Service execution schema.")
        try:
            run_id = str(UUID(str(payload["run_id"])))
        except (TypeError, ValueError) as exc:
            raise ServiceRequestError("run_id must be a UUID.") from exc
        try:
            timeout_at = datetime.fromisoformat(str(payload["timeout_at"]))
        except ValueError as exc:
            raise ServiceRequestError(
                "timeout_at must be an ISO-8601 timestamp."
            ) from exc
        if timeout_at.tzinfo is None:
            raise ServiceRequestError("timeout_at must include a timezone.")
        timeout_at = timeout_at.astimezone(UTC)

        raw_environment = payload["environment"]
        if not isinstance(raw_environment, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_environment.items()
        ):
            raise ServiceRequestError("environment must map strings to strings.")
        keys = set(raw_environment)
        missing = _REQUIRED_ENVIRONMENT - keys
        extra = keys - _REQUIRED_ENVIRONMENT - _OPTIONAL_ENVIRONMENT
        if missing or extra:
            raise ServiceRequestError(
                "environment keys do not match the portable runtime contract."
            )
        if raw_environment["PURELMS_RUN_ID"] != run_id:
            raise ServiceRequestError("Body and environment run identities differ.")
        return cls(
            run_id=run_id,
            timeout_at=timeout_at,
            environment=dict(raw_environment),
        )


def _decode_request_body(stream, length: int) -> dict[str, Any]:
    """Read one bounded JSON object from an HTTP request body."""
    if length < 1 or length > MAX_REQUEST_BYTES:
        raise ServiceRequestError("Request body size is invalid.")
    payload = json.loads(stream.read(length))
    if not isinstance(payload, dict):
        raise ServiceRequestError("Request body must be a JSON object.")
    return payload


def execute_delivery(  # noqa: PLR0911 - explicit outcomes form the state machine
    request: ServiceExecutionRequest,
    backend_script: Path,
    *,
    now: Callable[[], datetime] | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> HTTPStatus:
    """Execute or replay one delivery without mutating process-global state."""
    clock = now or (lambda: datetime.now(UTC))
    if clock() >= request.timeout_at:
        logger.info("Ignoring expired Service delivery for run %s", request.run_id)
        return HTTPStatus.NO_CONTENT

    location = RuntimeLocation.from_mapping(request.environment)
    input_envelope = read_input_envelope(location)
    if str(input_envelope.run_id) != request.run_id:
        raise ServiceRequestError("Input envelope run_id does not match delivery.")

    existing = read_existing_output(location)
    if existing is not None:
        output, identity = existing
        if str(output.run_id) != request.run_id:
            raise ServiceRequestError("Existing output belongs to another run.")
        notify_existing_output(location, input_envelope.context, identity)
        return HTTPStatus.OK

    remaining = (request.timeout_at - clock()).total_seconds()
    if remaining <= 0:
        return HTTPStatus.NO_CONTENT
    child_environment = os.environ.copy()
    child_environment.update(request.environment)
    try:
        result = runner(
            [sys.executable, str(backend_script)],
            env=child_environment,
            timeout=remaining,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Backend reached the absolute deadline for run %s", request.run_id
        )
        return HTTPStatus.NO_CONTENT

    if result.returncode == 0:
        existing = read_existing_output(location)
        if existing is None:
            logger.error(
                "Backend exited successfully without immutable output for run %s",
                request.run_id,
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        output, _identity = existing
        if str(output.run_id) != request.run_id:
            raise ServiceRequestError("Existing output belongs to another run.")
        return HTTPStatus.OK

    # A backend may have written immutable output and then failed only while
    # notifying PureLMS. Finish that exact output immediately instead of
    # repeating domain computation on the next Cloud Tasks delivery.
    existing = read_existing_output(location)
    if existing is not None:
        output, identity = existing
        if str(output.run_id) != request.run_id:
            raise ServiceRequestError("Existing output belongs to another run.")
        notify_existing_output(location, input_envelope.context, identity)
        return HTTPStatus.OK
    return HTTPStatus.INTERNAL_SERVER_ERROR


def serve(backend_script: Path, *, port: int | None = None) -> None:
    """Serve the portable task endpoint on Cloud Run's configured port."""
    script = backend_script.resolve()
    if not script.is_file():
        raise RuntimeConfigError(f"Backend script does not exist: {script}")

    class Handler(BaseHTTPRequestHandler):
        """Bounded HTTP adapter around :func:`execute_delivery`."""

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self.send_response(HTTPStatus.OK)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            if self.path not in {"", "/"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = _decode_request_body(self.rfile, length)
                status = execute_delivery(
                    ServiceExecutionRequest.from_payload(payload),
                    script,
                )
            except (
                json.JSONDecodeError,
                ServiceRequestError,
                RuntimeConfigError,
                ValueError,
            ) as exc:
                logger.warning("Rejected Service delivery: %s", exc)
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            except Exception:
                logger.exception("Service execution delivery failed")
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_response(status)
            self.end_headers()

        def log_message(self, message_format: str, *args: object) -> None:
            logger.info("task service: " + message_format, *args)

    listen_port = port or int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", listen_port), Handler).serve_forever()


__all__ = [
    "SERVICE_EXECUTION_SCHEMA",
    "ServiceExecutionRequest",
    "ServiceRequestError",
    "execute_delivery",
    "serve",
]
