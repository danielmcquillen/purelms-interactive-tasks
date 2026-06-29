# Scenario: `hydronic_loop`

A simple hydronic heating loop modeled on LBNL Modelica Buildings Library
(MBL) `Buildings.Examples.Tutorial.Boiler`: a **boiler** heats water that a
**pump** circulates through a **radiator** into a **room**, and the room
temperature feeds back to the boiler's setpoint.

## Files

- `scenario.json` — the single canonical source: the component **palette**
  (typed ports), the **expected graph** the topology checker grades against,
  the **parameter map** (manifest parameter → FMU start value), and the
  **outputs** map (FMU variable → summary). The frontend embeds this same file
  at build time, so palette/ports/expected-graph can't drift from what the
  backend grades.
- `model.fmu` — **(not yet committed)** FMI 2.0 co-simulation FMU,
  `linux64`/amd64, exported from MBL. See the recompile recipe below.

## Recompiling the FMU (provenance)

> The FMU is a compiled binary; reproducible from source but not rebuilt per
> CI run. Compile on a machine with OpenModelica + the pinned MBL.

- **MBL version:** `<pin, e.g. v11.0.0>`
- **Tool:** OpenModelica `omc` `<version>`
- **Target:** FMI 2.0 co-simulation, platform `linux64` (x86_64).
- **Recipe (sketch):**

  ```bash
  # In an OpenModelica environment with the Buildings library loaded:
  omc <<'OMC'
  loadModel(Buildings, {"<MBL_VERSION>"});
  buildModelFMU(
    Buildings.Examples.Tutorial.Boiler.<VariantForThisLoop>,
    version = "2.0",
    fmuType = "cs",
    platforms = {"x86_64-linux-gnu"}
  );
  OMC
  # → produces <Model>.fmu; rename to model.fmu and place here.
  ```

After committing `model.fmu`, add it to the InteractiveTask manifest's
`assets:` block with its SHA-256 (and run the scenario↔FMU usability
cross-check + golden-vector tests — see ADR-0019).
