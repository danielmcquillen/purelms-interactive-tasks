# `_shared_backends/` — shared backend libraries

This directory is reserved for **libraries shared across multiple
InteractiveTask backends** (e.g. a common EnergyPlus I/O helper, a
Modelica parameter-marshalling utility).

It is currently a placeholder. Nothing lives here yet — the echo
fixture is self-contained, and the first real backend
(`energyplus_single_zone/`, Slice 3d) embeds its EnergyPlus helpers
directly until at least one other backend wants the same code.

## When to extract

Per ADR-0014, the rule is **wait for the second user**:

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

See [ADR-0014](https://github.com/danielmcquillen/purelms-project/blob/main/docs/adr/0014-interactive-task-framework.md)
for the full framework specification.
