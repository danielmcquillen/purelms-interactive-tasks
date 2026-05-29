# EnergyPlus single-zone — backend

The backend half of the `energyplus_single_zone` InteractiveTask. It
takes a learner's three parameters (glazing U-value, window area,
climate zone) and returns annual heating + cooling energy and peak
heating load for a single conditioned zone.

## Two execution modes

| Mode | When | What runs |
|---|---|---|
| **`energyplus`** | The production container (binary baked in) | Builds a single-zone IDF from the parameters, runs the real EnergyPlus binary against the bundled per-zone EPW, mines `eplusout.sql`. |
| **`analytical`** | Dev / CI / no binary on PATH | Pure-Python steady-state heat balance (`U × A × degree-days × hours`). No 500 MB dependency. Qualitatively correct, **not** a design tool. |

Selection is via `PURELMS_EPLUS_MODE` (`auto` default → real if the
`energyplus` binary is on `PATH`, else analytical). Both modes return
identical output keys; the `notes` string says which one ran.

```
main.py            envelope I/O contract (read input.json → simulate → write output.json)
runner.py          simulate() + the real-EnergyPlus path + the analytical fallback
climate.py         per-zone config: EPW filename + degree-days + design ΔT
idf/single_zone.idf.template   the parametric IDF (string.Template placeholders)
```

`runner.build_idf` maps `glazing_u_value` straight onto
`WindowMaterial:SimpleGlazingSystem`'s U-Factor field and derives the
window length from the area, so adding a parameter or a climate zone is
a data change (template + `climate.py` + manifest enum), not a logic
change — the "configurable educational backend" property.

## Build + run

```bash
# From the purelms-interactive-tasks repo root:
just build energyplus_single_zone        # multi-stage: fetch EnergyPlus 25.2 + EPWs, assemble image
just test-backend energyplus_single_zone # the fast unit suite (no binary needed)

# Install into a PureLMS deployment (bumps the active registration to 0.2.0):
uv run python manage.py install_interactive_task \
    ../purelms-interactive-tasks/energyplus_single_zone --replace-active
```

The image sets `PURELMS_EPLUS_MODE=energyplus` and
`PURELMS_EPLUS_WEATHER_DIR=/opt/weather`, so the container always runs
the real simulation (and fails loud if the binary is somehow missing,
rather than silently degrading to the analytical model).

## Verification status (read before trusting outputs)

This backend was authored without an EnergyPlus binary or weather files
available in the authoring environment, so the pieces split into
**unit-verified** and **needs-a-real-build**:

**✅ Unit-tested (`tests/test_runner.py`, no binary required):**

- `build_idf` template substitution (values land, window length derived,
  no placeholder survives).
- `extract_metrics` SQL mining — run against a **synthetic SQLite DB**
  that mimics the EnergyPlus 25.x schema (`TabularDataWithStrings` End
  Uses + `ReportData`/`ReportDataDictionary`). The queries are translated
  from Validibot's production-proven EnergyPlus validator.
- `parse_err_file` severity tagging + multi-line continuation.
- `_select_mode` dispatch + the analytical model's physics.

**⚠️ NOT verified here — confirm with a real `just build` + container run:**

- That the **IDF template** is accepted by EnergyPlus 25.2 (object set,
  field order, surface vertex winding) and produces physically sane
  numbers. A vertex-winding or field-order error surfaces as a Severe/Fatal
  in `eplusout.err`.
- That the **EPW download URLs** in the `Dockerfile` resolve (the DOE
  weather set moves; a 404 means re-source the TMY3 file).
- That the **End Uses row/column names** match what an IdealLoads single
  zone actually writes (district heating/cooling labels vary by version).
- The end-to-end **image build** (binary fetch + assembly).

When validating: `just build energyplus_single_zone`, then run the
container against a hand-written `input.json` and inspect `output.json`
+ `eplusout.err`. The analytical fallback covers everything in the
meantime, so the task is never broken for local dev.

---

EnergyPlus™ is a trademark of the U.S. Department of Energy, distributed
under a BSD-3-Clause license by NREL. PureLMS is not affiliated with,
endorsed by, or sponsored by DOE or NREL.
