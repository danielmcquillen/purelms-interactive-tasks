"""
Backend container runtime contract — shared across InteractiveTask backends.

A PureLMS backend container is launched two ways, and must satisfy the
SAME contract in both:

- **Local / sync** (``DockerComposeExecutionBackend``): the worker mounts
  ``PURELMS_INPUT_DIR`` (read-only) + ``PURELMS_OUTPUT_DIR`` (writable),
  blocks on the container, then reads ``output.json`` off disk. The
  callback URLs in the envelope are the ``file:///dev/null`` sentinel.
- **Async / GCS** (``CloudRunJobsExecutionBackend``): the worker stages
  the input envelope to ``PURELMS_INPUT_URI`` (a ``gs://`` URI), passes
  ``PURELMS_OUTPUT_URI`` for where to write, and waits for HTTP callbacks
  — it can't see the container's filesystem. The envelope carries real
  ``https://`` worker callback URLs.

This module hides that split behind three calls so a backend's
``main.py`` is identical regardless of deployment:

1. :func:`read_input_envelope` — GCS download (URI mode) or local read.
2. :func:`make_progress_reporter` — a best-effort ``(pct, step)`` reporter
   that POSTs :class:`~purelms_shared.callbacks.ProgressCallback` to the
   worker, or ``None`` when there's no real endpoint (local/sync).
3. :func:`write_output_envelope` — GCS upload (URI mode) or local write,
   then — async only — POSTs the authoritative
   :class:`~purelms_shared.callbacks.CompleteCallback` so the worker
   finalizes the run. On the local path the worker reads the file
   directly, so no callback is sent.

The mode is read from the environment's *shape* (which env vars are set,
whether the callback URL is ``http(s)`` vs the ``file://`` sentinel) — a
backend never branches on "am I local or cloud".

Optional dependencies: ``google-cloud-storage`` (GCS I/O) and
``google-auth`` (OIDC tokens for the callbacks) are needed ONLY on the
async/Cloud Run image. They're declared under the ``cloud`` extra and
imported lazily + guarded, so the local/dev path (and the analytical
fallback that runs outside any container) works without them installed.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import urllib.request
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
# The completion callback is authoritative — retry it before giving up.
# Backoff is 1, 2, 4, 8s between 5 attempts (~15s total worst case).
_COMPLETE_MAX_ATTEMPTS = 5
_COMPLETE_BACKOFF_BASE_SECONDS = 1.0
_DEFAULT_MAX_ENVELOPE_BYTES = 5 * 1024 * 1024
_SHA256_HEX_LENGTH = 64


@dataclass(frozen=True)
class ObjectIdentity:
    """Content and object-store identity for one immutable envelope."""

    sha256: str
    size_bytes: int
    generation: int | None = None


class CompleteCallbackError(RuntimeError):
    """The authoritative ``/complete`` callback could not be delivered.

    Raised after exhausting retries. The output envelope is already in
    GCS, so the run is recoverable: the worker's sweeper finalizes it
    from ``run.output_envelope_uri``. The backend should let this
    propagate (exit non-zero) rather than swallow it.
    """


class RuntimeConfigError(RuntimeError):
    """The backend env contract is internally inconsistent.

    Raised by :meth:`RuntimeLocation.from_env` for a mixed I/O mode
    (one of the GCS URIs set without the other, or a non-``gs://``
    value). A platform misconfiguration — the backend should exit
    non-zero rather than run in a half-local / half-GCS state.
    """


def _validate_uri_mode(input_uri: str | None, output_uri: str | None) -> None:
    """Enforce: both GCS URIs (async) OR neither (local). No mixing."""
    if input_uri is None and output_uri is None:
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
        _validate_uri_mode(input_uri, output_uri)
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
        raw_bytes = _gcs_download_bytes(
            location.input_uri,  # type: ignore[arg-type]
            generation=location.input_generation,
            max_bytes=max_bytes,
        )
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

    URI mode: upload ``output.json`` to ``PURELMS_OUTPUT_URI`` and POST the
    authoritative :class:`CompleteCallback` (carrying that ``gs://`` URI)
    so the worker reads + finalizes the run. Dir mode: write
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
        generation = _gcs_upload_bytes(
            location.output_uri,  # type: ignore[arg-type]
            payload,
        )
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


def make_progress_reporter(
    context: ExecutionContext,
    started_at: float,
) -> Callable[[int, str], None] | None:
    """Build an ``on_progress(pct, step)`` reporter, or ``None`` if N/A.

    Returns ``None`` when ``callback_url_progress`` isn't a real
    ``http(s)`` endpoint — the case for synchronous backends, whose
    blocking run is observed via the output envelope, not callbacks. When
    ``None``, the domain code runs silently.

    Emission is strictly best-effort: any failure (no auth library,
    network error, non-2xx) is swallowed + logged to stderr so a flaky
    progress callback can never fail an otherwise-good run.

    Planned direction: when the shared ``ProgressCallback`` grows optional
    renderable-value fields, this reporter forwards them so the frontend
    can show interim results mid-run; today it forwards percent + step.
    """
    url = context.callback_url_progress
    if not url.lower().startswith(_HTTP_PREFIXES):
        return None

    def emit(pct: int, step: str) -> None:
        try:
            body = ProgressCallback(
                progress_pct=pct,
                step=step,
                elapsed_seconds=time.monotonic() - started_at,
            )
            _post_json(url, context.callback_audience, body.model_dump_json())
        except Exception as exc:
            print(
                f"purelms_itask_runtime: progress callback failed (non-fatal): {exc!r}",
                file=sys.stderr,
            )

    return emit


# ---------------------------------------------------------------------
# Worker callback HTTP client (OIDC-authed when google-auth is present)
# ---------------------------------------------------------------------


def _post_complete(
    context: ExecutionContext,
    *,
    output_envelope_uri: str,
    exit_code: int,
    identity: ObjectIdentity | None = None,
) -> None:
    """POST the authoritative completion callback — async path only.

    No-ops when ``callback_url_complete`` isn't an ``http(s)`` URL (the
    local/sync ``file:///dev/null`` sentinel): there, the worker reads the
    written envelope off disk.

    Unlike progress, completion is **authoritative + not best-effort**: a
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
                f"{attempt}/{_COMPLETE_MAX_ATTEMPTS} failed for "
                f"{output_envelope_uri!r}: {exc!r}",
                file=sys.stderr,
            )
            if attempt < _COMPLETE_MAX_ATTEMPTS:
                time.sleep(_COMPLETE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
        else:
            return

    msg = (
        f"complete callback undeliverable after {_COMPLETE_MAX_ATTEMPTS} "
        f"attempts to {url} (envelope at {output_envelope_uri})"
    )
    raise CompleteCallbackError(msg) from last_exc


def _post_json(url: str, audience: str, json_body: str) -> None:
    """POST a JSON body to a worker endpoint, OIDC-authed when possible."""
    headers = {"Content-Type": "application/json"}
    token = _fetch_id_token(audience)
    if token:
        headers["Authorization"] = f"Bearer {token}"
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


def _fetch_id_token(audience: str) -> str | None:
    """Mint a Google OIDC ID token for ``audience``; ``None`` if unavailable.

    Guarded so the local/dev paths (no ``google-auth`` installed, no
    metadata server) degrade to an unauthenticated POST rather than
    crashing — the worker rejects it, which the caller logs harmlessly.
    """
    try:
        # Lazy + guarded: ``google-auth`` is in the ``cloud`` extra,
        # present only on the async/Cloud Run image.
        from google.auth.transport import requests as ga_requests  # noqa: PLC0415
        from google.oauth2 import id_token  # noqa: PLC0415

        return id_token.fetch_id_token(ga_requests.Request(), audience)
    except Exception:
        return None


# ---------------------------------------------------------------------
# GCS I/O (guarded — google-cloud-storage is in the ``cloud`` extra)
# ---------------------------------------------------------------------


def _split_gs_uri(uri: str) -> tuple[str, str]:
    """``gs://bucket/path/to/obj`` -> ``("bucket", "path/to/obj")``."""
    rest = uri[len(_GS_PREFIX) :]
    bucket, _, blob = rest.partition("/")
    if not bucket or not blob:
        msg = f"malformed GCS URI {uri!r} (expected gs://bucket/object)"
        raise ValueError(msg)
    return bucket, blob


def _gcs_download_bytes(
    uri: str,
    *,
    generation: int | None,
    max_bytes: int,
) -> bytes:
    bucket, blob = _split_gs_uri(uri)
    from google.cloud import storage  # noqa: PLC0415 - cloud extra only

    client = storage.Client()
    obj = client.bucket(bucket).blob(blob, generation=generation)
    obj.reload()
    if obj.size is None or int(obj.size) > max_bytes:
        msg = f"GCS envelope {uri!r} exceeds {max_bytes} bytes or has unknown size"
        raise RuntimeConfigError(msg)
    kwargs = {"if_generation_match": generation} if generation is not None else {}
    return obj.download_as_bytes(**kwargs)


def _gcs_upload_bytes(uri: str, content: bytes) -> int:
    bucket, blob = _split_gs_uri(uri)
    from google.cloud import storage  # noqa: PLC0415 - cloud extra only

    client = storage.Client()
    obj = client.bucket(bucket).blob(blob)
    obj.upload_from_string(
        content,
        content_type="application/json",
        # One output object belongs to one SimulationRun. A provider retry or
        # duplicate execution must not overwrite the first attempt's bytes.
        if_generation_match=0,
    )
    if obj.generation is None:
        msg = f"GCS did not return a generation for immutable object {uri!r}"
        raise RuntimeConfigError(msg)
    return int(obj.generation)


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
