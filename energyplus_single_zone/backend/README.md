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

**✅ Build-validated end-to-end (2026-05-29, EnergyPlus 25.2):** built the
image and ran the container against a real envelope (`just build` +
`docker run` with input/output mounts). The IDF runs clean (no
Severe/Fatal), the EPW URLs resolve, the image assembles, and the End
Uses + peak-rate extraction returns sane, monotonic numbers — e.g. for a
5 m² U=2.5 window in 5A: ~3,000 kWh/yr heating; lowering U to 1.0 drops
heating ~15%, growing the window to 20 m² raises heating ~55% and cooling
~240% (solar gain), and 6A is colder than 5A. Two issues the validation
surfaced and fixed:

1. **Image must be `linux/amd64`** — the NREL EnergyPlus binary is x86_64
   only, so the Dockerfile pins both stages to `--platform=linux/amd64`
   (also matches Cloud Run). On Apple Silicon it builds + runs under
   emulation (~4 s/run).
2. **`OutputControl:Table:Style` unit conversion** — the report stores
   energy in the unit named by that field; it's set to `None`
   (report-native GJ) so `_sum_end_use_row`'s `Units = 'GJ'` filter +
   GJ→kWh conversion match. (`JtoKWH` would store kWh and silently
   return 0.)

The fast unit suite (above) still covers everything binary-free, so the
analytical fallback keeps local dev / CI working without the ~500 MB
dependency. To re-validate after an IDF or version change: `just build
energyplus_single_zone`, run the container against a hand-written
`input.json`, and inspect `output.json` + `eplusout.err`.

---

EnergyPlus™ is a trademark of the U.S. Department of Energy, distributed
under a BSD-3-Clause license by NREL. PureLMS is not affiliated with,
endorsed by, or sponsored by DOE or NREL.
