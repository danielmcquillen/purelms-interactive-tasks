# InteractiveTask Authoring Guide

The deep reference for building a new InteractiveTask end-to-end.
Read [`README.md`](README.md) first for the big picture, [`CONTRIBUTING.md`](CONTRIBUTING.md) for the mechanics checklist, and this guide when you're ready to actually write code.

This guide is the author-facing reference for the InteractiveTask framework: the manifest schema, the three contract edges, the configuration layers, and the lifecycle — with worked examples throughout.

---

## Table of contents

1. [The 5-minute path](#the-5-minute-path)
2. [What you're actually building](#what-youre-actually-building)
3. [The manifest in depth (`interactive_task.yaml`)](#the-manifest-in-depth-interactive_taskyaml)
4. [The backend container contract](#the-backend-container-contract)
5. [The frontend bundle contract](#the-frontend-bundle-contract)
6. [The three configuration layers](#the-three-configuration-layers)
7. [Installation + lifecycle commands](#installation--lifecycle-commands)
8. [Testing](#testing)
9. [Trust tiers + execution mode](#trust-tiers--execution-mode)
10. [Versioning](#versioning)
11. [Common gotchas](#common-gotchas)
12. [Worked example: `echo` end-to-end](#worked-example-echo-end-to-end)
13. [Further reading](#further-reading)

---

## The 5-minute path

```bash
# 1. Copy the skeleton
cp -r _template my_first_task
cd my_first_task

# 2. Edit interactive_task.yaml — set slug, name, version, description.
#    Leave the rest of the manifest alone for now.
vim interactive_task.yaml

# 3. Rename the frontend placeholder to match your slug.
mv frontend/src/placeholder.ts frontend/src/my_first_task.ts
mv frontend/tests/placeholder.test.ts frontend/tests/my_first_task.test.ts

# 4. Declare the backend once in the authoritative inventory.
cd ..
# Add a [[backend]] record; workspace/build/test/release membership derives here.
vim backends.toml

# 5. Build + test.
just build my_first_task
just test my_first_task

# 6. Install into a local PureLMS.
cd ../purelms
source set-env.sh
uv run python manage.py install_interactive_task \
    ../purelms-interactive-tasks/my_first_task
```

You now have a registered InteractiveTask with the template's "hello from bundle X" placeholder UI. The rest of this guide is about replacing that placeholder with your domain code.

---

## What you're actually building

An InteractiveTask is a **paired triple**:

```
my_task/
├── interactive_task.yaml   ← the manifest (Layer 1 definition)
├── backend/                ← Python container (Dockerized)
│   ├── Dockerfile
│   ├── main.py             ← entrypoint
│   ├── pyproject.toml
│   ├── __metadata__.py     ← informational; runtime self-description
│   └── tests/
└── frontend/               ← single ES module bundle
    ├── package.json
    ├── src/my_task.ts      ← exports mount(element, config, helpers)
    └── tests/
```

These three pieces are **co-located** on purpose: a domain expert who knows EnergyPlus changes both the container's IDF parsing AND the frontend's parameter slider in one PR.

PureLMS sees four touchpoints when a learner submits a run:

```
┌────────────────────────────────────────────────────────────────────┐
│  1. Frontend bundle calls helpers.api.submit({parameter values})   │
│     ↓                                                              │
│  2. LMS atomically: creates SimulationRun row → debits credits →   │
│     enqueues dispatch (or runs sync container directly)            │
│     ↓                                                              │
│  3. Container reads the input envelope, does the work, writes the  │
│     output envelope. Local (sync): $PURELMS_INPUT_DIR/input.json → │
│     $PURELMS_OUTPUT_DIR/output.json. Async (Cloud Run/GCS): the    │
│     gs:// URIs in $PURELMS_INPUT_URI / $PURELMS_OUTPUT_URI, plus a  │
│     /complete callback to the worker.                              │
│     ↓                                                              │
│  4. LMS reads the output envelope, marks the run terminal,         │
│     frontend's pollStatus async-iterator yields the result         │
└────────────────────────────────────────────────────────────────────┘
```

That's it. No databases the container writes to. No shared state.

**You don't write that I/O or the callbacks by hand** — the shared
`purelms_itask_runtime` helper (in `_shared_backends/`) abstracts the
local-dir-vs-GCS-URI split and the progress/`complete` worker callbacks,
so the same container satisfies the contract on both the local
DockerCompose path and managed Cloud Run Job/Service paths with no
mode-branching. Copy `_template/backend/main.py` — it wires
`RuntimeLocation` / `read_input_envelope` / `make_progress_reporter` /
`write_output_envelope`. The hand-rolled `input.json` / `output.json`
snippets later in this guide show the envelope *shapes* the helper reads
and writes; you normally call the helper rather than reproducing them.

---

## The manifest in depth (`interactive_task.yaml`)

The manifest is the **source of truth** for everything the LMS knows about your task. It's read at install time by `manage.py install_interactive_task`, validated against the v1 schema, and persisted on the `SimulationBackendRegistration` row alongside its sha256.

### Top-level required fields

```yaml
schema_version: "purelms.interactive_task.v1"

slug: my_task              # snake_case, ≤64 chars, no hyphens
name: My Task              # human-readable display name
version: 0.1.0             # semver; bump when you change the manifest
description: |             # markdown OK
  One paragraph explaining what the task does and what learners get out of it.
```

**Slug rules** (enforced by the installer):

- 1-64 characters
- lowercase alphanumeric + underscore only
- no leading or trailing underscore
- no hyphens (the install command converts to hyphens for the Docker image name)

The same slug appears in three places:

1. `interactive_task.yaml`'s `slug:` field
2. The directory name: `purelms-interactive-tasks/<slug>/`
3. PureLMS's `InteractiveTaskBlock.simulation_backend_slug` field (the string FK)

All three MUST match exactly. The Docker image name is the only place the slug appears hyphenated: `purelms-itask-<slug-with-hyphens>:<version>`.

### The `backend:` section

```yaml
backend:
  # image: <registry>/<name>:<tag>     (optional — see below)
  credit_cost: 5                       # credits debited per successful run
  trust_tier: platform                 # platform | verified | community
  execution_mode: sync                 # v1 accepts only "sync"
  progress_reporting: none             # none | percentage
  default_timeout_seconds: 600
  # max_timeout_seconds: 900            (optional authoring headroom)
  # default_memory_limit: "2Gi"        (optional override; default 2Gi)
  # default_cpu_limit: "1.0"           (optional override; default 1.0)
```

| Field | Purpose | Validation |
|---|---|---|
| `image` | Container image URI. **Optional.** If absent, the installer derives `<registry>/purelms-itask-<slug-with-hyphens>:<version>` using the `--registry` flag's value. Set this explicitly only when your registry has an unusual layout. | Free-form string |
| `credit_cost` | Compute credits debited per accepted run. Refunded on `FAILED_RUNTIME`, `TIMED_OUT`, or `CANCELLED`; kept on `FAILED_SIMULATION`. Zero means free runs. | Non-negative integer |
| `trust_tier` | Friendly form. Mapped at install time to `SimulationTrustTier`: `platform` → tier 1, `verified` → tier 2, `community` → tier 3. Only tier 1 is currently launchable. | One of the three values |
| `execution_mode` | v1 accepts ONLY `"sync"`; `"async"` returns an install error. This is a domain-execution declaration, not the provider transport: managed Job and Service routes are asynchronous to the learner through GCS and callbacks. | `"sync"` |
| `progress_reporting` | Whether the wrapped tool exposes a genuine measure of completed work. `none` gives the learner an animated indeterminate bar; `percentage` permits a determinate bar after the first callback. The backend, not the browser, owns throttling. | `none` (default) or `percentage` |
| `default_timeout_seconds` | Wall-clock budget per run. Container is hard-killed after this. | Positive integer |
| `max_timeout_seconds` | Optional hard ceiling for a placement override. Provider Service request/deadline configuration is derived from this maximum, not only the default. | Integer ≥ default |
| `default_memory_limit` | Container memory cap (Cloud Run / k8s syntax: `"2Gi"`, `"512Mi"`). | String |
| `default_cpu_limit` | Container vCPU cap (e.g. `"1.0"`, `"0.5"`). | String |

### The `deployment:` section

```yaml
deployment:
  portable_contract: purelms.portable_container.v1
  required_capabilities:
    - callbacks
  # provider_allowlist: [cloud_run_service, cloud_run_job]
```

All managed tasks use the portable container contract. Declare only capabilities
the task genuinely needs; `callbacks` and `percentage_progress` are examples.
Normally omit `provider_allowlist` so the operator can use any verified provider
that satisfies the contract. Add it only for an actual provider constraint.
Catalog admission rejects a route that cannot satisfy these declarations.

### The `frontend:` section

```yaml
frontend:
  bundle: my_task.js       # filename inside frontend/dist/ after esbuild
  mount_contract: purelms.interactive_mount.v1
```

`bundle` is the filename the LMS dispatcher will dynamic-import from `/static/backends/<slug>/<version>/<bundle>`. It must exist in `frontend/dist/` after `npm run build` (or `just frontend-build <slug>`). The versioned path prevents a new release from replacing the UI for an older pinned block. `mount_contract` declares the browser-host interface implemented by the bundle; use `purelms.interactive_mount.v1` for all current tasks. Older v1 manifests that omit it are interpreted as v1 for replay compatibility.

### Progress reporting

Progress capability belongs to `backend:` because it describes the wrapped
tool, not the frontend. Use `none` when work cannot be measured honestly. Use
`percentage` only when the tool exposes genuine completed/total work (for
example, EnergyPlus's Runtime API progress callback). Never advance a bar from
elapsed wall time unless elapsed time is itself the domain's completed-work
measure.

Every backend is responsible for suppressing a noisy tool callback stream. The
standard `make_progress_reporter()` does that at the backend boundary: raw
values are floored to `0/25/50/75/100`, each milestone is emitted at most once,
the envelope's `progress_min_interval_seconds` is respected, and terminal
`100` is never delayed. The callback remains best-effort; completion is still
authoritative.

The browser does not reinterpret the manifest. It calls the LMS-owned progress
controller's `update(status.progress_pct, label)`. The controller was already
bound to the backend capability: `none` stays animated and indeterminate;
`percentage` becomes determinate only after a non-null percentage arrives.

### Parameters (Layer 1 schema)

Parameters are the values learners supply at submission time. The manifest declares the **shape** (types, defaults, valid ranges); the course author tightens or restricts them per-block at Layer 2; the learner picks values at Layer 3.

```yaml
parameters:
  - name: glazing_u_value
    type: number
    label: Glazing U-value
    description: Heat-transfer coefficient of the window assembly.
    unit: "W/m²K"
    min: 0.5
    max: 6.0
    step: 0.1
    default: 2.5
    required: true

  - name: climate_zone
    type: enum
    label: Climate zone
    description: ASHRAE climate zone.
    choices:
      - {value: "4A", label: "Mixed-humid"}
      - {value: "5A", label: "Cool-humid"}
      - {value: "6A", label: "Cold-humid"}
    default: "5A"
    required: true

  - name: include_shading
    type: boolean
    label: Include shading
    description: Apply the building's exterior shading mask.
    default: false

  - name: notes
    type: string
    label: Notes
    description: Free-form notes saved with the run.
    default: ""
```

**Allowed parameter types** (v1): `number`, `string`, `boolean`, `enum`.

**Per-type validation rules:**

| Type | Required fields | Optional fields |
|---|---|---|
| `number` | `name`, `type`, `label` | `default`, `description`, `unit`, `min`, `max`, `step`, `required` |
| `string` | `name`, `type`, `label` | `default`, `description`, `required` |
| `boolean` | `name`, `type`, `label` | `default`, `description`, `required` |
| `enum` | `name`, `type`, `label`, **`choices`** (non-empty list of `{value, label}` mappings) | `default`, `description`, `required` |

If `min`/`max` are set for a `number` parameter and `min > max`, the install fails. Duplicate parameter `name`s fail the install too.

### Outputs (Layer 1 schema)

Outputs are the result values your container writes to `output.json` for the LMS + frontend to consume.

```yaml
outputs:
  - name: annual_heating_kWh
    type: number
    label: Annual heating energy
    description: Total heating energy across the simulation period.
    unit: kWh
    display_hint: value-card

  - name: annual_cooling_kWh
    type: number
    label: Annual cooling energy
    unit: kWh
    display_hint: value-card

  - name: status_message
    type: string
    label: Simulation status
    display_hint: alert
```

| Field | Purpose |
|---|---|
| `name` | Must match the key your container writes into `output.envelope.outputs` |
| `type` | One of `number`, `string`, `boolean`, `enum` |
| `label` | Human-readable column heading / card title |
| `unit` | Display unit; the LMS appends to numeric outputs in the UI |
| `display_hint` | UI rendering hint (`value-card`, `alert`, `table-cell`, ...). v1 only renders `value-card` and free-form text; richer hints are v2 work. |

### `lms_context_used` — which envelope fields you read

The container's input envelope (`SimulationInputEnvelope`) carries metadata about who's running it, where they're running it, and what placement triggered the run. Most containers ignore most of this — they only care about `parameters`. But evidence-bearing or audit-aware containers may want to read e.g. `student_id` and `course_id`.

Declare exactly the fields your container reads:

```yaml
lms_context_used:
  - run_id
  - backend_slug
  - backend_version
  # - student_id
  # - course_id
  # - block_id
  # - unit_block_id
```

The v1 allowed set is exactly these seven fields (matches `purelms_shared.envelopes.SimulationInputEnvelope`). The installer rejects unknown field names with a clear error. **Declaring more than you actually use isn't wrong**, just noisy; declaring less is fine too (your container still receives the full envelope — `lms_context_used` is documentation, not a filter).

### `lms_outcomes` — outcome-mapping rules

This is the declarative interface that lets course authors say "passing = `annual_heating_kWh < 10000`" without your container caring about pass/fail semantics — the LMS computes the grade from your outputs.

- **At install time**, the installer validates that every `outputs.NAME` referenced by a rule corresponds to a declared output in your manifest. Dangling references fail the install.
- **At completion time**, `_on_run_succeeded` evaluates the rules against your backend's `outputs` and writes the standardized `(passed, score, completion)` triple onto the learner's `InteractiveTaskAttempt`. A `passed` rule that fails records `success_status=FAILED` even on a `SUCCESS` envelope.

**If you omit `lms_outcomes` (or set it to `{}`), the default is:** `status == SUCCESS → passed = true, completion = 1.0, score = null`. Use explicit rules when domain outputs determine success; EnergyPlus and Modelica are examples. The default remains useful for open-schema utilities such as Echo.

Rule shape — each standard outcome field (`passed` / `score` / `completion`) maps to a `{source, transform, ...args}` rule:

```yaml
lms_outcomes:
  passed:
    source: outputs.annual_heating_kWh
    transform: less_than
    threshold: 10000
  score:
    source: outputs.annual_heating_kWh
    transform: normalize_inverse   # lower heating energy → higher score
    bounds: [2000, 10000]
```

Transforms: `passthrough`, `equals` (`value`), `less_than` / `greater_than` (`threshold`), `in_range` (`bounds: [min, max]`) → bool; `normalize` / `normalize_inverse` (`bounds: [min, max]`) → 0.0-1.0 float. A configured `passed` rule that cannot be evaluated fails closed; score/completion remain best-effort. Install-time validation catches the common dangling-reference typo.

(If you still want backend-side pass/fail — e.g. for logic the rule transforms can't express — emit `OutputStatus.FAILED_SIMULATION` from the container; note the credit is NOT refunded on FAILED_SIMULATION, since the learner used the compute.)

### `requires_user_context`

```yaml
requires_user_context: false
```

A boolean documenting whether the backend depends on learner-identity context (`student_id`, etc.). Declarative only in v1 — doesn't affect execution. Set `true` if your backend reads any user-identity envelope fields.

### The full minimal valid manifest

```yaml
schema_version: "purelms.interactive_task.v1"

slug: my_task
name: My Task
version: 0.1.0
description: |
  A minimal InteractiveTask that does nothing useful but is structurally valid.

backend:
  credit_cost: 1
  trust_tier: platform
  execution_mode: sync
  progress_reporting: none
  default_timeout_seconds: 60

frontend:
  bundle: my_task.js

parameters: []
outputs: []
lms_context_used:
  - run_id
  - backend_slug
  - backend_version
requires_user_context: false
lms_outcomes: {}
```

The `echo` task ships almost exactly this manifest; it's a useful diff base.

---

## The backend container contract

> **Use the runtime helper — don't hand-roll this.** Everything in this
> section (envelope read, output write, callbacks, exit codes) is
> provided by `purelms_itask_runtime`: `RuntimeLocation.from_env()` →
> `read_input_envelope()` → your domain work → `write_output_envelope()`,
> with `make_progress_reporter()` for mid-run progress. Copy
> `_template/backend/main.py`. The code + envelope snippets below are
> **shape references** — they show *what* the helper reads/writes (and
> the env contract it follows) so you understand the wire format, not
> code you should reproduce by hand. Hand-rolling local-dir I/O (as the
> snippets literally show) silently breaks the async Cloud Run / GCS
> path, which reads/writes `gs://` URIs and posts a `/complete` callback.

### Three touchpoints, one contract

Every InteractiveTask backend follows the same three-step pattern (the
helper does each step in the right mode — local dir vs GCS URI):

1. **Read** the `SimulationInputEnvelope` — `read_input_envelope(location)`
   (from `$PURELMS_INPUT_URI` on the async path, else
   `$PURELMS_INPUT_DIR/input.json`).
2. **Do** the domain work.
3. **Write** the `SimulationOutputEnvelope` + signal completion —
   `write_output_envelope(location, output, envelope.context)` (to
   `$PURELMS_OUTPUT_URI` on the async path, else
   `$PURELMS_OUTPUT_DIR/output.json`; async also POSTs `/complete`).

The LMS treats a missing output as a contract violation regardless of exit code.

### Environment variables

The container is launched with these envs set (the helper reads them via
`RuntimeLocation.from_env()` — you don't read them directly):

| Variable | Default | Purpose |
|---|---|---|
| `PURELMS_INPUT_DIR` | `/purelms/input` | **Local/sync** read-only mount with `input.json` + any `InputFile` / `ResourceFile` materializations |
| `PURELMS_OUTPUT_DIR` | `/purelms/output` | **Local/sync** read-write mount; the helper writes `output.json` + artifacts here |
| `PURELMS_INPUT_URI` / `PURELMS_OUTPUT_URI` | (unset) | **Managed/GCS** canonical `gs://` identities used by either Job or Service; signed operation URLs carry the bytes (both set together or neither) |
| `PURELMS_INPUT_SHA256` / `PURELMS_INPUT_SIZE_BYTES` | (unset) | Expected immutable input-envelope identity. They must be set together; the runtime rejects a digest or size mismatch before domain code runs. |
| `PURELMS_INPUT_GENERATION` | (unset) | GCS object generation for a strict async input read. Required when input identity is supplied on the GCS path, preventing replacement races. |
| `PURELMS_RUN_ID` | (uuid) | The run's UUID, for log correlation. Not load-bearing — the canonical `run_id` lives inside the envelope. |

The defaults are convention; the Dockerfile in `_template/` declares `ENV PURELMS_INPUT_DIR=/purelms/input` etc. so your image self-documents. On the async path the worker overrides them with the `gs://` URIs.

### Reading the input envelope

```python
import os
import sys
from pathlib import Path

from purelms_shared.envelopes import SimulationInputEnvelope

input_dir = Path(os.environ.get("PURELMS_INPUT_DIR", "/purelms/input"))
input_path = input_dir / "input.json"

if not input_path.exists():
    print(f"missing input envelope at {input_path}", file=sys.stderr)
    sys.exit(1)

envelope = SimulationInputEnvelope.model_validate_json(input_path.read_text())
```

The envelope is a Pydantic v2 model with `extra="forbid"` and `frozen=True`. The fields you can read:

| Field | Type | Use case |
|---|---|---|
| `run_id` | UUID | Echo into outputs for correlation |
| `backend_slug`, `backend_version` | str | Sanity-check that you're being run as expected |
| `student_id`, `course_id` | int | Audit / per-learner branching (rare) |
| `block_id` | int | The configured `InteractiveTaskBlock.id` |
| `unit_block_id` | int \| None | The placement `UnitBlock.id` — distinct from `block_id` |
| `parameters` | dict | The learner-supplied values (validated against the manifest's `parameters` schema by the LMS before you see them) |
| `input_files` | list[InputFile] | Files materialized into the workspace (e.g. an IDF file the course author attached at the block level) |
| `resource_files` | list[ResourceFile] | Auxiliary files (e.g. weather data the LMS attached at trust-tier 1) |
| `context` | ExecutionContext | Per-run metadata (callback URLs, timeout, audience) — async only in v1 |

Use `envelope.expected_role(InputFileRole.TEMPLATE)` (or any other role enum / string) to filter input files by their declared role.

### Writing the output envelope

```python
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import (
    Message,
    SimulationOutputEnvelope,
    OutputArtifact,
)

output = SimulationOutputEnvelope(
    run_id=envelope.run_id,                # echo from input
    status=OutputStatus.SUCCESS,
    outputs={
        "annual_heating_kWh": 1234.5,      # keys must match manifest's outputs[]
        "annual_cooling_kWh": 678.9,
    },
    artifacts=[],                          # populated below if you have files
    messages=[
        Message.model_validate({
            "level": "info",
            "code": "MY_TASK.OK",
            "text": "Annual run completed in 12.3s.",
        }),
    ],
    metrics={"peak_kw": 4.2},              # optional scalar metrics
    runtime_seconds=12.3,
)

output_dir = Path(os.environ.get("PURELMS_OUTPUT_DIR", "/purelms/output"))
output_dir.mkdir(parents=True, exist_ok=True)
(output_dir / "output.json").write_text(output.model_dump_json(indent=2))
sys.exit(0)
```

### `OutputStatus` values

| Value | When to use |
|---|---|
| `SUCCESS` | The run completed and produced meaningful outputs. The LMS evaluates `lms_outcomes` to set `success_status`. With empty rules, `SUCCESS` maps to `PASSED`; with a `passed` rule, outputs that miss the criterion record `FAILED`. |
| `FAILED_SIMULATION` | The run completed but the simulation said "you got the wrong answer" — credit is kept, learner sees the outputs + messages. |
| `FAILED_RUNTIME` | The backend implementation or platform failed. Credit is **refunded**. Learner-correctable domain/model errors belong in `FAILED_SIMULATION`. |
| `CANCELLED` | The run was cancelled. Credit is refunded. |
| `TIMED_OUT` | Exceeded `default_timeout_seconds`. Credit refunded. |

### `Message` for surfacing learner-facing info

```python
Message(level="info",    code="ASHRAE.CLAMP", text="Setpoint clamped to 24°C")
Message(level="warning", code="EPLUS.WARN.5",  text="Cooling capacity underrun by 8%")
Message(level="error",   code="EPLUS.FATAL.3", text="No convergence after 50 iterations")
```

The LMS renders these verbatim in the unit page. Keep text under ~200 chars; longer detail goes into artifacts.

### `OutputArtifact` for files the learner can download

```python
OutputArtifact(
    filename="results.csv",
    mime_type="text/csv",
    uri="file:///purelms/output/results.csv",   # local path; LMS uploads to GCS in production
    sha256="abc123...",                          # 64 lowercase hex chars
    size_bytes=12345,
    label="Hourly heating + cooling results",
)
```

The LMS reads these from `output.json`, uploads them to GCS (in production deploys), and surfaces signed-URL download links to the learner.

### Exit codes + error semantics

| Exit code | Semantic |
|---|---|
| `0` + `output.json` written | Whatever `OutputStatus` says (SUCCESS / FAILED_SIMULATION / etc.) |
| `0` + `output.json` missing | Contract violation → LMS marks the run `FAILED_RUNTIME` and refunds |
| non-zero | LMS reads the last ~2000 bytes of stderr/stdout as the error message → marks `FAILED_RUNTIME` and refunds |

**Always** try to write `output.json` even on failure — set `status=FAILED_RUNTIME` and put a clear `Message` in the envelope. The clean envelope path gives the learner a better error UI than the log-tail fallback.

### Dockerfile patterns

The `_template/backend/Dockerfile` is the recommended starting point. Key patterns:

```dockerfile
FROM python:3.13-slim AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY _vendor /vendor/
# Install the shared runtime with its [cloud] extra. Local builds stage
# purelms-itask-runtime + purelms-shared in /vendor; release CI stages the
# runtime wheel and resolves published purelms-shared from PyPI. GCS and OIDC
# dependencies come from PyPI in both cases, so the SAME image meets the local
# DockerCompose and async Cloud Run / GCS contracts. Installing only
# purelms-shared, as older templates did, silently breaks the async path.
RUN pip install --no-cache-dir --find-links /vendor "purelms-itask-runtime[cloud]"
RUN pip install --no-cache-dir --find-links /vendor .

FROM python:3.13-slim

ARG PURELMS_IMAGE_VERSION="dev"
ARG PURELMS_IMAGE_REVISION="unknown"
ARG PURELMS_IMAGE_SOURCE="https://github.com/danielmcquillen/purelms-interactive-tasks"
ARG PURELMS_BACKEND_SLUG="my_task"
ARG PURELMS_TASK_VERSION="0.1.0"

LABEL org.opencontainers.image.version="${PURELMS_IMAGE_VERSION}" \
      org.opencontainers.image.revision="${PURELMS_IMAGE_REVISION}" \
      org.opencontainers.image.source="${PURELMS_IMAGE_SOURCE}" \
      io.purelms.interactive-task.slug="${PURELMS_BACKEND_SLUG}" \
      io.purelms.interactive-task.version="${PURELMS_TASK_VERSION}"

# Non-root user is non-negotiable.
RUN useradd --create-home --uid 1000 purelms
USER purelms
WORKDIR /home/purelms

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --chown=purelms:purelms main.py ./

ENV PURELMS_INPUT_DIR=/purelms/input
ENV PURELMS_OUTPUT_DIR=/purelms/output

ENTRYPOINT ["python", "main.py"]
```

**Non-negotiables:**

- **Non-root user (uid 1000).** The LMS runs containers without root capabilities.
- **No domain network dependency by default.** Local Docker launches with `--network=none`. Cloud Run allows the shared runtime to reach the platform's GCS and authenticated callback endpoints; your domain code must not treat that as permission for arbitrary outbound calls. If a task genuinely needs external data at run time, document and review the egress requirement and use `community` (or `verified` after sign-off), not `platform`, until the dependency is approved.
- **Read-only root filesystem.** The LMS will set this in production. Write only to `$PURELMS_OUTPUT_DIR` and `/tmp` (tmpfs).
- **Single-purpose image.** One InteractiveTask per image. No multi-entrypoint, no `if-elif` dispatch on env vars.
- **Inspectable image identity.** Preserve the template's OCI version,
  revision, source, backend-slug, and task-version labels. The build recipes
  stamp release/revision values; keep the task-version default synchronized
  with `interactive_task.yaml`.
- **Narrow build context.** Preserve the template's `.dockerignore`. Never send
  environment files, key material, tests, caches, or domain build scratch into
  the Docker build context.

For an official backend, add one record to `backends.toml`. That inventory
records its paths, image name, shared-contract floor, supported platform, and
native-payload type, and directly drives aggregate Just recipes, the release
matrix, and deployment registration. Do not add a parallel slug list elsewhere.

### `__metadata__.py` — informational self-description

Per-backend Python file that documents the runtime contract for drift detection and operator inspection. It is currently informational—the LMS does not read it at runtime—so keep it synchronized with the manifest.

```python
BACKEND_TYPE = "ENERGYPLUS"
BACKEND_NAME = "EnergyPlus Whole-Building Energy"
BACKEND_DESCRIPTION = "..."
BACKEND_VERSION = "0.1.0"

EXPOSED_PARAMETERS = [
    {"name": "glazing_u_value", "type": "number", "unit": "W/m²K"},
]
OUTPUT_METRICS = [
    {"name": "annual_heating_kWh", "type": "number", "unit": "kWh"},
]
SUPPORTS_STREAMING = False
```

---

## The frontend bundle contract

The frontend bundle is a single ES module that exports `mount(element, config, helpers)`. The PureLMS dispatcher dynamic-imports the bundle at runtime and calls `mount(...)` exactly once per placement.

### The `mount` signature

```typescript
import type { MountFn, MountHelpers } from "./contract";   // vendored

export const mount: MountFn = async (
  element: HTMLElement,
  config: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> => {
  // Render your UI into `element`. The bundle owns this element
  // completely from this point — the dispatcher never re-enters it.
};

export default mount;   // dispatcher accepts named OR default export
```

### What `helpers` exposes

```typescript
interface MountHelpers {
  api: {
    // POST a submission. Rejects with ApiError on non-2xx.
    submit(parameters: Record<string, unknown>): Promise<SubmissionOutcomeResponse>;

    // Yields run-status snapshots until terminal or signal aborts.
    pollStatus(
      runId: string,
      options?: { intervalSeconds?: number; maxAttempts?: number; signal?: AbortSignal },
    ): AsyncIterable<SimulationRunStatusResponse>;
  };

  // HTML-escape a string for safe textContent / innerHTML insertion.
  escape(value: string): string;

  ui: {
    // Bootstrap progress UI already bound to backend.progress_reporting.
    createProgressBar(): {
      readonly element: HTMLElement;
      update(pct: number | null, label?: string): void;
      indeterminate(label?: string): void;
      determinate(pct: number, label?: string): void;
      complete(label?: string): void;
      error(label?: string): void;
      remove(): void;
    };
    renderSubmissionError(error: unknown): HTMLElement | null;
  };

  // Per-mount diagnostics — read-only.
  meta: {
    bundle: string;             // the bundle filename
    unitBlockId: number;        // the UnitBlock placement PK
    creditCost: number | null;  // credits debited per run (from registration)
    backendAvailable: boolean | null;  // false → render "no longer available" state
    progressReporting: "none" | "percentage";
  };
}
```

**Hard rules for `helpers`:**

- **No direct `fetch()` calls.** Everything network-facing goes through `helpers.api`. The dispatcher injects the right URLs + CSRF + auth.
- **No DOM lookups outside `element`.** Don't `document.getElementById(...)` the LMS's nav or other blocks. You only own what's inside `element`.
- **`helpers.escape` is mandatory for any string you didn't generate yourself.** Even though `outputs` came from your own container, treat it as untrusted on principle. Use `textContent` (not `innerHTML`) wherever possible.

### The submission + polling flow

Official backends import the build-time helper at
`_shared_frontend/run_lifecycle.ts` for this repeated lifecycle. It restores a
prior run, presents `DISPATCHED` as environment startup rather than fabricated
progress, polls at the server-provided cadence, and renders a safe transport
failure. The helper is bundled into each task's own ES module; it is not a
runtime cross-task dependency. Keep domain-specific controls, visualization,
and terminal-result rendering in the task frontend.

```typescript
async function handleSubmit(parameters: Record<string, unknown>) {
  // 1. POST the submission.
  const outcome = await helpers.api.submit(parameters);

  if (outcome.is_complete || outcome.run === null) {
    // Synchronous backend (DockerCompose default in dev). The status
    // is already terminal; the outcome carries the result.
    renderTerminal(outcome);
    return;
  }

  // 2. Async: poll until terminal.
  const run = outcome.run;
  const progressHost = document.createElement("div");
  element.append(progressHost);
  const progress = helpers.ui.createProgressBar();
  progressHost.replaceChildren(progress.element);
  progress.update(null, "Starting the simulation environment…");
  for await (const status of helpers.api.pollStatus(run.id, {
    intervalSeconds: run.poll_interval_seconds || 2,
  })) {
    progress.update(status.progress_pct, status.progress_step);
    if (status.is_terminal) {
      renderTerminal(status);
      if (status.status === "success") progress.complete("Complete");
      else progress.error("Run could not complete");
      break;
    }
  }
}
```

### The Layer 2 config object

The second `mount` argument is the **per-block configuration** the course author set. Its shape mirrors your manifest's `parameters:` block, but each parameter carries five extra fields:

```typescript
config = {
  parameters: {
    glazing_u_value: {
      visible: true,        // false → don't render the input
      enabled: true,        // false → render but disabled
      default: 2.5,         // pre-fill value
      min: 1.0,             // tighten the manifest's min (course author's choice)
      max: 4.0,             // tighten the manifest's max
    },
    climate_zone: {
      visible: true,
      enabled: true,
      default: "5A",
      choices: [{value: "5A", label: "Cool-humid"}],  // subset of manifest's choices
    },
  },
  outputs: {
    // Same shape — visibility + display_hint per output.
  },
};
```

**Honor this config.** A bundle that ignores `visible: false` and renders the parameter anyway is violating the contract — the course author asked the LMS to hide that parameter, and the learner shouldn't see it.

Default to "if missing → show". A course author who hasn't customized doesn't get an empty form.

### Rendering outputs

When a status snapshot reaches `is_terminal: true`, its `outputs` field holds the values your container wrote into `output_envelope.outputs`. Keys match your manifest's `outputs[].name`.

```typescript
function renderTerminal(status: SimulationRunStatusResponse) {
  const outputs = status.outputs ?? {};
  // Match manifest's outputs[].display_hint to a renderer.
  for (const [key, value] of Object.entries(outputs)) {
    const card = document.createElement("div");
    card.className = "value-card";
    const labelEl = document.createElement("div");
    labelEl.textContent = key;   // ideally look up the label from manifest's metadata
    const valueEl = document.createElement("div");
    valueEl.textContent = String(value);   // escape via textContent
    card.append(labelEl, valueEl);
    element.append(card);
  }
}
```

The full manifest is available to the bundle via `config` (the LMS injects relevant Layer 1 metadata too — labels, units, display hints). The above is intentionally minimal; the echo bundle is a more complete example.

### Build tooling

The `_template/frontend/package.json` ships with esbuild + vitest + happy-dom. Build script:

```bash
npm run build         # esbuild src/<slug>.ts --bundle --minify ... --outfile=dist/<slug>.js
npm run watch         # incremental rebuild
npm run typecheck     # tsc --noEmit
npm run test          # vitest run
```

Or from the repo root:

```bash
just frontend-build my_task
just test my_task     # backend pytest + frontend vitest
```

### Framework choice

The framework you write the bundle in is **your decision**. The dispatcher contract is framework-agnostic — anything that compiles to a single ES module exporting `mount(...)` works.

Worked examples in this repo:

- **Vanilla TypeScript** (~2 KB) — `echo/frontend/src/echo.ts` — the simplest possible reference. A form + a submit button + polling. No external deps.
- **Vanilla TypeScript + Three.js** (~120 KB gzipped) — `energyplus_single_zone/frontend/src/energyplus_single_zone.ts` — parameter sliders with live 3D zone visualization. Shows how to integrate a rendering library while keeping the dispatcher contract straightforward (no framework bootstrapping ceremony). Gracefully degrades when WebGL is unavailable.
- **Vanilla TypeScript + Drawflow** — `modelica_diagram/frontend/src/modelica_diagram.ts` — typed component ports, canvas serialization, topology feedback, and FMU result rendering.
- (Future) **Angular / React / Vue** — any of these works with the dispatcher. The mounting layer adds some bootstrapping ceremony (e.g. `bootstrapApplication` + `createComponent({ hostElement })` for Angular) but the runtime contract is identical.

Pick the lightest framework that fits your task. The dispatcher loads bundles lazily, so bundle size only affects the page the task is placed on. **Three.js for rich 3D viz is a great fit; jumping to a full frontend framework just for a form is overkill.**

---

## The three configuration layers

PureLMS layers configuration at three points:

1. **Layer 1 — Definition** (`interactive_task.yaml`): the InteractiveTask author's defaults, types, units, and valid ranges. Shipped with the task in the InteractiveTasks repo.

2. **Layer 2 — Block-level author config** (`InteractiveTaskBlock.interaction_details`): the course author's per-block overrides. Tighter bounds, hidden parameters, restricted choices. **NOT** placement-scoped in v1 — same block in N `UnitBlock` placements has identical Layer 2 config.

3. **Layer 3 — Submission** (`SimulationRun.parameters`): the learner's actual values at submission time. Validated against L1 ∩ L2 by the LMS before credit debit or container execution.

**Validation (all three layers enforced as of 2026-05-29):** the LMS validates **L1 at install** (manifest schema, parameter types, lms_outcomes references), **L2 ↔ L1 at block edit/import** (a course author can no longer save an `interaction_details` config that widens past or references parameters outside your manifest — `InteractiveTaskBlock.clean()` + the importer serializer reject it), and **L3 ↔ L1 ∩ L2 at submit, before the credit debit** (`submit_simulation_run` rejects out-of-bounds / wrong-type / bad-enum / unknown / missing-required submissions and rolls back with no charge). One exception: an **open-schema** backend (`parameters: []`, like `echo`) accepts arbitrary parameters by design and skips L2/L3. **What this means for you as an author:** declare accurate `min`/`max`/`choices`/`required` in your manifest — those bounds are now enforced end-to-end. Keep your backend's parameter helpers as defense-in-depth (and the FAILED_RUNTIME refund remains the safety net for open-schema tasks), but you no longer have to rely on them as the *only* guard.

Concrete example. Manifest declares:

```yaml
parameters:
  - name: glazing_u_value
    type: number
    label: Glazing U-value
    min: 0.5
    max: 6.0
    default: 2.5
```

A course author creating a *very simple* exercise (Block A) sets L2:

```json
{
  "parameters": {
    "glazing_u_value": {"visible": true, "enabled": true, "min": 1.0, "max": 4.0, "default": 2.0}
  }
}
```

A course author creating a *more advanced* exercise (Block B) sets L2:

```json
{
  "parameters": {
    "glazing_u_value": {"visible": true, "enabled": true, "min": 0.5, "max": 6.0, "default": 2.5}
  }
}
```

Both blocks point at the same backend slug + same registration row. The framework supports **variation-as-blocks** (different `InteractiveTaskBlock` rows → different L2 configs) but NOT variation-as-placements in v1.

The frontend bundle should:

1. Render the L1 schema (from manifest) as the *outer envelope*
2. Apply the L2 overrides to constrain visibility / bounds / defaults
3. Submit the L3 values to `helpers.api.submit(...)`

---

## Installation + lifecycle commands

Five management commands drive an InteractiveTask's lifecycle. All live in `purelms` and are invoked from a PureLMS checkout.

### `install_interactive_task`

```bash
uv run python manage.py install_interactive_task ../purelms-interactive-tasks/my_task
```

Reads `my_task/interactive_task.yaml`, validates against the v1 schema, computes derived fields (`default_placement_config`, manifest sha256, image URI), writes or updates the `SimulationBackendRegistration` row, and stages the frontend bundle into `purelms/static/backends/<slug>/<version>/`.

**Useful flags:**

| Flag | When to use |
|---|---|
| `--dry-run` | Validate the manifest + show the planned action. No DB writes, no bundle staging. Run this first when iterating on a manifest. |
| `--registry <prefix>` | Override the container registry prefix (e.g. `us-central1-docker.pkg.dev/myproj/purelms`). Used only when `backend.image` is omitted from the manifest. |
| `--force` | Allow content-overwriting reinstall when an existing row at this `(slug, version)` is PRISTINE (zero `SimulationRun` FKs + zero `InteractiveTaskBlock` slug references). Refuses on non-pristine rows. |

Installation is additive: it never changes a current authoring default.

| Existing state | Behavior |
|---|---|
| No row at this `(slug, version)` | Create a new inactive candidate. |
| Existing row at this `(slug, version)` | Idempotent if manifest, digest, and bundle hash match. `--force` repairs only a pristine row. |
| A different version is active | Leave it active and install the new candidate beside it. |

### `activate_interactive_task`

```bash
# Verify the exact staged bundle without changing the authoring default.
uv run python manage.py activate_interactive_task my_task 0.2.0 \
  --actor-email operator@example.org --reason "Release smoke passed" --dry-run

# Make the verified version the default for newly authored blocks.
uv run python manage.py activate_interactive_task my_task 0.2.0 \
  --actor-email operator@example.org --reason "Release smoke passed"
```

Activation verifies the staged bundle hash, locks every registration for the slug, changes the single active default, and writes an audit event. Existing blocks, attempts, and runs retain their exact pins; use the Builder's explicit upgrade operation for a block that should move forward.

### `list_interactive_tasks`

```bash
uv run python manage.py list_interactive_tasks
uv run python manage.py list_interactive_tasks --slug my_task
uv run python manage.py list_interactive_tasks --active-only
```

Tabular view of installed InteractiveTasks. Shows active state + block-reference count + image URI.

### `deactivate_interactive_task`

```bash
uv run python manage.py deactivate_interactive_task my_task
```

Sets `is_active=False` on the slug's active row. Existing `InteractiveTaskBlock` placements continue to render but surface a "no longer available" state. To restore, reinstall the exact version if necessary, then use `activate_interactive_task` with a recorded operator identity and reason.

### `uninstall_interactive_task`

```bash
# The currently active row
uv run python manage.py uninstall_interactive_task my_task

# A specific version
uv run python manage.py uninstall_interactive_task my_task --task-version 0.1.0

# All inactive rows for the slug
uv run python manage.py uninstall_interactive_task my_task --all-inactive
```

This is development-only cleanup for an unreferenced registration. It
hard-deletes registration row(s). **Blocked** when:

- Any `SimulationRun` row has a PROTECT FK to the registration (historical evidence)
- For the active row: any `InteractiveTaskBlock` references the slug

(The flag is `--task-version` rather than `--version` because Django's `BaseCommand` reserves `--version` for printing the Django version.)

Do not use it to remove a production backend release. Production history must
follow the retirement workflow below so its pinned course, run, audit, deployments,
image, and bundle evidence remains intelligible.

### Retire and purge a production backend version

```bash
# First make an alternate version active, then block and retire the old one.
uv run python manage.py retire_interactive_task my_task 0.1.0 \
  --actor-email operator@example.org --reason "Superseded by 0.2.0"

# Inspect all retention and reference blockers. JSON is useful for an operator
# record; --retention-days is planning-only and does not shorten production policy.
uv run python manage.py plan_interactive_task_retirement my_task 0.1.0 \
  --format json

# Only after the plan is eligible, the static bundle is removed and deployed,
# and every exact provider deployment/image digest has been decommissioned:
uv run python manage.py purge_retired_interactive_task my_task 0.1.0 \
  --actor-email operator@example.org --reason "Retention elapsed" \
  --confirmation-token <token-from-plan> \
  --provider-artifacts-decommissioned --apply
```

Retirement blocks new launches and records an audit event; it does not delete
anything. The plan refuses active, referenced, or still-retained versions and
prints the exact static path, execution deployments, and image digest. Provider cleanup
and static-bundle deployment are separate, deliberate operations; the final
purge deletes only the now-unreferenced registration and leaves the audit event
as evidence. Run workspaces have their own retention policy and are never
removed by backend retirement.

---

## Testing

InteractiveTasks have **three test layers**, in order of speed:

### 1. Backend unit tests (fast — pytest, no Docker)

Run `main()` directly against a synthetic input envelope in a `tmp_path`:

```python
# echo/backend/tests/test_main.py
import json
from pathlib import Path
from uuid import uuid4

from purelms_shared.envelopes import SimulationInputEnvelope


def _envelope(parameters: dict) -> SimulationInputEnvelope:
    return SimulationInputEnvelope(
        run_id=uuid4(),
        backend_slug="echo",
        backend_version="0.1.0",
        student_id=1, course_id=1, block_id=1,
        parameters=parameters,
        context={  # minimal — sync backends don't actually use these
            "callback_url_progress": "file:///dev/null",
            "callback_url_complete": "file:///dev/null",
            "callback_audience": "unused",
            "timeout_seconds": 60,
        },
    )


def test_main_writes_success_output_envelope(tmp_path, monkeypatch):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "input.json").write_text(_envelope({"value": "hi"}).model_dump_json())

    monkeypatch.setenv("PURELMS_INPUT_DIR", str(input_dir))
    monkeypatch.setenv("PURELMS_OUTPUT_DIR", str(output_dir))

    from main import main
    assert main() == 0

    output = json.loads((output_dir / "output.json").read_text())
    assert output["status"] == "success"
    assert output["outputs"]["echoed_parameters"] == {"value": "hi"}
```

The full echo test suite (`echo/backend/tests/test_main.py`) is a worked example.

### 2. Frontend unit tests (fast — vitest + happy-dom)

Mount the bundle against a virtual DOM with mocked `helpers`:

```typescript
// echo/frontend/tests/echo.test.ts
import { describe, expect, it, vi } from "vitest";
import { mount } from "../src/echo";

describe("echo bundle", () => {
  it("renders a submit form", async () => {
    const element = document.createElement("div");
    const submit = vi.fn().mockResolvedValue({
      attempt: null,
      run: null,
      is_complete: true,
    });

    await mount(
      element,
      {},
      {
        api: { submit, pollStatus: async function* () {} },
        escape: (v) => v.replace(/</g, "&lt;"),
        meta: { bundle: "echo.js", unitBlockId: 42, creditCost: 1, backendAvailable: true },
      },
    );

    const button = element.querySelector("button");
    expect(button).not.toBeNull();
    expect(button!.textContent).toContain("Run");
  });
});
```

### 3. Container smoke tests (slower — real Docker)

`just smoke my_task` builds `linux/amd64`, runs the image against a representative real input envelope, and validates domain outputs. Docker Desktop emulates the same Cloud Run target on Apple Silicon. This catches mixed-architecture native assets, multi-stage Dockerfile bugs, and missing system dependencies.

For a real end-to-end smoke test against PureLMS, see `purelms/purelms/simulations/tests/test_docker_echo_integration.py` (marked `@pytest.mark.docker`).

### The just-test recipe

```bash
just test my_task           # backend pytest + frontend vitest
just test-runtime           # shared local-directory + Cloud Run/GCS transport
just test-all               # repository contracts, runtime, and every task
just smoke my_task          # build + real docker smoke (slow, opt-in)
just smoke-all              # every backend's real domain runtime
```

---

## Trust tiers + execution mode

### Trust tiers

| Manifest value | Enum | Meaning |
|---|---|---|
| `platform` | `TIER_1_PLATFORM` | Platform-maintained; evidence flows into credentials; runs with default isolation |
| `verified` | `TIER_2_VERIFIED` | Contributor-authored with PureLMS-side sign-off; evidence into credentials; tighter sandbox |
| `community` | `TIER_3_COMMUNITY` | Community-contributed, unverified; evidence does NOT flow into credentials; strictest sandbox |

**How to pick:**

- **`platform`** — only for InteractiveTasks the PureLMS team maintains directly (echo, EnergyPlus single-zone, the canonical ports).
- **`verified`** — a contributor task under review. It can be registered but is not currently launchable.
- **`community`** — unverified metadata. It is not launchable and can never satisfy learner evidence or credential-bearing goals.

When promoting from `community` → `verified`, bump the manifest version, install it as a candidate, verify it, then activate it explicitly. The promotion is auditable because every registration row carries `manifest_sha256` + `manifest_yaml`.

### Execution mode

The manifest accepts only `execution_mode: sync` at install. This describes the container invocation contract, not whether the deployment launches it asynchronously.

Two things are easy to conflate:

- On managed Cloud Run, the LMS stages input and grants signed object capabilities. A Job is launched through the Jobs API; a Service is invoked by one deterministic provider Cloud Task. Both finish through the same immutable output and callback contract.
- `purelms_itask_runtime` handles signed object I/O, output verification, and OIDC progress/completion callbacks. Task code does not write callback or GCS-client logic.

For now, declare `sync` and design every execution to fit inside the declared timeout. The deployment chooses local Docker or an asynchronous managed Job/Service independently of this manifest field.

---

## Versioning

Semver. The `version` field in your manifest drives the registration row's `version` column.

**When to bump:**

| Change | Bump |
|---|---|
| Container code change (no manifest change) | Patch (`0.1.0` → `0.1.1`). Install as a candidate, verify, then activate explicitly. |
| Parameter / output schema additive (new optional parameter, new output) | Minor (`0.1.x` → `0.2.0`). Course authors with existing blocks keep working; new blocks get the richer schema. |
| Parameter / output schema breaking (renamed parameter, removed output) | Major (`0.x.y` → `1.0.0`). Existing blocks may break — old block configs reference old parameter names. |
| Trust tier change (`community` → `verified`) | Minor. The promotion is itself a semver-significant event. |

**Registration immutability:** once any `SimulationRun` references a registration row OR any `InteractiveTaskBlock` references the slug, the row's content is **frozen** for audit. Mutating it via `--force` is blocked. Any real change requires a new version.

This is the framework's way of saying "you can't silently change what 'EnergyPlus 0.1.0' means after learners have run against it." Bump the version; the old runs keep pointing at the original row (preserved); new blocks use a verified version only after explicit activation.

---

## Common gotchas

1. **The slug appears in three places — all must match.** `interactive_task.yaml:slug`, directory name `<slug>/`, and `InteractiveTaskBlock.simulation_backend_slug`. The installer enforces the first two; the third is the course author's responsibility (they pick the slug when creating a block).

2. **The Docker image name uses hyphens, the slug uses underscores.** `s/_/-/g` conversion happens in exactly two places: the `justfile`'s `build` recipe and `Manifest.derive_image_uri()` in the installer. Both produce the same string by spec. Don't add a third.

3. **The envelope is `extra="forbid"`.** Adding a new field to `SimulationInputEnvelope` or `SimulationOutputEnvelope` is a **major version bump of `purelms-shared`** + lockstep update of all containers. Don't try to sneak fields through.

4. **The container MUST write `output.json` before exiting 0.** Missing file → contract violation → `FAILED_RUNTIME` → credit refund. Always wrap your main path with a `try/except` that writes a fallback envelope on unexpected exceptions.

5. **Pristine vs non-pristine registrations.** Once a block references your slug OR any run history exists, the registration is content-frozen. Iterating on the manifest during dev → either keep bumping the version OR delete the offending blocks first OR work on a fresh slug. The `--force` flag refuses on non-pristine rows by design.

6. **The frontend bundle must not import from PureLMS.** Vendor the types from `purelms/static/src/ts/sims/contract.ts` into your bundle; don't try to import from the LMS package. The bundle compiles standalone.

7. **Output keys MUST match the manifest's `outputs[].name` exactly.** The LMS uses the manifest to look up `display_hint` and `unit`. A key in `output.envelope.outputs` that isn't in the manifest gets dropped from the UI.

8. **Non-root user is required.** uid 1000 is the convention. If your domain code needs root for setup, do it in the builder stage of the Dockerfile and copy the resulting artifacts into a non-root runtime stage.

9. **Don't poll your own backend's output URLs from the frontend.** The frontend talks to PureLMS via `helpers.api`; PureLMS talks to your backend's output file. There is no direct frontend-to-container channel.

10. **The `helpers.api.pollStatus` async iterator yields once and then waits.** Don't `await` it inside a tight loop expecting multiple snapshots back-to-back — it polls at the interval the run reference specified. Use `for await (const status of iter) { ... }` and break when `status.is_terminal`.

---

## Worked example: `echo` end-to-end

The `echo` task is the canonical reference. It's intentionally trivial — it echoes the learner's parameters back as outputs — so the framework surface is foregrounded.

**Manifest** (`echo/interactive_task.yaml`):

```yaml
schema_version: "purelms.interactive_task.v1"

slug: echo
name: Echo
version: 0.1.0
description: |
  Permanent LMS-side integration-test fixture. Reads whatever
  parameters the learner submits and echoes them back as outputs.

backend:
  image: purelms-itask-echo:dev
  credit_cost: 1
  trust_tier: platform
  execution_mode: sync
  progress_reporting: none
  default_timeout_seconds: 30

frontend:
  bundle: echo.js

parameters: []   # echo accepts arbitrary parameters
outputs: []      # echo produces whatever the learner submitted
lms_context_used:
  - run_id
  - backend_slug
  - backend_version
requires_user_context: false
lms_outcomes: {}
```

**Container** (`echo/backend/main.py` — abridged):

```python
from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope


def main() -> int:
    # RuntimeLocation resolves local-dir vs GCS-URI I/O from the
    # environment — the backend never branches on the execution mode.
    location = RuntimeLocation.from_env()

    # 1. Read + parse the input envelope. A missing / invalid
    # envelope is a contract violation → exit 1.
    try:
        envelope = read_input_envelope(location)
    except Exception as exc:
        print(f"echo: could not read input envelope: {exc}", file=sys.stderr)
        return 1

    # 2. Do the "work" — just echo.
    output = SimulationOutputEnvelope(
        run_id=envelope.run_id,
        status=OutputStatus.SUCCESS,
        outputs={
            "echoed_parameters": envelope.parameters,
            "echoed_backend_slug": envelope.backend_slug,
        },
        artifacts=[],
        messages=[Message.model_validate({
            "level": "info", "code": "ECHO.OK",
            "text": f"Echoed {len(envelope.parameters)} parameters.",
        })],
        metrics={},
        runtime_seconds=0.0,
    )

    # 3. Write the output envelope (local dir or GCS URI) + signal
    # completion to the worker on the async path.
    write_output_envelope(location, output, envelope.context)
    return 0
```

**Frontend** (`echo/frontend/src/echo.ts` — abridged):

```typescript
export async function mount(
  element: HTMLElement,
  _config: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> {
  const root = document.createElement("div");
  const input = document.createElement("input");
  input.type = "text";
  input.value = "hello";

  const button = document.createElement("button");
  button.textContent = "Run echo";

  const status = document.createElement("div");
  const result = document.createElement("pre");

  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "Submitting…";

    const outcome = await helpers.api.submit({ value: input.value });

    if (outcome.is_complete || outcome.run === null) {
      status.textContent = "Done (sync).";
      button.disabled = false;
      return;
    }

    for await (const s of helpers.api.pollStatus(outcome.run.id)) {
      status.textContent = `Run ${s.id}: ${s.status}`;
      if (s.is_terminal) {
        result.textContent = JSON.stringify(s, null, 2);
        break;
      }
    }
    button.disabled = false;
  });

  root.append(input, button, status, result);
  element.replaceChildren(root);
}

export default mount;
```

**Install + smoke:**

```bash
# In purelms-interactive-tasks/
just build echo
just frontend-build echo

# In purelms/
source set-env.sh
uv run python manage.py install_interactive_task ../purelms-interactive-tasks/echo
SIMULATION_EXECUTION_BACKEND=docker_compose uv run python manage.py runserver
# Browse to a course with an echo block, click Run echo, see the JSON.
```

---

## Further reading

- **`purelms-shared`** — [the Pydantic envelope schemas](https://github.com/danielmcquillen/purelms-shared) (`SimulationInputEnvelope`, `SimulationOutputEnvelope`, etc.). The MIT-licensed contract package between LMS and containers.
- **`echo/`** — this repo's canonical reference InteractiveTask. Copy patterns from here; it's intentionally minimal.
- **`energyplus_single_zone/`** — a native binary, bundled input data, and a rich Three.js frontend.
- **`modelica_diagram/`** — a compiled FMU, typed diagram topology, and architecture-sensitive release assets.
- **`RELEASING.md` / `SECURITY.md`** — signed image provenance, native-asset gates, and private vulnerability reporting.

If something in this guide is wrong, file an issue or PR. The framework is young and this document is the contract — keeping it accurate is worth more than keeping it tidy.
