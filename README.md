# purelms-interactive-tasks

Container-and-frontend pairs that the [PureLMS](https://github.com/danielmcquillen/purelms) (AGPL) platform launches as **InteractiveTasks** — the runtime concept defined by [ADR-0014](https://github.com/danielmcquillen/purelms-project/blob/main/docs/adr/0014-interactive-task-framework.md).

This repo is **MIT-licensed**. PureLMS itself is AGPL-3.0-or-later; the InteractiveTasks here are intentionally permissively licensed so educator-contributors can ship their domain expertise without copyleft friction. The data contract between PureLMS and these tasks is the Pydantic schema package [`purelms-shared`](https://github.com/danielmcquillen/purelms-shared) (also MIT).

## What's an InteractiveTask?

An **InteractiveTask** is a paired unit:

- A **backend container** — a Dockerized program that reads a `SimulationInputEnvelope`, runs domain code (EnergyPlus, an FMU, a bioinformatics pipeline, a code grader, ...), writes a `SimulationOutputEnvelope`, exits.
- A **frontend bundle** — a single ES module exporting `mount(element, config, helpers)`. PureLMS's dispatcher dynamic-imports the bundle into a `<div data-purelms-task-*>` placeholder on the learner's unit page.
- An **`interactive_task.yaml` manifest** — the single source of truth for the task's identity, deploy metadata, parameter schema, output schema, and outcome rules. The LMS reads it at install time.

The repo's old name was `purelms-backends`, which was a misnomer: each task ships its frontend in the same directory tree, so "backends" understated what's here. The current name reflects the actual unit — an **InteractiveTask** — that the LMS-side `InteractiveTaskBlock` model points at.

## Layout

Each InteractiveTask lives at `<slug>/` with two subdirectories: `backend/` for the container, `frontend/` for the ES module bundle the LMS dynamic-imports at runtime. Co-located so a domain author touches one directory tree.

```
purelms-interactive-tasks/
├── README.md                  (this file)
├── LICENSE                    (MIT)
├── CONTRIBUTING.md
├── pyproject.toml             (uv workspace; each task's backend/ is a member)
├── justfile                   (recipes: build, test, push per task)
├── _template/                 (skeleton for new InteractiveTasks)
├── _shared_backends/          (escape hatch — empty until needed; documents
│                               the "one container, many configured tasks"
│                               pattern for the day it arises)
├── echo/                      (stub test task — permanent test fixture)
│   ├── backend/
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── main.py
│   │   └── tests/
│   ├── frontend/
│   │   ├── package.json
│   │   └── src/echo.ts
│   └── interactive_task.yaml
├── energyplus_single_zone/    (first real InteractiveTask — Slice 3d work)
│   ├── backend/
│   ├── frontend/
│   └── interactive_task.yaml
└── ...
```

**Slug naming convention**: snake_case at the directory level and inside `interactive_task.yaml`'s `slug:` field (`energyplus_single_zone`, not `energyplus-single-zone`). The Docker image name derives a hyphenated alias at the boundary (`purelms-itask-energyplus-single-zone:<version>`); see [ADR-0014's Docker image naming section](https://github.com/danielmcquillen/purelms-project/blob/main/docs/adr/0014-interactive-task-framework.md#docker-image-naming) for the rule.

## The contracts (three edges)

1. **Container ↔ LMS** (file-based): the container reads `$PURELMS_INPUT_DIR/input.json` (a `SimulationInputEnvelope`) and writes `$PURELMS_OUTPUT_DIR/output.json` (a `SimulationOutputEnvelope`). Both schemas live in [`purelms-shared`](https://github.com/danielmcquillen/purelms-shared). For sync execution (local Docker), the LMS reads `output.json` after the container exits. For async execution (Cloud Run Jobs, Slice 4), the container additionally POSTs progress + completion callbacks; see [`purelms_shared.callbacks`](https://github.com/danielmcquillen/purelms-shared) for the bodies.

2. **Frontend ↔ LMS** (in-browser): the frontend bundle exports `mount(element, config, helpers)`. The LMS's dispatcher (in `purelms/static/src/ts/sims/`) dynamic-imports the bundle at runtime and calls `mount(...)`. The `helpers` arg gives the bundle typed access to `api.submit`, `api.pollStatus` (an async iterator that yields run-status snapshots until terminal — terminal snapshots carry the outputs), `escape` for HTML-safe text, and a `meta` object with the bundle filename + placement id.

3. **Frontend ↔ Container** (implicit schema contract via LMS): the bundle's `parameters` payload flows through the LMS into the container's `input.json`; the container's `outputs` flow back through the LMS to the bundle. Both sides agree on the shape via the manifest's `parameters:` and `outputs:` sections — no runtime negotiation.

[ADR-0014](https://github.com/danielmcquillen/purelms-project/blob/main/docs/adr/0014-interactive-task-framework.md) is the authoritative spec for all three edges + the four-category data model + the three-layer config model + the five-phase lifecycle.

## Adding a new InteractiveTask

1. `cp -r _template <your_slug>` (or `mkdir <your_slug>/{backend,frontend}` from scratch).
2. Fill in `<your_slug>/interactive_task.yaml` (identity, backend image URI, frontend bundle filename, parameters, outputs, lms_outcomes rules).
3. Add `<your_slug>/backend` to `pyproject.toml`'s `[tool.uv.workspace.members]`.
4. Implement `backend/main.py` + `backend/Dockerfile`. Read the envelope, do the work, write the output envelope.
5. Implement `frontend/src/<slug>.ts` exporting `mount(...)`. Build to `frontend/dist/<slug>.js`.
6. Add tests under `backend/tests/` and `frontend/tests/`.
7. Install into a PureLMS instance:
   ```bash
   cd path/to/purelms
   uv run python manage.py install_interactive_task ../purelms-interactive-tasks/<your_slug>
   ```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full checklist, and [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md) for the deep author-facing reference (every manifest field, every helper, every gotcha — the document to keep open while you build).

## License

MIT — see [LICENSE](LICENSE).
