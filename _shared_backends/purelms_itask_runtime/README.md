# `purelms-itask-runtime`

Shared runtime helper for PureLMS InteractiveTask **backend containers**.

A backend container is launched two ways and must satisfy the same
contract in both:

| | Local / sync (`DockerComposeExecutionBackend`) | Async / GCS (`CloudRunJobsExecutionBackend`) |
|---|---|---|
| Input | `PURELMS_INPUT_DIR/input.json` (mounted) | `PURELMS_INPUT_URI` (`gs://…`) |
| Output | `PURELMS_OUTPUT_DIR/output.json` (mounted) | upload to `PURELMS_OUTPUT_URI` |
| Progress | none (worker can't observe mid-run) | POST `ProgressCallback` |
| Completion | worker reads `output.json` off disk | POST `CompleteCallback` (authoritative) |

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

The mode is read from the environment's *shape* (which env vars are set;
whether the callback URL is `http(s)` vs the `file:///dev/null`
sentinel) — a backend never branches on "am I local or cloud".

On Cloud Run, the worker also supplies `PURELMS_INPUT_SHA256`,
`PURELMS_INPUT_SIZE_BYTES`, and `PURELMS_INPUT_GENERATION`. The runtime reads
the exact GCS generation with a size limit, then verifies its digest and byte
count before parsing. Local runs may omit all three. A partial or inconsistent
identity is a configuration error, not a best-effort fallback.

## Extras

`google-cloud-storage` + `google-auth` are needed ONLY on the async /
Cloud Run image (GCS I/O + OIDC-authed callbacks). They live under the
`cloud` extra and are imported lazily + guarded, so the local/dev path
runs without them. The deployed image installs
`purelms-itask-runtime[cloud]`.

## Why it lives in `_shared_backends/`

Per the backend contract, "shared utilities (callback client, envelope
loader, GCS helpers)" belong here once a second backend wants them —
`echo`, `energyplus_single_zone`, and `modelica_diagram` are wired to it. It is a uv
workspace member; container builds vendor its wheel into
`<slug>/backend/_vendor/`. Local builds also stage the sibling
`purelms-shared` wheel; release CI stages this runtime wheel and resolves the
published `purelms-shared` package from PyPI.
