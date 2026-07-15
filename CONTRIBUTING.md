# Contributing to purelms-interactive-tasks

Thanks for adding an InteractiveTask. This doc covers the practical mechanics; the deep author-facing how-to reference is [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md) (read order: README → CONTRIBUTING → BACKEND_AUTHORING_GUIDE).

## What you're building

An **InteractiveTask** is a paired unit: a backend Docker container + a frontend ES module bundle + a manifest that ties them together. PureLMS launches the container at submission time and mounts the bundle into the learner's browser when they view the unit.

The unit of configuration is the **`InteractiveTaskBlock`** row in PureLMS — that's where a course author writes the Layer 2 config (visibility, defaults, restricted bounds) for a specific configured exercise. Same InteractiveTask, different `InteractiveTaskBlock` rows → different configured exercises.

## Repo structure

```
purelms-interactive-tasks/
├── <your_slug>/
│   ├── interactive_task.yaml   # the manifest — Layer 1 definition
│   ├── backend/                # Python container
│   │   ├── Dockerfile
│   │   ├── pyproject.toml      # workspace member; deps live here
│   │   ├── __metadata__.py     # runtime self-description
│   │   ├── main.py             # container entrypoint
│   │   ├── runner.py           # domain code (optional, if main.py grows)
│   │   └── tests/
│   └── frontend/               # TypeScript / Angular / React / Vue bundle
│       ├── package.json
│       ├── tsconfig.json
│       ├── src/<slug>.ts       # exports mount(element, config, helpers)
│       └── tests/
```

## Slug naming convention

**`<domain>_<scope>` in snake_case** — e.g. `energyplus_single_zone`, `gel_electrophoresis_basic`, `python_grading_pep8`. Underscores, not hyphens. The slug is what PureLMS's `InteractiveTaskBlock.simulation_backend_slug` references.

Docker images derive a hyphenated alias at tooling boundaries: slug `energyplus_single_zone` → image `purelms-itask-energyplus-single-zone:<version>`. The justfile, release workflow, and LMS installer all apply the same `s/_/-/g` rule.

## Adding an InteractiveTask

1. **Copy the template.** `cp -r _template <your_slug>` is the fastest start.
2. **Fill in `interactive_task.yaml`.** Required: `schema_version: "purelms.interactive_task.v1"`, `slug`, `name`, `version`, `description`, `backend` (image, credit_cost, trust_tier, execution_mode, default_timeout_seconds), `frontend.bundle`. Recommended: `parameters`, `outputs`, `lms_context_used`, `lms_outcomes`. See the manifest section of [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md).
3. **Declare an official backend once.** Add its record to `backends.toml`.
   The uv workspace discovers `<your_slug>/backend` by glob, while aggregate
   build, frontend, test, smoke, release, and deploy registration all derive
   released membership from that inventory. Do not edit a Just or workflow
   slug list; none exists by design.
4. **Implement the container** (`backend/main.py`) using `purelms_itask_runtime` as shown in `_template/backend/main.py`:
   - Create a `RuntimeLocation` and read the `SimulationInputEnvelope` with `read_input_envelope()`.
   - Do the domain work.
   - Write the `SimulationOutputEnvelope` with `write_output_envelope()`. The helper selects local directories or Cloud Run/GCS and sends the completion callback.
   - Exit 0 on success; non-zero on failure (the LMS reads the exit code and the log tail).
5. **Implement the frontend** (`frontend/src/<slug>.ts`):
   - Export `function mount(element, config, helpers)` (named OR default export — the dispatcher accepts either).
   - Read the Layer 2 config from `config`. Honor `visible` / `enabled` / `default` / `min` / `max` / `choices` for each parameter.
   - Submit via `helpers.api.submit(parameters)`. Poll via `helpers.api.pollStatus(runId, options?)` — the async iterator yields run-status snapshots and the terminal snapshot carries the outputs in its `outputs` field. There is no separate `getOutputs` helper.
6. **Write tests.** Backend: pytest under `backend/tests/`. Frontend: Vitest (or your framework's test runner) under `frontend/tests/`. Both should run without PureLMS — test the envelope-read/write directly in the backend; mock `helpers` in the frontend.
7. **Build the image and frontend bundle:**
   ```bash
   just build <your_slug>
   just frontend-build <your_slug>
   ```
8. **Install into a PureLMS instance:**
   ```bash
   cd path/to/purelms
   uv run python manage.py install_interactive_task ../purelms-interactive-tasks/<your_slug>
   ```
   The install command reads `interactive_task.yaml`, validates the manifest schema, computes a default block-level config for the LMS-side admin form, stages the frontend bundle into `purelms/static/backends/<slug>/`, and creates a `SimulationBackendRegistration` row. See the installation + lifecycle section of [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md).

## Local development loop

The fastest dev loop uses the `DockerComposeExecutionBackend` (set `SIMULATION_EXECUTION_BACKEND=docker_compose` in your local PureLMS settings):

1. `just build <slug>` builds your container locally.
2. `just frontend-build <slug>` builds the ES module to `<slug>/frontend/dist/<slug>.js`.
3. `uv run python manage.py install_interactive_task ../purelms-interactive-tasks/<slug>` registers it on your local LMS (this also stages the bundle via `collect_backend_bundles`).
4. In PureLMS, create a course → unit → `InteractiveTaskBlock` with `simulation_backend_slug=<your_slug>` via Django admin.
5. Submit a run from the learner UI; the LMS launches your container, your frontend bundle renders the result.

## Testing

Per-InteractiveTask test commands run from the workspace root:

```bash
just test <slug>           # backend pytest + frontend vitest
just test-runtime          # shared local-directory + Cloud Run/GCS transport
just test-all              # repository contracts, runtime, and all tasks
just smoke <slug>          # build + execute the real linux/amd64 container
just smoke-all             # all real backend containers (slower)
```

The normal container target is `linux/amd64`, matching Cloud Run. Docker
Desktop runs it under emulation on Apple-Silicon Macs. Native binaries inside
an image—EnergyPlus and FMUs in particular—must match that target. Validibot's
GCP and self-hosted native-backend recipes enforce the same target. A
host-native optimization may be useful for a genuinely portable backend, but
must not wrap an x86-only payload in an arm64 image: that creates a
mixed-architecture image which may build successfully but fails when the
native binary is loaded.

Every backend build context also carries a `.dockerignore` that excludes tests,
local caches, environment files, private-key formats, and other workstation
noise. Keep it when copying the template.

## Pre-commit

This repo uses [pre-commit](https://pre-commit.com/) for style + safety nets (ruff lint + format, YAML + TOML syntax, trailing whitespace, etc.). The config is at [`.pre-commit-config.yaml`](.pre-commit-config.yaml). One-time setup after cloning:

```bash
uv sync --extra dev          # installs pre-commit alongside ruff + pytest
uv run pre-commit install    # wires the git hook
```

After that, `git commit` runs the hooks automatically. To run against the whole repo on demand:

```bash
git add -N .                              # makes untracked files visible to pre-commit
uv run pre-commit run --all-files
```

**Gotcha:** pre-commit only sees tracked files. The `git add -N .` step is the intent-to-add trick that lists new files without staging their content. Without it, hooks silently skip new files.

## Conventions

- **Container size matters.** Aim for < 1GB images. Use multi-stage Dockerfiles. For EnergyPlus-class tasks with large dependencies this is aspirational.
- **No outbound network by default.** PureLMS launches containers with `--network=none` unless explicitly enabled. Backends that need to fetch external data (weather files, etc.) need to declare that explicitly + accept a higher `trust_tier` cost.
- **Idempotent and stateless.** A container's only state is the per-run workspace. No shared databases, no external state.
- **Deterministic outputs** for evidence-bearing credentials. Same input envelope + same image digest → same output envelope. Non-determinism (e.g. wall-clock-seeded RNG) belongs OFF unless the manifest declares it.
- **Honor Layer 2 config in the frontend.** A bundle that ignores `config.parameters.<name>.visible` / `enabled` is a contract violation — the learner-facing form must respect what the course author chose.

## License

MIT. By contributing you agree your contribution is released under the MIT License.

Report suspected vulnerabilities through the private channel in
[`SECURITY.md`](SECURITY.md), not a public issue. Never include credentials,
private data, or exploit details in a contribution.
