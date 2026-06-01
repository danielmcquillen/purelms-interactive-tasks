# `_shared_backends/` — shared backend libraries

This directory holds **libraries shared across multiple InteractiveTask
backends** (e.g. a common EnergyPlus I/O helper, a Modelica
parameter-marshalling utility).

## What lives here

- **`purelms_itask_runtime/`** — the shared backend runtime contract:
  local-dir vs GCS-URI envelope I/O + the progress/complete worker
  callbacks. Both `echo` and `energyplus_single_zone` depend on it (the
  "second user" trigger below), so each backend's `main.py` is identical
  regardless of whether the LMS launched it via the local DockerCompose
  path or the async Cloud Run Jobs path. See its README for the contract.

## When to extract

The rule is **wait for the second user**:

- A first InteractiveTask invents a helper. Helper lives inside that
  InteractiveTask's `backend/` directory.
- A second InteractiveTask wants the same helper. **Now** extract it
  into `_shared_backends/<helper-name>/`, give it its own
  `pyproject.toml`, and wire both backends to depend on it.
- A third InteractiveTask gets it for free.

This avoids the "premature shared library" failure mode where the
abstraction is wrong because it was designed before its second user
existed.

## Layout (once populated)

```
_shared_backends/
  energyplus_io/             # for example
    pyproject.toml           # name = "purelms-shared-energyplus-io"
    src/
      energyplus_io/
        __init__.py
        ...
    tests/
      ...
```

Shared backend libraries are added to the workspace root
`pyproject.toml`'s `[tool.uv.workspace.members]` list so `uv sync`
picks them up.

## What does **not** belong here

- **Per-backend code** — that lives inside `<slug>/backend/`.
- **LMS / frontend code** — wrong repo. LMS code lives in `purelms/`;
  the envelope schemas live in `purelms-shared/` (a separate repo).
- **Speculative abstractions** — if no second backend wants it yet,
  it doesn't belong here yet.
