"""
Backend container runtime contract — shared across InteractiveTask backends.

A PureLMS backend container is launched two ways, and must satisfy the
SAME contract in both:

- **Local / sync** (``DockerComposeExecutionBackend``): the worker mounts
  ``PURELMS_INPUT_DIR`` (read-only) + ``PURELMS_OUTPUT_DIR`` (writable),
  blocks on the container, then reads ``output.json`` off disk. The
  callback URLs in the envelope are the ``file:///dev/null`` sentinel.
- **Async / object storage** (``CloudRunJobsExecutionBackend``): canonical
  ``gs://`` URIs identify the immutable envelopes, while short-lived signed
  URLs grant this run one input read, output create, and verification read.

This module hides that split behind three calls so a backend's
``main.py`` is identical regardless of deployment:

1. :func:`read_input_envelope` — signed download or local read.
2. :func:`make_progress_reporter` — a best-effort ``(pct, step)`` reporter
   that POSTs :class:`~purelms_shared.callbacks.ProgressCallback` to the
   worker, or ``None`` when there's no real endpoint (local/sync).
3. :func:`write_output_envelope` — signed upload or local write,
   then — async only — POSTs a required
   :class:`~purelms_shared.callbacks.CompleteCallback` notification so the
   worker promptly finalizes the run from the authoritative output envelope.
   On the local path the worker reads the file directly, so no callback is sent.

The mode is read from the environment's *shape* (which env vars are set,
whether the callback URL is ``http(s)`` vs the ``file://`` sentinel) — a
backend never branches on "am I local or cloud".

``google-auth`` with its Requests transport is required only for OIDC callback
tokens on Cloud Run and is imported lazily. Object I/O uses the standard
library against signed URLs, so backend identities hold no bucket permissions
and need no storage SDK.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from purelms_shared.callbacks import CompleteCallback
from purelms_shared.callbacks import ProgressCallback
from purelms_shared.envelopes import SimulationInputEnvelope

if TYPE_CHECKING:
    from collections.abc import Callable

    from purelms_shared.envelopes import ExecutionContext
    from purelms_shared.envelopes import SimulationOutputEnvelope

_GS_PREFIX = "gs://"
_HTTP_PREFIXES = ("http://", "https://")
# Worker callbacks are tiny JSON POSTs; a short timeout keeps a flaky
# endpoint from stalling the container's exit.
_CALLBACK_TIMEOUT_SECONDS = 10
# Completion notification is required for the prompt path; retry before giving up.
# Backoff is 1, 2, 4, 8s between 5 attempts (~15s total worst case).
_COMPLETE_MAX_ATTEMPTS = 5
_COMPLETE_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_MAX_ENVELOPE_BYTES = 5 * 1024 * 1024
_SHA256_HEX_LENGTH = 64
_PROGRESS_MAX = 100

# Tool callbacks can fire once per timestep/day/iteration. Only these
# learner-meaningful milestones cross the network by default. A backend can
# supply another ordered set when its domain genuinely needs finer reporting.
DEFAULT_PROGRESS_MILESTONES = (0, 25, 50, 75, _PROGRESS_MAX)


@dataclass(frozen=True)
class ObjectIdentity:
    """Content and object-store identity for one immutable envelope."""

    sha256: str
    size_bytes: int
    generation: int | None = None


class CompleteCallbackError(RuntimeError):
    """The required ``/complete`` notification could not be delivered.

    Raised after exhausting retries. The output envelope is already in
    GCS, so the run is recoverable: the worker's sweeper finalizes it
    from ``run.output_envelope_uri``. The backend should let this
    propagate (exit non-zero) rather than swallow it.
    """


class CallbackAuthenticationError(RuntimeError):
    """The backend could not mint the OIDC token required by the worker.

    HTTP callbacks never fall back to anonymous requests. Progress reporting
    logs this as non-fatal; completion delivery retries and then raises its
    normal :class:`CompleteCallbackError` with this exception as the cause.
    """


class RuntimeConfigError(RuntimeError):
    """The backend env contract is internally inconsistent.

    Raised by :meth:`RuntimeLocation.from_env` for a mixed I/O mode
    (one of the GCS URIs set without the other, or a non-``gs://``
    value). A platform misconfiguration — the backend should exit
    non-zero rather than run in a half-local / half-GCS state.
    """


def _validate_uri_mode(
    input_uri: str | None,
    output_uri: str | None,
    signed_urls: tuple[str | None, str | None, str | None],
) -> None:
    """Enforce a complete signed-URL cloud contract or a fully local one."""
    present_signed = [value is not None for value in signed_urls]
    if input_uri is None and output_uri is None:
        if any(present_signed):
            msg = "signed object URLs are not allowed without canonical GCS URIs"
            raise RuntimeConfigError(msg)
        return  # local dir mode — neither set.
    if input_uri is None or output_uri is None:
        # Exactly one is set → a mixed mode we must reject.
        have, missing = (
            ("PURELMS_INPUT_URI", "PURELMS_OUTPUT_URI")
            if input_uri is not None
            else ("PURELMS_OUTPUT_URI", "PURELMS_INPUT_URI")
        )
        msg = (
            f"mixed I/O mode: {have} is set but {missing} is not. The async "
            "path needs BOTH PURELMS_INPUT_URI and PURELMS_OUTPUT_URI as "
            "gs:// URIs; the local path sets neither."
        )
        raise RuntimeConfigError(msg)
    # Both set → both must be gs:// URIs.
    bad = sorted(
        name
        for name, uri in (
            ("PURELMS_INPUT_URI", input_uri),
            ("PURELMS_OUTPUT_URI", output_uri),
        )
        if not uri.startswith(_GS_PREFIX)
    )
    if bad:
        msg = (
            f"{bad} must be gs:// URIs for the async path; got "
            f"input={input_uri!r} output={output_uri!r}."
        )
        raise RuntimeConfigError(msg)
    if not all(present_signed):
        msg = (
            "Cloud I/O requires PURELMS_INPUT_FETCH_URL, "
            "PURELMS_OUTPUT_UPLOAD_URL, and PURELMS_OUTPUT_VERIFY_URL."
        )
        raise RuntimeConfigError(msg)
    if any(not value.startswith("https://") for value in signed_urls if value):
        msg = "Signed object URLs must use HTTPS"
        raise RuntimeConfigError(msg)


@dataclass(frozen=True)
class RuntimeLocation:
    """Where this run reads input + writes output, per the env contract.

    Built from the environment by :meth:`from_env`. ``input_uri`` /
    ``output_uri`` are the ``gs://`` URIs set by the Cloud Run Jobs path;
    ``input_dir`` / ``output_dir`` are the mount points the local
    DockerCompose path uses. Exactly one mode is active per run.
    """

    run_id: str
    input_uri: str | None
    output_uri: str | None
    input_dir: Path
    output_dir: Path
    input_sha256: str | None = None
    input_size_bytes: int | None = None
    input_generation: int | None = None
    input_fetch_url: str | None = None
    output_upload_url: str | None = None
    output_verify_url: str | None = None

    @classmethod
    def from_env(cls) -> RuntimeLocation:
        """Read the standard PureLMS backend env contract.

        Raises :class:`RuntimeConfigError` on a mixed / invalid mode:
        ``PURELMS_INPUT_URI`` and ``PURELMS_OUTPUT_URI`` must EITHER both
        be ``gs://`` URIs (async/GCS) OR both be absent (local dir). A
        half-set pair would let a backend read GCS but write a local path
        — then post that local path to ``/complete``, which the worker
        tries (and fails) to download as a ``gs://`` envelope.
        """
        input_uri = os.environ.get("PURELMS_INPUT_URI") or None
        output_uri = os.environ.get("PURELMS_OUTPUT_URI") or None
        input_fetch_url = os.environ.get("PURELMS_INPUT_FETCH_URL") or None
        output_upload_url = os.environ.get("PURELMS_OUTPUT_UPLOAD_URL") or None
        output_verify_url = os.environ.get("PURELMS_OUTPUT_VERIFY_URL") or None
        _validate_uri_mode(
            input_uri,
            output_uri,
            (input_fetch_url, output_upload_url, output_verify_url),
        )
        input_sha256 = os.environ.get("PURELMS_INPUT_SHA256") or None
        input_size_raw = os.environ.get("PURELMS_INPUT_SIZE_BYTES") or None
        input_generation_raw = os.environ.get("PURELMS_INPUT_GENERATION") or None
        input_size = int(input_size_raw) if input_size_raw is not None else None
        input_generation = (
            int(input_generation_raw) if input_generation_raw is not None else None
        )
        if (input_sha256 is None) != (input_size is None):
            msg = (
                "PURELMS_INPUT_SHA256 and PURELMS_INPUT_SIZE_BYTES must be set together"
            )
            raise RuntimeConfigError(msg)
        if input_sha256 is not None and (
            len(input_sha256) != _SHA256_HEX_LENGTH
            or any(ch not in "0123456789abcdef" for ch in input_sha256)
        ):
            msg = "PURELMS_INPUT_SHA256 must be 64 lowercase hexadecimal characters"
            raise RuntimeConfigError(msg)
        if input_size is not None and input_size < 0:
            msg = "PURELMS_INPUT_SIZE_BYTES must be non-negative"
            raise RuntimeConfigError(msg)
        if input_generation is not None and input_generation < 1:
            msg = "PURELMS_INPUT_GENERATION must be a positive GCS generation"
            raise RuntimeConfigError(msg)
        if (
            input_uri is not None
            and input_sha256 is not None
            and input_generation is None
        ):
            msg = "PURELMS_INPUT_GENERATION is required for strict GCS input"
            raise RuntimeConfigError(msg)

        return cls(
            run_id=os.environ.get("PURELMS_RUN_ID", "unknown"),
            input_uri=input_uri,
            output_uri=output_uri,
            input_dir=Path(os.environ.get("PURELMS_INPUT_DIR", "/purelms/input")),
            output_dir=Path(os.environ.get("PURELMS_OUTPUT_DIR", "/purelms/output")),
            input_sha256=input_sha256,
            input_size_bytes=input_size,
            input_generation=input_generation,
            input_fetch_url=input_fetch_url,
            output_upload_url=output_upload_url,
            output_verify_url=output_verify_url,
        )

    @property
    def uses_gcs_input(self) -> bool:
        return bool(self.input_uri and self.input_uri.startswith(_GS_PREFIX))

    @property
    def uses_gcs_output(self) -> bool:
        return bool(self.output_uri and self.output_uri.startswith(_GS_PREFIX))


def read_input_envelope(location: RuntimeLocation) -> SimulationInputEnvelope:
    """Read + parse the input envelope (GCS URI mode or local dir mode).

    Raises (``FileNotFoundError`` / GCS errors / ``pydantic.ValidationError``)
    on a missing or invalid envelope; the caller maps that to a non-zero
    exit (a contract violation the LMS surfaces via the log tail).
    """
    max_bytes = _max_envelope_bytes()
    if location.uses_gcs_input:
        raw_bytes, generation = _http_get_bytes(
            location.input_fetch_url,  # type: ignore[arg-type]
            max_bytes=max_bytes,
        )
        if (
            location.input_generation is not None
            and generation != location.input_generation
        ):
            msg = (
                "input envelope generation mismatch: "
                f"expected {location.input_generation}, got {generation}"
            )
            raise RuntimeConfigError(msg)
    else:
        input_path = location.input_dir / "input.json"
        if not input_path.exists():
            msg = f"missing input envelope at {input_path}"
            raise FileNotFoundError(msg)
        if input_path.stat().st_size > max_bytes:
            msg = f"input envelope exceeds {max_bytes} bytes"
            raise RuntimeConfigError(msg)
        raw_bytes = input_path.read_bytes()
    _verify_identity(
        raw_bytes,
        expected_sha256=location.input_sha256,
        expected_size=location.input_size_bytes,
        label="input envelope",
    )
    return SimulationInputEnvelope.model_validate_json(raw_bytes)


def write_output_envelope(
    location: RuntimeLocation,
    envelope: SimulationOutputEnvelope,
    context: ExecutionContext,
    *,
    exit_code: int = 0,
) -> None:
    """Write the output envelope, then signal completion on the async path.

    URI mode: upload the authoritative ``output.json`` to
    ``PURELMS_OUTPUT_URI`` and POST :class:`CompleteCallback` (carrying that
    ``gs://`` URI) so the worker promptly reads + finalizes the run. Dir mode: write
    ``PURELMS_OUTPUT_DIR/output.json``; the worker observes the file
    directly, so no callback is sent (the envelope's
    ``callback_url_complete`` is the ``file:///dev/null`` sentinel and
    :func:`_post_complete` no-ops on it).

    Raises :class:`CompleteCallbackError` on the async path if the
    ``/complete`` callback can't be delivered after retries. The envelope
    is already in GCS at that point, so the backend should let this
    propagate (exit non-zero) — the worker's sweeper salvages the run
    from the written envelope instead of refunding a good result.
    """
    payload = envelope.model_dump_json(indent=2).encode("utf-8")
    if len(payload) > _max_envelope_bytes():
        msg = f"output envelope exceeds {_max_envelope_bytes()} bytes"
        raise RuntimeConfigError(msg)
    identity = ObjectIdentity(
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    if location.uses_gcs_output:
        upload_generation = _http_put_bytes(
            location.output_upload_url,  # type: ignore[arg-type]
            payload,
        )
        verified, verify_generation = _http_get_bytes(
            location.output_verify_url,  # type: ignore[arg-type]
            max_bytes=_max_envelope_bytes(),
        )
        if verified != payload:
            msg = "uploaded output envelope did not verify byte-for-byte"
            raise RuntimeConfigError(msg)
        generation = verify_generation or upload_generation
        if generation is None:
            msg = "object storage did not return an output generation"
            raise RuntimeConfigError(msg)
        identity = ObjectIdentity(
            sha256=identity.sha256,
            size_bytes=identity.size_bytes,
            generation=generation,
        )
        output_ref = location.output_uri
    else:
        location.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = location.output_dir / "output.json"
        with output_path.open("xb") as output_file:
            output_file.write(payload)
        output_ref = str(output_path)

    _post_complete(
        context,
        output_envelope_uri=output_ref,
        exit_code=exit_code,
        identity=identity,
    )


class ProgressReporter:
    """Quantize and time-throttle one backend's progress callbacks.

    A wrapped tool may report at arbitrary frequency and precision. This
    backend-side adapter floors raw values to configured milestones and emits
    each milestone at most once while respecting the LMS-provided minimum
    interval. The terminal 100% milestone bypasses the time interval so a
    short run can still report that its backend work finished.

    Delivery remains best-effort: callback failures are logged and swallowed;
    the required completion callback is the authoritative lifecycle signal.
    """

    def __init__(
        self,
        *,
        context: ExecutionContext,
        started_at: float,
        milestones: tuple[int, ...] = DEFAULT_PROGRESS_MILESTONES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if (
            not milestones
            or milestones[0] != 0
            or milestones[-1] != _PROGRESS_MAX
            or tuple(sorted(set(milestones))) != milestones
            or any(value < 0 or value > _PROGRESS_MAX for value in milestones)
        ):
            msg = "progress milestones must be unique, increasing, and span 0 to 100"
            raise ValueError(msg)
        self._context = context
        self._started_at = started_at
        self._milestones = milestones
        self._clock = clock or time.monotonic
        self._last_emitted: int | None = None
        self._last_emitted_at: float | None = None

    def __call__(self, pct: int | float, step: str) -> None:
        """Accept raw tool progress and emit at most one quantized callback."""
        numeric = max(0.0, min(100.0, float(pct)))
        milestone = self._milestones[bisect_right(self._milestones, numeric) - 1]
        if self._last_emitted is not None and milestone <= self._last_emitted:
            return

        now = self._clock()
        interval = self._context.progress_min_interval_seconds
        if (
            milestone != _PROGRESS_MAX
            and self._last_emitted_at is not None
            and now - self._last_emitted_at < interval
        ):
            return

        # Mark before the best-effort POST. A failed progress notification is
        # not retried and must never slow or fail the simulation itself.
        self._last_emitted = milestone
        self._last_emitted_at = now
        try:
            body = ProgressCallback(
                progress_pct=milestone,
                step=step,
                elapsed_seconds=now - self._started_at,
            )
            _post_json(
                self._context.callback_url_progress,
                self._context.callback_audience,
                body.model_dump_json(),
            )
        except Exception as exc:
            print(
                f"purelms_itask_runtime: progress callback failed (non-fatal): {exc!r}",
                file=sys.stderr,
            )


def make_progress_reporter(
    context: ExecutionContext,
    started_at: float,
    *,
    milestones: tuple[int, ...] = DEFAULT_PROGRESS_MILESTONES,
) -> ProgressReporter | None:
    """Build a throttled backend reporter, or ``None`` for local sync runs.

    Raw tool updates are quantized to 0/25/50/75/100 by default and time-
    throttled using ``context.progress_min_interval_seconds``. Emission is
    strictly best-effort so progress can never fail an otherwise-good run.
    """
    url = context.callback_url_progress
    if not url.lower().startswith(_HTTP_PREFIXES):
        return None
    return ProgressReporter(
        context=context,
        started_at=started_at,
        milestones=milestones,
    )


# ---------------------------------------------------------------------
# Worker callback HTTP client (OIDC authentication is mandatory)
# ---------------------------------------------------------------------


def _post_complete(
    context: ExecutionContext,
    *,
    output_envelope_uri: str,
    exit_code: int,
    identity: ObjectIdentity | None = None,
) -> None:
    """POST the required completion notification — async path only.

    No-ops when ``callback_url_complete`` isn't an ``http(s)`` URL (the
    local/sync ``file:///dev/null`` sentinel): there, the worker reads the
    written envelope off disk.

    Unlike progress, completion notification is **not best-effort**: a
    successful run whose ``/complete`` is dropped would otherwise be swept
    to FAILED_RUNTIME + refunded despite a perfectly good ``output.json``
    sitting in GCS. So we retry with exponential backoff and, if it still
    can't be delivered, RAISE — the container exits non-zero, which (a)
    surfaces the failure in the Cloud Run Job execution and (b) lets the
    worker's sweeper salvage the run from the already-written envelope
    rather than refunding it.
    """
    url = context.callback_url_complete
    if not url.lower().startswith(_HTTP_PREFIXES):
        return
    body = CompleteCallback(
        output_envelope_uri=output_envelope_uri,
        exit_code=exit_code,
        output_sha256=identity.sha256 if identity else None,
        output_size_bytes=identity.size_bytes if identity else None,
        output_generation=identity.generation if identity else None,
    )
    json_body = body.model_dump_json()
    last_exc: Exception | None = None
    for attempt in range(1, _COMPLETE_MAX_ATTEMPTS + 1):
        try:
            _post_json(url, context.callback_audience, json_body)
        except Exception as exc:
            last_exc = exc
            print(
                f"purelms_itask_runtime: complete callback attempt "
                f"{attempt}/{_COMPLETE_MAX_ATTEMPTS} failed: {exc!r}",
                file=sys.stderr,
            )
            if attempt < _COMPLETE_MAX_ATTEMPTS:
                time.sleep(_COMPLETE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        else:
            return

    msg = f"complete callback undeliverable after {_COMPLETE_MAX_ATTEMPTS} attempts"
    raise CompleteCallbackError(msg) from last_exc


def _post_json(url: str, audience: str, json_body: str) -> None:
    """POST JSON to the worker with a mandatory Google OIDC identity token."""
    token = _fetch_id_token(audience)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    request = urllib.request.Request(
        url,
        data=json_body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(
        request,
        timeout=_CALLBACK_TIMEOUT_SECONDS,
    ) as response:
        response.read()


def _fetch_id_token(audience: str) -> str:
    """Mint the required Google OIDC token or raise an actionable error."""
    if not audience.strip():
        msg = "callback audience is empty; refusing an unauthenticated request"
        raise CallbackAuthenticationError(msg)
    try:
        # Lazy import: the ``google-auth[requests]`` transport is in the cloud
        # image only. HTTP callbacks are cloud-only, so absence is a deployment
        # failure.
        from google.auth.transport import requests as ga_requests  # noqa: PLC0415
        from google.oauth2 import id_token  # noqa: PLC0415

        token = id_token.fetch_id_token(ga_requests.Request(), audience)
    except Exception as exc:
        msg = f"could not mint callback OIDC token for audience {audience!r}: {exc}"
        raise CallbackAuthenticationError(msg) from exc
    if not token:
        msg = f"Google returned an empty callback OIDC token for {audience!r}"
        raise CallbackAuthenticationError(msg)
    return token


# ---------------------------------------------------------------------
# Signed object I/O
# ---------------------------------------------------------------------


def _http_get_bytes(url: str, *, max_bytes: int) -> tuple[bytes, int | None]:
    """Read a bounded signed object URL and return bytes plus generation."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(
            request, timeout=_CALLBACK_TIMEOUT_SECONDS
        ) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > max_bytes:
                msg = f"object envelope exceeds {max_bytes} bytes"
                raise RuntimeConfigError(msg)
            content = response.read(max_bytes + 1)
            generation_raw = response.headers.get("x-goog-generation")
    except urllib.error.HTTPError as exc:
        msg = f"signed object read failed with HTTP {exc.code}"
        raise RuntimeConfigError(msg) from None
    except urllib.error.URLError:
        msg = "signed object read failed at the transport layer"
        raise RuntimeConfigError(msg) from None
    if len(content) > max_bytes:
        msg = f"object envelope exceeds {max_bytes} bytes"
        raise RuntimeConfigError(msg)
    return content, int(generation_raw) if generation_raw else None


def _http_put_bytes(url: str, content: bytes) -> int | None:
    """Create an immutable output object through its signed PUT capability."""
    request = urllib.request.Request(
        url,
        data=content,
        headers={
            "Content-Type": "application/json",
            "x-goog-if-generation-match": "0",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_CALLBACK_TIMEOUT_SECONDS
        ) as response:
            response.read()
            generation_raw = response.headers.get("x-goog-generation")
    except urllib.error.HTTPError as exc:
        msg = f"signed object create failed with HTTP {exc.code}"
        raise RuntimeConfigError(msg) from None
    except urllib.error.URLError:
        msg = "signed object create failed at the transport layer"
        raise RuntimeConfigError(msg) from None
    return int(generation_raw) if generation_raw else None


def _max_envelope_bytes() -> int:
    """Configured hard bound for both input and output envelope bytes."""
    value = int(
        os.environ.get("PURELMS_MAX_ENVELOPE_BYTES", _DEFAULT_MAX_ENVELOPE_BYTES)
    )
    if value < 1:
        msg = "PURELMS_MAX_ENVELOPE_BYTES must be positive"
        raise RuntimeConfigError(msg)
    return value


def _verify_identity(
    content: bytes,
    *,
    expected_sha256: str | None,
    expected_size: int | None,
    label: str,
) -> None:
    """Fail closed when fetched bytes differ from their launch identity."""
    if expected_size is not None and len(content) != expected_size:
        msg = f"{label} size mismatch: expected {expected_size}, got {len(content)}"
        raise RuntimeConfigError(msg)
    if expected_sha256 is not None:
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            msg = f"{label} sha256 mismatch: expected {expected_sha256}, got {actual}"
            raise RuntimeConfigError(msg)
