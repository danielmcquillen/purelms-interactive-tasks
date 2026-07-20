# `purelms-itask-runtime`

Shared runtime helper for PureLMS InteractiveTask **backend containers**.

A backend container is launched two ways and must satisfy the same
contract in both:

| | Local / sync (`DockerComposeExecutionBackend`) | Managed / signed object (Job or Service) |
|---|---|---|
| Input | `PURELMS_INPUT_DIR/input.json` (mounted) | GET `PURELMS_INPUT_FETCH_URL` |
| Output | `PURELMS_OUTPUT_DIR/output.json` (mounted) | PUT `PURELMS_OUTPUT_UPLOAD_URL`, then GET `PURELMS_OUTPUT_VERIFY_URL` |
| Progress | none (worker can't observe mid-run) | POST `ProgressCallback` |
| Completion | worker reads `output.json` off disk | immutable `output.json` is authoritative; POST `CompleteCallback` is retryable notification |

This package hides that split so a backend's `main.py` is identical
regardless of deployment:

```python
from purelms_itask_runtime import (
    RuntimeLocation,
    make_progress_reporter,
    read_input_envelope,
    write_output_envelope,
)

location = RuntimeLocation.from_env()
envelope = read_input_envelope(location)          # GCS or local read
on_progress = make_progress_reporter(envelope.context, started_at)  # None when sync
outputs = simulate(envelope.parameters, on_progress=on_progress)
write_output_envelope(location, output_envelope, envelope.context)  # writes + /complete
```

`make_progress_reporter()` is the backend-side throttling boundary. It accepts
the wrapped tool's raw 0–100 values, floors them to `0`, `25`, `50`, `75`, or
`100`, emits each milestone at most once, and also observes the envelope's
`progress_min_interval_seconds`. The final `100` is never delayed. Alternate
milestones can be passed explicitly when a backend has a documented need, but
the default keeps polling and callback traffic deliberately sparse.

This helper does not turn an indeterminate tool into a determinate one. A task
declares `backend.progress_reporting: percentage` only when its tool exposes a
genuine measure of completed work; otherwise it declares `none` and the LMS
renders an animated indeterminate bar.

The mode is read from the environment's *shape* (which env vars are set;
whether the callback URL is `http(s)` vs the `file:///dev/null`
sentinel) — a backend never branches on "am I local or cloud".

The container entrypoint has two lifecycle modes. The default runs the backend
script once for a Job. `PURELMS_RUNTIME_MODE=service` starts the shared bounded
HTTP server. The Service handler validates a narrow request body, refuses work
at/after its absolute deadline, runs the same script in a subprocess with a
request-private environment, and checks immutable output before acknowledging.
On redelivery it skips computation when output already exists and retries only
completion notification.

On Cloud Run, the worker also supplies `PURELMS_INPUT_SHA256`,
`PURELMS_INPUT_SIZE_BYTES`, and `PURELMS_INPUT_GENERATION`. The runtime reads
the exact signed object with a size limit, then verifies its digest and byte
count before parsing. The canonical `gs://` URIs are retained only for callback
and evidence identity. Local runs may omit all cloud fields.

## Extras

`google-auth` and its Requests transport are needed only on the async Cloud Run
image for OIDC-authenticated callbacks. They live under the `cloud` extra and
are imported lazily and guarded, so the local/dev path runs without them. The
deployed image installs
`purelms-itask-runtime[cloud]`.

## Why it lives in `_shared_backends/`

Per the backend contract, shared callback and envelope helpers belong here —
`echo`, `energyplus_single_zone`, and `modelica_diagram` are wired to it. It is a uv
workspace member; container builds vendor its wheel into
`<slug>/backend/_vendor/`. Local builds also stage the sibling
`purelms-shared` wheel; release CI stages this runtime wheel and resolves the
published `purelms-shared` package from PyPI.
