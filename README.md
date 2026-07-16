# purelms-interactive-tasks

Container-and-frontend pairs that the [PureLMS](https://github.com/danielmcquillen/purelms) (AGPL) platform launches as **InteractiveTasks**.

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
├── SECURITY.md                (private vulnerability reporting + release hygiene)
├── backends.toml              (authoritative released-backend inventory)
├── pyproject.toml             (uv workspace; each task's backend/ is a member)
├── justfile                   (recipes: build, test, publish, deploy per task)
├── scripts/                   (release-asset validation + real container smoke tests)
├── _template/                 (skeleton for new InteractiveTasks)
├── _shared_backends/          (shared runtime for local-dir and signed object I/O)
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
├── energyplus_single_zone/    (reference native-binary InteractiveTask)
│   ├── backend/
│   ├── frontend/
│   └── interactive_task.yaml
└── ...
```

**Slug naming convention**: snake_case at the directory level and inside `interactive_task.yaml`'s `slug:` field (`energyplus_single_zone`, not `energyplus-single-zone`). The Docker image name derives a hyphenated alias at tooling boundaries (`purelms-itask-energyplus-single-zone:<version>`). The justfile, release workflow, and LMS installer all apply the same `s/_/-/g` rule.

## The contracts (three edges)

1. **Container ↔ LMS**: every backend uses `purelms_itask_runtime`. Local Docker reads `$PURELMS_INPUT_DIR/input.json` and writes `$PURELMS_OUTPUT_DIR/output.json`; Cloud Run Jobs reads and writes immutable `gs://` objects through `$PURELMS_INPUT_URI` and `$PURELMS_OUTPUT_URI`, then posts progress and completion callbacks. Both envelope schemas live in [`purelms-shared`](https://github.com/danielmcquillen/purelms-shared), and the backend's domain code is identical in both modes.

2. **Frontend ↔ LMS** (in-browser): the frontend bundle exports `mount(element, config, helpers)`. The LMS's dispatcher (in `purelms/static/src/ts/sims/`) dynamic-imports the bundle at runtime and calls `mount(...)`. The `helpers` arg gives the bundle typed access to `api.submit`, `api.pollStatus` (an async iterator that yields run-status snapshots until terminal — terminal snapshots carry the outputs), `escape` for HTML-safe text, and a `meta` object with the bundle filename + placement id.

3. **Frontend ↔ Container** (implicit schema contract via LMS): the bundle's `parameters` payload flows through the LMS into the container's `input.json`; the container's `outputs` flow back through the LMS to the bundle. Both sides agree on the shape via the manifest's `parameters:` and `outputs:` sections — no runtime negotiation.

The [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md) is the in-repo reference for all three edges + the four-category data model + the three-layer config model + the five-phase lifecycle.

## Adding a new InteractiveTask

1. `cp -r _template <your_slug>` (or `mkdir <your_slug>/{backend,frontend}` from scratch).
2. Fill in `<your_slug>/interactive_task.yaml` (identity, backend image URI, frontend bundle filename, parameters, outputs, lms_outcomes rules).
3. For an officially published backend, add one record to `backends.toml`.
   That inventory is the single source of released-backend membership for
   aggregate Just recipes, the GitHub Actions release matrix, and deployment
   registration. The uv workspace discovers `<your_slug>/backend`
   structurally.
4. Implement `backend/main.py` + `backend/Dockerfile` using the runtime helper in `_template/backend/main.py`. It handles both local-directory and Cloud Run/GCS envelope I/O.
5. Implement `frontend/src/<slug>.ts` exporting `mount(...)`. Build to `frontend/dist/<slug>.js`.
6. Add tests under `backend/tests/` and `frontend/tests/`.
8. Run `just test <your_slug>` and `just smoke <your_slug>`. The smoke recipe builds and executes the same `linux/amd64` target used by Cloud Run; Docker Desktop emulates it on Apple Silicon.
9. Install into a PureLMS instance:
   ```bash
   cd path/to/purelms
   uv run python manage.py install_interactive_task ../purelms-interactive-tasks/<your_slug>
   ```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full checklist, and [`BACKEND_AUTHORING_GUIDE.md`](BACKEND_AUTHORING_GUIDE.md) for the deep author-facing reference (every manifest field, every helper, every gotcha — the document to keep open while you build).

## Container architecture policy

The production and supported local target is `linux/amd64`. GitHub Actions,
the local build/publish recipes, the smoke runner, and Cloud Run Jobs all use
that target. On an Apple-Silicon Mac, Docker Desktop runs the image under
emulation. This is an intentional production-parity choice: EnergyPlus ships
an x86-64 Linux executable and the committed Modelica FMU contains x86-64
Linux code, so a native-arm64 wrapper image would be a broken mixed-architecture
container.

This follows the proven Validibot path for native-payload backends: its GCP and
self-hosted recipes explicitly build `linux/amd64`. Validibot also has a newer
host-native developer default intended to speed up portable backends such as
SHACL; that optimization is not safe for an image containing an x86-only
EnergyPlus executable or FMU. Slow local emulation is acceptable here because
it executes the same native bytes that Cloud Run executes. `just smoke-all`
proves that contract across every backend.

## Publish and deploy

The repository version is the release-image version for all backends. The
normal production path is a signed release: `just release X.Y.Z` pushes tag
`vX.Y.Z`, and GitHub Actions builds linux/amd64 images, publishes them to GHCR,
and mirrors them to PureLMS's Artifact Registry when the documented repository
variables are configured.

Cloud Run deployment is separate from publishing:

```bash
# Run from purelms/ after sourcing its GCP operator configuration.
just backends stage-bundles vX.Y.Z
git add purelms/static/backends
git commit -m "build(simulations): stage vX.Y.Z frontend bundles"
just gcp deploy-all prod
just backends deploy energyplus_single_zone prod
just backends deploy-all prod
```

The staging step builds frontend bundles from the exact signed tag and copies
them into PureLMS's tracked static tree. The LMS must be committed and deployed
before the matching backend release can be registered; backend deploys verify
this ordering against the live Django image.

`deploy` resolves the default `v<pyproject version>` tag to its Artifact
Registry digest and deploys `IMAGE@sha256:...`; the Cloud Run Job never points
at `latest`. Prod uses `purelms-itask-<slug>`, while dev and staging append the
stage name. Each stage gets a dedicated `purelms-sim-<stage>` runtime service
account with simulation-bucket and worker-callback access only.

After deployment, one `purelms-register-backends[-<stage>]` Job reconciles the
tagged inventory into the LMS. `deploy-all` submits every released entry in one
transactional catalog; `deploy <slug>` submits one selected entry through the
same Job. Missing catalog entries are never deactivated implicitly.

On the first deployment to a stage, Google Cloud can take a minute or more to
make that new service account visible to Cloud Storage and Cloud Run. The
recipe retries those cross-service operations with capped exponential backoff.

Built images carry OCI labels for the repository release version, source
revision, source repository, backend slug, and InteractiveTask manifest
version. These labels support operator inventory; the immutable image digest
remains the deployment and provenance trust root.

The PureLMS worker deploy stamps the deterministic
`SIMULATION_CALLBACK_SERVICE_ACCOUNT` for its stage. Backend deployment verifies
both the worker's `roles/run.invoker` IAM binding and that Django-facing value;
it fails with an instruction to deploy the current worker if the two sides do
not agree. No callback allowlist edit in the secret file is required. The
runtime mints a fresh OIDC token for every delivery attempt and refuses to send
an anonymous callback if token minting fails.

For a deliberate local publish (development or recovery), `just publish
<slug>` builds linux/amd64 and pushes only the immutable `vX.Y.Z` tag. It does
not replace the signed release workflow for normal production releases.

## Security

Report vulnerabilities privately and never put secrets or learner data in a
public issue. See [SECURITY.md](SECURITY.md) for the disclosure and release
hygiene policy.

## License

MIT — see [LICENSE](LICENSE).
