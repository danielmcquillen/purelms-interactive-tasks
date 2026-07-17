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
  **Built for `linux/amd64`**, matching Cloud Run and local Docker smoke tests.
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
# Always compile for Cloud Run's architecture. Docker Desktop emulates this on
# an Apple-Silicon Mac.
docker pull openmodelica/openmodelica:v1.26.9-ompython
docker run -d --name om-build --platform linux/amd64 \
  -v "$PWD/model:/work" -w /work \
  openmodelica/openmodelica:v1.26.9-ompython sleep infinity

# 2. Install Buildings (downloads MBL 13.0.0) + build the FMU.
docker exec om-build omc /work/setup.mos   # installPackage(Buildings)
docker exec om-build omc /work/build.mos   # -> /work/HydronicLoop.fmu

# 3. Validate (params settable + sane numbers), then place + hash.
docker exec om-build bash -lc "pip install -q fmpy && python3 /work/validate.py"
cp model/HydronicLoop.fmu model.fmu
shasum -a 256 model.fmu
# Expected for the committed FMU:
# fd358022989b9420441637c986888026acfebf15d204790db337a0db5f4d5e79
docker rm -f om-build

# From the repository root: verify the manifest hash, embedded ELF
# architecture, and the real container execution.
python3 scripts/validate_release_assets.py
just smoke modelica_diagram
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

**Validated 2026-07-14 under linux/amd64 emulation** (15 °C start, 0 °C
outside, 3 h): `QBoi_kW=10, TRooSet=21` → room **22.17 °C**, **13.92 kWh**;
the parameters clearly move the result.

## Platform note (important)

`model.fmu` contains x86-64 Linux shared libraries. Cloud Run executes it
natively; Docker Desktop executes the same backend image under AMD64 emulation
on Apple Silicon. Do not rebuild it in a native arm64 OpenModelica container:
an FMU can still use the generic `binaries/linux64/` path while embedding an
incompatible AArch64 ELF. `scripts/validate_release_assets.py` inspects the ELF
headers, and both `just release` and release CI run that check before a tag can
produce artifacts.

## Deploying a change

`model.fmu` + `scenario.json` are pinned by SHA in the manifest `assets:` block.
After changing either: re-hash, update the manifest, **bump the manifest
version**, run `just smoke modelica_diagram`, install the new candidate, then
activate it explicitly after verification (the installer won't mutate a
registration that has historical runs).
