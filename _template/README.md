# `_template/` — skeleton for a new InteractiveTask

Copy this directory to start a new InteractiveTask:

```bash
cp -r _template my_new_task
# Then edit my_new_task/interactive_task.yaml + fill in backend/main.py
# + fill in frontend/src/placeholder.ts (rename to match your slug).
```

Then:

1. Rename `my_new_task/frontend/src/placeholder.ts` to
   `my_new_task/frontend/src/<your_slug>.ts` (matching your slug).
2. Rename `my_new_task/frontend/tests/placeholder.test.ts` to
   `<your_slug>.test.ts`.
3. Update `my_new_task/interactive_task.yaml` (slug, name, version,
   parameters, outputs, lms_outcomes).
4. Update `my_new_task/backend/pyproject.toml` `name` to
   `purelms-itask-<your-slug-with-hyphens>` (s/_/-/g).
5. Update `my_new_task/backend/__metadata__.py` (BACKEND_TYPE,
   BACKEND_NAME, etc.).
6. Update `my_new_task/frontend/package.json`:
   - `name` to `@purelms-interactive-tasks/<your-slug-with-hyphens>-frontend`
   - `build`/`watch` scripts to point at `src/<your_slug>.ts`
   - `--outfile` to `dist/<your_slug>.js`
7. Add `"my_new_task/backend"` to the workspace root
   `pyproject.toml`'s `[tool.uv.workspace.members]`.
8. If this is an officially published backend, add it to `backends.toml`, add
   `my_new_task` to the root justfile's `slugs` value, and add it to the release
   workflow matrix. Update the Dockerfile's backend-slug and task-version label
   defaults too.
9. Build + test: `just test my_new_task && just smoke my_new_task`. The smoke
   recipe builds and runs `linux/amd64`, matching Cloud Run and running under
   Docker Desktop emulation on Apple Silicon.
10. Install into a PureLMS instance:
   `manage.py install_interactive_task ../purelms-interactive-tasks/my_new_task`.

See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) and
[`../BACKEND_AUTHORING_GUIDE.md`](../BACKEND_AUTHORING_GUIDE.md)
for the full framework reference.

The files in this template are deliberately minimal — just enough
scaffolding to get you to a working build. Real InteractiveTasks add
domain code, validation, richer UIs, tests, and per-task tooling.
