# purelms-backends

Container-and-frontend pairs that the [PureLMS](https://github.com/danielmcquillen/purelms) (AGPL) platform launches as interactive task backends.

This repo is **MIT-licensed**. PureLMS itself is AGPL-3.0-or-later; the backends here are intentionally permissively licensed so educator-contributors can ship their domain expertise without copyleft friction. The data contract between PureLMS and these backends is the Pydantic schema package [`purelms-shared`](https://github.com/danielmcquillen/purelms-shared) (also MIT).

## What's a backend?

A **backend** is one of:

- A **simulation backend** — a containerized program that consumes a `SimulationInputEnvelope`, runs domain code (EnergyPlus, an FMU, a bioinformatics pipeline, ...), writes a `SimulationOutputEnvelope`. Plus a paired frontend bundle the LMS mounts to collect parameters and display results.
- A **validation backend** — same shape, different intent (think evidence-checking, automated grading rubrics, file-format validators). Not yet implemented; the repo's naming generalizes to keep the door open.
- Future kinds — anything that fits the input-envelope / output-envelope / paired-frontend contract.

We chose `purelms-backends` (not `purelms-simulation-backends`) so the repo's name doesn't lock us into one category.

## Layout

Each backend lives at `<slug>/` with two subdirectories: `backend/` for the container, `frontend/` for the TypeScript bundle the LMS loads at render-time. Co-located so a domain author touches one directory tree.

```
purelms-backends/
├── README.md                  (this file)
├── LICENSE                    (MIT)
├── CONTRIBUTING.md
├── pyproject.toml             (uv workspace; each backend is a member)
├── justfile                   (recipes: build, test, push per backend)
├── echo/                      (stub test backend — permanent test fixture)
│   ├── backend/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── main.py
│   │   └── tests/
│   └── frontend/
│       ├── package.json
│       └── src/echo.ts
├── energyplus_single_zone/    (first real backend — Slice 3d work)
│   ├── backend/
│   └── frontend/
└── ...
```

**No `simulation_backends/` inner directory.** Each slug sits at the top level. The flat layout generalizes cleanly to non-simulation backends without forcing a directory rename.

## The contract

Every `backend/` container reads from `$PURELMS_INPUT_DIR/input.json` (a `SimulationInputEnvelope`) and writes to `$PURELMS_OUTPUT_DIR/output.json` (a `SimulationOutputEnvelope`). Both schemas live in [`purelms-shared`](https://github.com/danielmcquillen/purelms-shared).

For sync execution (local Docker), no callbacks are needed — the LMS reads `output.json` after the container exits. For async execution (Cloud Run Jobs), the container additionally POSTs progress + completion callbacks; see [`purelms_shared.callbacks`](https://github.com/danielmcquillen/purelms-shared) for the bodies.

Every `frontend/` bundle exports a `mount(element, config, helpers)` function that the LMS dispatcher calls. See [`purelms-project/docs/architecture/interactive-task-architecture.md`](https://github.com/danielmcquillen/purelms-project/blob/main/docs/architecture/interactive-task-architecture.md) for the full surface.

## Adding a new backend

1. `mkdir <slug>/{backend,frontend}` at the repo root.
2. Add the backend to `pyproject.toml`'s `[tool.uv.workspace.members]`.
3. Implement `backend/main.py` + `backend/Dockerfile`. Read the envelope, do the work, write the output envelope.
4. Implement `frontend/src/<slug>.ts` exporting `mount(...)`. Build to `frontend/dist/<slug>.js`.
5. Add tests under `backend/tests/` and `frontend/tests/`.
6. Register in PureLMS via Django admin: `SimulationBackendRegistration(backend_slug="<slug>", version="...", backend_image_uri="...", ...)`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full checklist.

## License

MIT — see [LICENSE](LICENSE).
