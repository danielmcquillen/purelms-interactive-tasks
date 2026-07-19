"""
Tests for the shared backend runtime helper.

The signed-object and callback HTTP seams are monkeypatched so the contract
is exercised without credentials, a network, or a real bucket.
"""

from __future__ import annotations

import io
import json
import urllib.error
from uuid import uuid4

import purelms_itask_runtime.runtime as rt
import pytest
from purelms_itask_runtime import ProgressReporter
from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import make_progress_reporter
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import ExecutionContext
from purelms_shared.envelopes import SimulationInputEnvelope
from purelms_shared.envelopes import SimulationOutputEnvelope

_FILE_SENTINEL = "file:///dev/null"
_PROGRESS_URL = "https://worker.example/api/v1/sims/internal/runs/abc/progress"
_COMPLETE_URL = "https://worker.example/api/v1/sims/internal/runs/abc/complete"
_AUDIENCE = "https://worker.example"


def _context(
    *,
    progress_url: str,
    complete_url: str,
    progress_min_interval_seconds: float = 2.0,
) -> ExecutionContext:
    return ExecutionContext(
        callback_url_progress=progress_url,
        callback_url_complete=complete_url,
        callback_audience=_AUDIENCE,
        timeout_seconds=30,
        progress_min_interval_seconds=progress_min_interval_seconds,
    )


def _input_envelope(context: ExecutionContext) -> SimulationInputEnvelope:
    return SimulationInputEnvelope(
        run_id=uuid4(),
        backend_slug="echo",
        backend_version="0.1.0",
        student_id=1,
        course_id=1,
        block_id=1,
        parameters={"a": 1},
        context=context,
    )


def _output_envelope(run_id) -> SimulationOutputEnvelope:
    return SimulationOutputEnvelope(
        run_id=run_id,
        status=OutputStatus.SUCCESS,
        outputs={"ok": True},
        runtime_seconds=0.1,
    )


def _set_cloud_env(monkeypatch) -> None:
    monkeypatch.setenv("PURELMS_INPUT_URI", "gs://bucket/runs/1/input.json")
    monkeypatch.setenv("PURELMS_OUTPUT_URI", "gs://bucket/runs/1/output.json")
    monkeypatch.setenv("PURELMS_INPUT_FETCH_URL", "https://signed.example/input")
    monkeypatch.setenv(
        "PURELMS_OUTPUT_UPLOAD_URL",
        "https://signed.example/output-upload",
    )
    monkeypatch.setenv(
        "PURELMS_OUTPUT_VERIFY_URL",
        "https://signed.example/output-verify",
    )


# ---------------------------------------------------------------------
# RuntimeLocation.from_env
# ---------------------------------------------------------------------


def test_from_env_local_dir_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("PURELMS_INPUT_URI", raising=False)
    monkeypatch.delenv("PURELMS_OUTPUT_URI", raising=False)
    monkeypatch.setenv("PURELMS_INPUT_DIR", str(tmp_path / "in"))
    monkeypatch.setenv("PURELMS_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("PURELMS_RUN_ID", "run-1")

    loc = RuntimeLocation.from_env()
    assert loc.run_id == "run-1"
    assert loc.uses_gcs_input is False
    assert loc.uses_gcs_output is False
    assert loc.input_dir == tmp_path / "in"


def test_from_env_gcs_uri_mode(monkeypatch):
    _set_cloud_env(monkeypatch)
    monkeypatch.setenv("PURELMS_RUN_ID", "run-2")

    loc = RuntimeLocation.from_env()
    assert loc.uses_gcs_input is True
    assert loc.uses_gcs_output is True


# ---------------------------------------------------------------------
# read_input_envelope
# ---------------------------------------------------------------------


def test_read_input_local(tmp_path):
    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    env = _input_envelope(ctx)
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "input.json").write_text(env.model_dump_json())

    loc = RuntimeLocation(
        run_id="r",
        input_uri=None,
        output_uri=None,
        input_dir=input_dir,
        output_dir=tmp_path / "out",
    )
    parsed = read_input_envelope(loc)
    assert parsed.backend_slug == "echo"
    assert parsed.parameters == {"a": 1}


def test_read_input_missing_local_raises(tmp_path):
    loc = RuntimeLocation(
        run_id="r",
        input_uri=None,
        output_uri=None,
        input_dir=tmp_path / "in",
        output_dir=tmp_path / "out",
    )
    with pytest.raises(FileNotFoundError):
        read_input_envelope(loc)


def test_read_input_gcs(monkeypatch):
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    env = _input_envelope(ctx)
    monkeypatch.setattr(
        rt,
        "_http_get_bytes",
        lambda _url, **_kwargs: (env.model_dump_json().encode(), 7),
    )

    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
        input_fetch_url="https://signed.example/input",
    )
    parsed = read_input_envelope(loc)
    assert parsed.backend_slug == "echo"


def test_read_input_rejects_gcs_generation_mismatch(monkeypatch):
    """Strict GCS input identifies one immutable object generation, not just bytes."""
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    env = _input_envelope(ctx)
    monkeypatch.setattr(
        rt,
        "_http_get_bytes",
        lambda _url, **_kwargs: (env.model_dump_json().encode(), 7),
    )
    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
        input_generation=8,
        input_fetch_url="https://signed.example/input",
    )

    with pytest.raises(rt.RuntimeConfigError, match="generation mismatch"):
        read_input_envelope(loc)


# ---------------------------------------------------------------------
# make_progress_reporter
# ---------------------------------------------------------------------


def test_progress_reporter_none_for_file_sentinel():
    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    assert make_progress_reporter(ctx, started_at=0.0) is None


def test_progress_reporter_posts_progress_callback(monkeypatch):
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        rt,
        "_post_json",
        lambda url, audience, body: calls.append((url, audience, body)),
    )
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    reporter = make_progress_reporter(ctx, started_at=0.0)
    assert reporter is not None

    reporter(42, "Running")

    assert len(calls) == 1
    url, audience, body = calls[0]
    assert url == _PROGRESS_URL
    assert audience == _AUDIENCE
    assert '"progress_pct":25' in body.replace(" ", "")
    assert "Running" in body


def test_progress_reporter_defaults_to_quarter_steps_and_minimum_interval(
    monkeypatch,
):
    """Raw tool chatter becomes at most 0/25/50/75/100 over HTTP."""
    bodies: list[dict] = []
    monkeypatch.setattr(
        rt,
        "_post_json",
        lambda _url, _audience, body: bodies.append(json.loads(body)),
    )
    now = [10.0]
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    reporter = ProgressReporter(
        context=ctx,
        started_at=5.0,
        clock=lambda: now[0],
    )

    reporter(1, "starting")  # floors to 0 and emits immediately
    reporter(24, "still starting")  # same milestone: no duplicate
    now[0] = 10.5
    reporter(25, "quarter")  # new milestone, but too soon
    now[0] = 12.0
    reporter(49, "quarter")  # pending quarter now emits
    now[0] = 14.0
    reporter(50, "half")
    now[0] = 14.1
    reporter(80, "three quarters")  # throttled by time
    now[0] = 16.0
    reporter(80, "three quarters")
    now[0] = 16.1
    reporter(100, "backend work complete")  # terminal milestone is immediate

    assert [body["progress_pct"] for body in bodies] == [0, 25, 50, 75, 100]
    assert bodies[0]["elapsed_seconds"] == 5.0
    assert bodies[-1]["step"] == "backend work complete"


def test_progress_reporter_accepts_backend_specific_milestones(monkeypatch):
    """The quarter-step policy is a default, not a hard-coded limitation."""
    bodies: list[dict] = []
    monkeypatch.setattr(
        rt,
        "_post_json",
        lambda _url, _audience, body: bodies.append(json.loads(body)),
    )
    ctx = _context(
        progress_url=_PROGRESS_URL,
        complete_url=_COMPLETE_URL,
        progress_min_interval_seconds=0,
    )
    reporter = ProgressReporter(
        context=ctx,
        started_at=0,
        milestones=(0, 10, 100),
        clock=lambda: 1.0,
    )

    reporter(18, "custom")
    reporter(100, "done")

    assert [body["progress_pct"] for body in bodies] == [10, 100]


def test_progress_reporter_rejects_invalid_milestones():
    """Custom milestone sets must remain monotonic and cover the lifecycle."""
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    with pytest.raises(ValueError, match="span 0 to 100"):
        ProgressReporter(
            context=ctx,
            started_at=0,
            milestones=(0, 50, 50, 100),
        )


def test_progress_reporter_swallows_post_failures(monkeypatch):
    def _boom(*_args, **_kwargs):
        msg = "network down"
        raise RuntimeError(msg)

    monkeypatch.setattr(rt, "_post_json", _boom)
    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    reporter = make_progress_reporter(ctx, started_at=0.0)
    assert reporter is not None
    # Must NOT raise — a flaky progress callback can't fail the run.
    reporter(10, "step")


# ---------------------------------------------------------------------
# write_output_envelope
# ---------------------------------------------------------------------


def test_write_output_local_writes_file_and_skips_complete(monkeypatch, tmp_path):
    posts: list[tuple] = []
    monkeypatch.setattr(rt, "_post_json", lambda *a: posts.append(a))

    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    run_id = uuid4()
    out_dir = tmp_path / "out"
    loc = RuntimeLocation(
        run_id="r",
        input_uri=None,
        output_uri=None,
        input_dir=tmp_path / "in",
        output_dir=out_dir,
    )

    write_output_envelope(loc, _output_envelope(run_id), ctx)

    # Output written locally...
    written = (out_dir / "output.json").read_text()
    assert '"status"' in written
    # ...and NO complete callback fired (file:/// sentinel → worker reads
    # the file directly).
    assert posts == []


def test_write_output_gcs_uploads_verifies_and_posts_complete(monkeypatch):
    uploads: list[bytes] = []
    posts: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        rt,
        "_http_put_bytes",
        lambda _url, content: uploads.append(content) or 456,
    )
    monkeypatch.setattr(
        rt,
        "_http_get_bytes",
        lambda _url, **_kwargs: (uploads[0], 456),
    )
    monkeypatch.setattr(
        rt,
        "_post_json",
        lambda url, audience, body: posts.append((url, audience, body)),
    )

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    run_id = uuid4()
    output_uri = "gs://bucket/runs/1/output.json"
    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri=output_uri,
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
        input_fetch_url="https://signed.example/input",
        output_upload_url="https://signed.example/output-upload",
        output_verify_url="https://signed.example/output-verify",
    )

    write_output_envelope(loc, _output_envelope(run_id), ctx, exit_code=0)

    # Uploaded the envelope to the output URI...
    assert len(uploads) == 1
    assert b'"status"' in uploads[0]
    # ...and POSTed the required completion notification carrying that URI.
    assert len(posts) == 1
    url, audience, body = posts[0]
    assert url == _COMPLETE_URL
    assert audience == _AUDIENCE
    assert output_uri in body
    assert '"exit_code":0' in body.replace(" ", "")
    assert '"output_generation":456' in body.replace(" ", "")
    assert '"output_sha256":' in body.replace(" ", "")


def test_complete_callback_noops_on_file_sentinel(monkeypatch):
    posts: list[tuple] = []
    monkeypatch.setattr(rt, "_post_json", lambda *a: posts.append(a))
    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    rt._post_complete(ctx, output_envelope_uri="/tmp/out/output.json", exit_code=0)
    assert posts == []


# ---------------------------------------------------------------------
# Completion delivery is required for the prompt path — retry then raise
# ---------------------------------------------------------------------


def _boom(*_args, **_kwargs):
    msg = "network down"
    raise RuntimeError(msg)


def test_complete_callback_retries_then_raises(monkeypatch):
    """A /complete that never lands must NOT be swallowed: retry, then
    raise so the container exits non-zero and the sweeper salvages."""
    attempts: list[str] = []

    def _record_then_fail(url, _audience, _body):
        attempts.append(url)
        raise RuntimeError("boom")

    monkeypatch.setattr(rt, "_post_json", _record_then_fail)
    monkeypatch.setattr(rt.time, "sleep", lambda _s: None)  # no real backoff

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    with pytest.raises(rt.CompleteCallbackError):
        rt._post_complete(ctx, output_envelope_uri="gs://b/o.json", exit_code=0)
    assert len(attempts) == rt._COMPLETE_MAX_ATTEMPTS


def test_complete_callback_succeeds_on_retry(monkeypatch):
    """A transient failure that recovers on a later attempt does NOT raise."""
    calls = {"n": 0}

    def _fail_once_then_ok(_url, _audience, _body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr(rt, "_post_json", _fail_once_then_ok)
    monkeypatch.setattr(rt.time, "sleep", lambda _s: None)

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    rt._post_complete(ctx, output_envelope_uri="gs://b/o.json", exit_code=0)
    assert calls["n"] == 2  # failed once, succeeded on the retry


def test_complete_callback_refreshes_oidc_token_on_retry(monkeypatch):
    """Each delivery attempt mints a fresh token instead of reusing a stale one."""
    tokens: list[str] = []
    auth_headers: list[str | None] = []

    def _fetch(_audience):
        token = f"token-{len(tokens) + 1}"
        tokens.append(token)
        return token

    class _Response:
        """Minimal successful urllib response context manager."""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b""

    def _urlopen(request, *, timeout):
        assert timeout == rt._CALLBACK_TIMEOUT_SECONDS
        auth_headers.append(request.get_header("Authorization"))
        if len(auth_headers) == 1:
            raise TimeoutError
        return _Response()

    monkeypatch.setattr(rt, "_fetch_id_token", _fetch)
    monkeypatch.setattr(rt.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(rt.time, "sleep", lambda _s: None)

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    rt._post_complete(ctx, output_envelope_uri="gs://b/o.json", exit_code=0)

    assert tokens == ["token-1", "token-2"]
    assert auth_headers == ["Bearer token-1", "Bearer token-2"]


def test_write_output_gcs_raises_when_complete_undeliverable(monkeypatch):
    """The envelope is uploaded, but an undeliverable /complete propagates
    as CompleteCallbackError (so the backend exits non-zero)."""
    written: list[bytes] = []
    monkeypatch.setattr(
        rt,
        "_http_put_bytes",
        lambda _url, content: written.append(content) or 456,
    )
    monkeypatch.setattr(
        rt,
        "_http_get_bytes",
        lambda _url, **_kwargs: (written[0], 456),
    )
    monkeypatch.setattr(rt, "_post_json", _boom)
    monkeypatch.setattr(rt.time, "sleep", lambda _s: None)

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
        input_fetch_url="https://signed.example/input",
        output_upload_url="https://signed.example/output-upload",
        output_verify_url="https://signed.example/output-verify",
    )
    with pytest.raises(rt.CompleteCallbackError):
        write_output_envelope(loc, _output_envelope(uuid4()), ctx)


def test_http_callback_refuses_anonymous_fallback(monkeypatch):
    """A token-mint failure must stop before any unauthenticated POST."""
    posts: list[object] = []
    monkeypatch.setattr(
        rt,
        "_fetch_id_token",
        lambda _audience: (_ for _ in ()).throw(
            rt.CallbackAuthenticationError("metadata unavailable")
        ),
    )
    monkeypatch.setattr(
        rt.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: posts.append(object()),
    )

    with pytest.raises(rt.CallbackAuthenticationError, match="metadata unavailable"):
        rt._post_json(_COMPLETE_URL, _AUDIENCE, "{}")

    assert posts == []


def test_empty_callback_audience_fails_closed():
    """A missing audience cannot silently become an anonymous callback."""
    with pytest.raises(rt.CallbackAuthenticationError, match="audience is empty"):
        rt._fetch_id_token("")


def test_read_input_rejects_hash_mismatch(tmp_path):
    """The runtime must not execute bytes that differ from the launch claim."""
    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    env = _input_envelope(ctx)
    raw = env.model_dump_json().encode()
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "input.json").write_bytes(raw)
    loc = RuntimeLocation(
        run_id="r",
        input_uri=None,
        output_uri=None,
        input_dir=input_dir,
        output_dir=tmp_path / "out",
        input_sha256="0" * 64,
        input_size_bytes=len(raw),
    )

    with pytest.raises(rt.RuntimeConfigError, match="sha256 mismatch"):
        read_input_envelope(loc)


def test_from_env_requires_generation_for_strict_gcs_input(monkeypatch):
    """A strict cloud launch must pin the exact object generation."""
    _set_cloud_env(monkeypatch)
    monkeypatch.setenv("PURELMS_INPUT_SHA256", "a" * 64)
    monkeypatch.setenv("PURELMS_INPUT_SIZE_BYTES", "123")
    monkeypatch.delenv("PURELMS_INPUT_GENERATION", raising=False)

    with pytest.raises(rt.RuntimeConfigError, match="INPUT_GENERATION"):
        RuntimeLocation.from_env()


# ---------------------------------------------------------------------
# from_env mode validation — both gs:// or neither (P2 review)
# ---------------------------------------------------------------------


def test_from_env_rejects_input_uri_without_output_uri(monkeypatch):
    monkeypatch.setenv("PURELMS_INPUT_URI", "gs://bucket/runs/1/input.json")
    monkeypatch.delenv("PURELMS_OUTPUT_URI", raising=False)
    with pytest.raises(rt.RuntimeConfigError):
        RuntimeLocation.from_env()


def test_from_env_rejects_output_uri_without_input_uri(monkeypatch):
    monkeypatch.delenv("PURELMS_INPUT_URI", raising=False)
    monkeypatch.setenv("PURELMS_OUTPUT_URI", "gs://bucket/runs/1/output.json")
    with pytest.raises(rt.RuntimeConfigError):
        RuntimeLocation.from_env()


def test_from_env_rejects_non_gs_uris(monkeypatch):
    monkeypatch.setenv("PURELMS_INPUT_URI", "https://example/input.json")
    monkeypatch.setenv("PURELMS_OUTPUT_URI", "https://example/output.json")
    with pytest.raises(rt.RuntimeConfigError):
        RuntimeLocation.from_env()


def test_write_output_rejects_bytes_changed_after_upload(monkeypatch):
    """The runtime notifies completion only after byte-for-byte verification."""
    monkeypatch.setattr(rt, "_http_put_bytes", lambda _url, _content: 9)
    monkeypatch.setattr(
        rt,
        "_http_get_bytes",
        lambda _url, **_kwargs: (b"different", 9),
    )
    posts: list[object] = []
    monkeypatch.setattr(rt, "_post_json", lambda *_args: posts.append(object()))
    location = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
        input_fetch_url="https://signed.example/input",
        output_upload_url="https://signed.example/output-upload",
        output_verify_url="https://signed.example/output-verify",
    )

    with pytest.raises(rt.RuntimeConfigError, match="byte-for-byte"):
        write_output_envelope(
            location,
            _output_envelope(uuid4()),
            _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL),
        )

    assert posts == []


def test_signed_http_error_does_not_expose_capability_url(monkeypatch):
    """An expiring signed URL must never appear in a surfaced diagnostic."""
    secret_url = "https://storage.example/object?X-Goog-Signature=secret"
    error = urllib.error.HTTPError(secret_url, 403, "Forbidden", {}, io.BytesIO())
    monkeypatch.setattr(
        rt.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(rt.RuntimeConfigError) as captured:
        rt._http_get_bytes(secret_url, max_bytes=100)

    assert "secret" not in str(captured.value)
