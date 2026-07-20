"""Pin replay, deadline, and environment isolation for the HTTP task runtime."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from uuid import uuid4

import pytest
from purelms_itask_runtime import ObjectIdentity
from purelms_itask_runtime import service
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import ExecutionContext
from purelms_shared.envelopes import SimulationInputEnvelope
from purelms_shared.envelopes import SimulationOutputEnvelope


def _request(*, deadline: datetime | None = None) -> service.ServiceExecutionRequest:
    run_id = str(uuid4())
    return service.ServiceExecutionRequest(
        run_id=run_id,
        timeout_at=deadline or datetime.now(UTC) + timedelta(minutes=2),
        environment={
            "PURELMS_INPUT_URI": "gs://bucket/run/input.json",
            "PURELMS_OUTPUT_URI": "gs://bucket/run/output.json",
            "PURELMS_INPUT_FETCH_URL": "https://signed.example/input",
            "PURELMS_OUTPUT_UPLOAD_URL": "https://signed.example/output",
            "PURELMS_OUTPUT_VERIFY_URL": "https://signed.example/verify",
            "PURELMS_RUN_ID": run_id,
            "PURELMS_INPUT_SHA256": "a" * 64,
            "PURELMS_INPUT_SIZE_BYTES": "10",
            "PURELMS_INPUT_GENERATION": "1",
        },
    )


def _input(run_id: str) -> SimulationInputEnvelope:
    return SimulationInputEnvelope(
        run_id=run_id,
        backend_slug="echo",
        backend_version="1.0.0",
        student_id=1,
        course_id=1,
        block_id=1,
        parameters={},
        context=ExecutionContext(
            callback_url_progress="https://worker.example/progress",
            callback_url_complete="https://worker.example/complete",
            callback_audience="https://worker.example",
            timeout_seconds=30,
        ),
    )


def _output(run_id: str) -> SimulationOutputEnvelope:
    return SimulationOutputEnvelope(
        run_id=run_id,
        status=OutputStatus.SUCCESS,
        outputs={"ok": True},
        runtime_seconds=0.1,
    )


def test_expired_delivery_acknowledges_without_starting_compute(monkeypatch):
    """A late retry must not begin fresh billable computation."""
    now = datetime.now(UTC)
    request = _request(deadline=now - timedelta(seconds=1))
    calls: list[object] = []

    status = service.execute_delivery(
        request,
        Path("/app/main.py"),
        now=lambda: now,
        runner=lambda *_args, **_kwargs: calls.append(object()),
    )

    assert status is HTTPStatus.NO_CONTENT
    assert calls == []


def test_replay_reuses_output_and_retries_only_completion(monkeypatch):
    """A redelivery with output present cannot rerun the domain tool."""
    request = _request()
    input_envelope = _input(request.run_id)
    identity = ObjectIdentity(sha256="b" * 64, size_bytes=5, generation=2)
    notices: list[ObjectIdentity] = []
    monkeypatch.setattr(service, "read_input_envelope", lambda _loc: input_envelope)
    monkeypatch.setattr(
        service,
        "read_existing_output",
        lambda _loc: (_output(request.run_id), identity),
    )
    monkeypatch.setattr(
        service,
        "notify_existing_output",
        lambda _loc, _context, seen: notices.append(seen),
    )

    status = service.execute_delivery(
        request,
        Path("/app/main.py"),
        runner=lambda *_args, **_kwargs: pytest.fail("backend was rerun"),
    )

    assert status is HTTPStatus.OK
    assert notices == [identity]


def test_new_delivery_runs_with_private_environment_mapping(monkeypatch):
    """Concurrent requests must not communicate through global os.environ writes."""
    request = _request()
    monkeypatch.setattr(
        service, "read_input_envelope", lambda _loc: _input(request.run_id)
    )
    identity = ObjectIdentity(sha256="c" * 64, size_bytes=5, generation=3)
    outputs = iter([None, (_output(request.run_id), identity)])
    monkeypatch.setattr(service, "read_existing_output", lambda _loc: next(outputs))
    seen: list[dict[str, str]] = []
    before = os.environ.get("PURELMS_RUN_ID")

    def _runner(_command, *, env, timeout, check):
        assert timeout > 0
        assert check is False
        seen.append(env)
        return subprocess.CompletedProcess([], 0)

    status = service.execute_delivery(
        request,
        Path("/app/main.py"),
        runner=_runner,
    )

    assert status is HTTPStatus.OK
    assert seen[0]["PURELMS_RUN_ID"] == request.run_id
    assert os.environ.get("PURELMS_RUN_ID") == before


def test_success_without_immutable_output_is_retried(monkeypatch):
    """Exit code zero alone cannot acknowledge a provider delivery."""
    request = _request()
    monkeypatch.setattr(
        service, "read_input_envelope", lambda _loc: _input(request.run_id)
    )
    monkeypatch.setattr(service, "read_existing_output", lambda _loc: None)

    status = service.execute_delivery(
        request,
        Path("/app/main.py"),
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0),
    )

    assert status is HTTPStatus.INTERNAL_SERVER_ERROR


def test_failed_process_with_written_output_finishes_without_recompute(monkeypatch):
    """A callback-only failure should reuse immutable output immediately."""
    request = _request()
    identity = ObjectIdentity(sha256="c" * 64, size_bytes=5, generation=3)
    outputs = iter([None, (_output(request.run_id), identity)])
    notices: list[ObjectIdentity] = []
    monkeypatch.setattr(
        service, "read_input_envelope", lambda _loc: _input(request.run_id)
    )
    monkeypatch.setattr(service, "read_existing_output", lambda _loc: next(outputs))
    monkeypatch.setattr(
        service,
        "notify_existing_output",
        lambda _loc, _context, seen: notices.append(seen),
    )

    status = service.execute_delivery(
        request,
        Path("/app/main.py"),
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1),
    )

    assert status is HTTPStatus.OK
    assert notices == [identity]


def test_request_rejects_arbitrary_environment_keys():
    """The provider handoff is not an arbitrary remote environment primitive."""
    request = _request()
    payload = {
        "schema_version": service.SERVICE_EXECUTION_SCHEMA,
        "run_id": request.run_id,
        "timeout_at": request.timeout_at.isoformat(),
        "environment": {**request.environment, "UNSAFE": "value"},
    }

    with pytest.raises(service.ServiceRequestError, match="environment keys"):
        service.ServiceExecutionRequest.from_payload(payload)
