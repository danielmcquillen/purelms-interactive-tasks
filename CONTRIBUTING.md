# Contributing to purelms-backends

Thanks for adding a backend. This doc covers the practical mechanics; the design rationale lives in [PureLMS's architecture refs](https://github.com/danielmcquillen/purelms-project/blob/main/docs/architecture/interactive-task-architecture.md).

## Repo structure

```
purelms-backends/
├── <your-slug>/
│   ├── backend/            # Python container
│   │   ├── Dockerfile
│   │   ├── pyproject.toml  # workspace member; deps live here
│   │   ├── __metadata__.py # runtime self-description
│   │   ├── main.py         # container entrypoint
│   │   ├── runner.py       # domain code
│   │   └── tests/
│   └── frontend/           # TypeScript bundle
│       ├── package.json
│       ├── tsconfig.json
│       ├── src/<slug>.ts   # exports mount(element, config, helpers)
│       └── tests/
```

The slug naming convention is `<domain>_<scope>` — e.g. `energyplus_single_zone`, `gel_electrophoresis_basic`. Underscores, not hyphens. The slug is what PureLMS's `InteractiveTaskBlock.simulation_backend_slug` references.

## Adding a backend

1. **Create the directory tree.** Use an existing backend (e.g. `echo/`) as a template.
2. **Add to the workspace.** Add `"your_slug/backend"` to `pyproject.toml`'s `[tool.uv.workspace.members]` array.
3. **Implement the container** (`backend/main.py`):
   - Read input envelope from `$PURELMS_INPUT_DIR/input.json`.
   - Parse it as `purelms_shared.envelopes.SimulationInputEnvelope`.
   - Do the work.
   - Write output as `purelms_shared.envelopes.SimulationOutputEnvelope` to `$PURELMS_OUTPUT_DIR/output.json`.
   - Exit 0 on success; non-zero on failure (the LMS reads the exit code and the log tail).
4. **Implement the frontend** (`frontend/src/<slug>.ts`):
   - Export a `mount(element: HTMLElement, config: object, helpers: PureLMSHelpers): () => void` function.
   - `helpers` carries the typed API client (submit, poll), HTML-escape util, and the run reference.
   - Return an optional teardown callback (called when the LMS navigates away).
5. **Write tests.** Backend: pytest under `backend/tests/`. Frontend: Vitest under `frontend/tests/`.
6. **Register in PureLMS** (Django admin):
   - Create a `SimulationBackendRegistration` row with `backend_slug`, `version`, `backend_image_uri` (registry-qualified container image), credit cost, trust tier, resource defaults.
7. **Build + push the image:**
   ```bash
   just build <slug>
   just push <slug>
   ```

## Local development loop

The fastest dev loop uses the `DockerComposeExecutionBackend` (set `SIMULATION_EXECUTION_BACKEND=docker_compose` in your local PureLMS settings):

1. `just build <slug>` builds your container locally.
2. `just frontend-build <slug>` builds the TS bundle to `<slug>/frontend/dist/<slug>.js` and copies it to PureLMS's static dir.
3. In PureLMS, create a course → unit → InteractiveTaskBlock with `simulation_backend_slug=<your-slug>`.
4. Submit a run from the learner UI; the LMS launches your container, your TS bundle renders the result.

## Testing

Per-backend test commands run from the workspace root:

```bash
just test <slug>           # backend pytest + frontend vitest
just test                  # all backends
```

## Conventions

- **Container size matters.** Aim for < 1GB images. Use multi-stage Dockerfiles. For EnergyPlus-class backends with large dependencies this is aspirational.
- **No outbound network by default.** PureLMS launches containers with `--network=none` unless `DOCKER_NETWORK_ENABLED=True`. Backends that need to fetch external data (weather files, etc.) need to declare that explicitly.
- **Idempotent and stateless.** A backend container's only state is the per-run workspace. No shared databases, no external state.
- **Deterministic outputs** for evidence-bearing credentials. Same input envelope + same image digest → same output envelope. Non-determinism (e.g. wall-clock-seeded RNG) belongs OFF.

## License

MIT. By contributing you agree your contribution is released under the MIT License.
