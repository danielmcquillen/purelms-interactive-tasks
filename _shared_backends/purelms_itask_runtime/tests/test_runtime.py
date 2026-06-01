"""
Tests for the shared backend runtime helper.

The GCS + HTTP seams are monkeypatched (``_gcs_download_text`` /
``_gcs_upload_text`` / ``_post_json``) so the contract logic — which mode
is chosen, what gets written, which callbacks fire — is exercised without
google-cloud-storage, google-auth, a network, or a real bucket.
"""

from __future__ import annotations

from uuid import uuid4

import purelms_itask_runtime.runtime as rt
import pytest
from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import make_progress_reporter
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import ExecutionContext
from purelms_shared.envelopes import SimulationInputEnvelope
from purelms_shared.envelopes import SimulationOutputEnvelope

_FILE_SENTINEL = "file:///dev/null"
_PROGRESS_URL = "https://worker.example/api/internal/sim/runs/abc/progress"
_COMPLETE_URL = "https://worker.example/api/internal/sim/runs/abc/complete"
_AUDIENCE = "https://worker.example"


def _context(*, progress_url: str, complete_url: str) -> ExecutionContext:
    return ExecutionContext(
        callback_url_progress=progress_url,
        callback_url_complete=complete_url,
        callback_audience=_AUDIENCE,
        timeout_seconds=30,
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
    monkeypatch.setenv("PURELMS_INPUT_URI", "gs://bucket/runs/1/input.json")
    monkeypatch.setenv("PURELMS_OUTPUT_URI", "gs://bucket/runs/1/output.json")
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
    monkeypatch.setattr(rt, "_gcs_download_text", lambda uri: env.model_dump_json())

    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
    )
    parsed = read_input_envelope(loc)
    assert parsed.backend_slug == "echo"


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
    assert '"progress_pct":42' in body.replace(" ", "")
    assert "Running" in body


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


def test_write_output_gcs_uploads_and_posts_complete(monkeypatch):
    uploads: list[tuple[str, str]] = []
    posts: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        rt,
        "_gcs_upload_text",
        lambda uri, text: uploads.append((uri, text)),
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
    )

    write_output_envelope(loc, _output_envelope(run_id), ctx, exit_code=0)

    # Uploaded the envelope to the output URI...
    assert len(uploads) == 1
    assert uploads[0][0] == output_uri
    assert '"status"' in uploads[0][1]
    # ...and POSTed the authoritative complete callback carrying that URI.
    assert len(posts) == 1
    url, audience, body = posts[0]
    assert url == _COMPLETE_URL
    assert audience == _AUDIENCE
    assert output_uri in body
    assert '"exit_code":0' in body.replace(" ", "")


def test_complete_callback_noops_on_file_sentinel(monkeypatch):
    posts: list[tuple] = []
    monkeypatch.setattr(rt, "_post_json", lambda *a: posts.append(a))
    ctx = _context(progress_url=_FILE_SENTINEL, complete_url=_FILE_SENTINEL)
    rt._post_complete(ctx, output_envelope_uri="/tmp/out/output.json", exit_code=0)
    assert posts == []


# ---------------------------------------------------------------------
# Completion delivery is authoritative — retry then raise (P1 review)
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


def test_write_output_gcs_raises_when_complete_undeliverable(monkeypatch):
    """The envelope is uploaded, but an undeliverable /complete propagates
    as CompleteCallbackError (so the backend exits non-zero)."""
    monkeypatch.setattr(rt, "_gcs_upload_text", lambda _uri, _text: None)
    monkeypatch.setattr(rt, "_post_json", _boom)
    monkeypatch.setattr(rt.time, "sleep", lambda _s: None)

    ctx = _context(progress_url=_PROGRESS_URL, complete_url=_COMPLETE_URL)
    loc = RuntimeLocation(
        run_id="r",
        input_uri="gs://bucket/runs/1/input.json",
        output_uri="gs://bucket/runs/1/output.json",
        input_dir=rt.Path("/purelms/input"),
        output_dir=rt.Path("/purelms/output"),
    )
    with pytest.raises(rt.CompleteCallbackError):
        write_output_envelope(loc, _output_envelope(uuid4()), ctx)


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
