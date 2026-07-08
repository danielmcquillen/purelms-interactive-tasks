# Scenario: `hydronic_loop`

A single-room hydronic heating loop, modeled on LBNL Modelica Buildings Library
(MBL) `Buildings.Examples.Tutorial.Boiler`:

- a **boiler** heats water that a **pump** circulates through a **radiator** in
  a **closed water loop** (`boiler → pump → radiator → boiler`);
- the **radiator** warms the **room** through a **heat** connection — the room
  is *not* in the water loop;
- the room loses heat to the outdoors, and an on/off thermostat on the room
  temperature cycles the **boiler** (room temperature → boiler). The pump runs
  at constant flow.

The student reconstructs exactly this topology on the canvas.

## Files

- `scenario.json` — the single canonical source: the component **palette**
  (typed `fluid` / `heat` / `signal` ports), the **expected graph** the topology
  checker grades against, the **parameter map** (UI parameter → FMU variable),
  and the **outputs** map (FMU variable → summary). The frontend embeds this
  same file, so palette/ports/expected-graph can't drift from what the backend
  grades.
- `model.fmu` — the compiled FMI 2.0 co-simulation FMU the runner executes.
  **Built for `aarch64-linux`** (see the platform note below).
- `model/HydronicLoop.mo` — the Modelica source. `model/{setup,build,diag}.mos`
  + `model/validate.py` — the OpenModelica build + validation scripts.

## The variable contract (model ↔ scenario)

`scenario.json` binds UI parameters + result cards to **top-level FMU variables**.
`HydronicLoop.mo` exposes exactly these names (clean units; the model converts
to SI internally):

| scenario | FMU variable | unit | meaning |
|---|---|---|---|
| param `boiler_nominal_power_kw` | `QBoi_kW` | kW | boiler nominal power |
| param `room_setpoint_c` | `TRooSet_degC` | °C | target room temperature |
| output `room_temp_final_c` | `TRoo_degC` | °C | final room air temperature |
| output `energy_used_kwh` | `EHea_kWh` | kWh | cumulative heating energy |

Keep these names in sync if you edit either side.

## How `model.fmu` was built

Compiled with **OpenModelica 1.26.9** + **Modelica Buildings Library 13.0.0**,
inside the official OpenModelica Docker image — no local OM install needed.
From this directory:

```bash
# 1. Pull OM (native arch: arm64 on Apple Silicon, amd64 elsewhere).
docker pull openmodelica/openmodelica:v1.26.9-ompython
docker run -d --name om-build -v "$PWD/model:/work" -w /work \
  openmodelica/openmodelica:v1.26.9-ompython sleep infinity

# 2. Install Buildings (downloads MBL 13.0.0) + build the FMU.
docker exec om-build omc /work/setup.mos   # installPackage(Buildings)
docker exec om-build omc /work/build.mos   # -> /work/HydronicLoop.fmu

# 3. Validate (params settable + sane numbers), then place + hash.
docker exec om-build bash -lc "pip install -q fmpy && python3 /work/validate.py"
cp model/HydronicLoop.fmu model.fmu
shasum -a 256 model.fmu     # -> manifest assets: block
```

Three non-obvious fixes are baked into `HydronicLoop.mo` / `build.mos`:

1. **`--fmiFlags=s:cvode`** (in `build.mos`) — the *critical* one. The CS FMU's
   default `euler` solver blows up on this stiff fluid loop; CVODE matches the
   native DASSL run. (Confirm model soundness independently with `diag.mos`,
   which runs OM's native solver.)
2. **Constant-flow pump, on/off boiler** — switching the *flow* on/off makes a
   fluid volume ill-posed at zero flow. The loop always circulates; only the
   boiler firing cycles. Also physically common.
3. **No `Evaluate=false`; radiator `TAir_nominal` decoupled from the setpoint** —
   keeps `QBoi_kW`/`TRooSet_degC` settable without breaking OM's FMU init
   Jacobian. `TRoo_degC`/`EHea_kWh` are declared `output`.

**Validated** (15 °C start, 0 °C outside, 3 h): `QBoi_kW=10, TRooSet=21` →
room **22.1 °C**, **13.3 kWh**; the parameters clearly move the result.

## Platform note (important)

`model.fmu` is committed as **`aarch64-linux`** (built on Apple Silicon), which
matches a locally-built arm64 backend image. **Cloud Run is `amd64`** — before
deploying there, rebuild the FMU on the amd64 image and re-hash:

```bash
docker run -d --name om-amd64 --platform linux/amd64 -v "$PWD/model:/work" \
  -w /work openmodelica/openmodelica:v1.26.9-ompython sleep infinity
# (same setup.mos + build.mos; produces an amd64 binary)
```

(A multi-platform FMU bundling both `binaries/aarch64-linux/` and
`binaries/x86_64-linux/` would serve both; that's a follow-up.)

## Deploying a change

`model.fmu` + `scenario.json` are pinned by SHA in the manifest `assets:` block.
After changing either: re-hash, update the manifest, **bump the manifest
version**, `just build modelica_diagram`, and reinstall with `--replace-active`
(the installer won't mutate a registration that has historical runs).
