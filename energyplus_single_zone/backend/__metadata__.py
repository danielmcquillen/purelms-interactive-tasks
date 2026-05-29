"""
Per-backend runtime self-description for the EnergyPlus single-zone
InteractiveTask.

Informational only in v1 — the LMS reads ``interactive_task.yaml``
as the source of truth (per ADR-0014's manifest-driven model).
When registration-time container introspection ships (Slice 4+,
per ADR-0002 open question 1), the LMS will read this file from
the running container and verify it matches the persisted
``SimulationBackendRegistration``.

Keep this file in sync with ``interactive_task.yaml`` so the
future drift-detection check passes.
"""

BACKEND_TYPE = "ENERGYPLUS_SINGLE_ZONE"
BACKEND_NAME = "EnergyPlus Single-Zone"
BACKEND_DESCRIPTION = (
    "Single-zone building energy through one window. v0.2 runs a REAL "
    "EnergyPlus simulation: it builds a single-zone IDF from the learner's "
    "parameters, runs the bundled EnergyPlus binary against a per-zone EPW "
    "weather file, and mines eplusout.sql for annual heating/cooling + peak "
    "load. A pure-Python analytical model is the binary-free dev/CI fallback "
    "(PURELMS_EPLUS_MODE=analytical), so contributors can iterate without the "
    "~500 MB EnergyPlus dependency."
)
BACKEND_VERSION = "0.2.1"

# Mirror the manifest's ``parameters:`` block (informational).
EXPOSED_PARAMETERS = [
    {
        "name": "glazing_u_value",
        "type": "number",
        "unit": "W/m²K",
        "min": 0.5,
        "max": 6.0,
        "default": 2.5,
    },
    {
        "name": "window_area",
        "type": "number",
        "unit": "m²",
        "min": 1.0,
        "max": 20.0,
        "default": 5.0,
    },
    {
        "name": "climate_zone",
        "type": "enum",
        "choices": ["4A", "5A", "6A"],
        "default": "5A",
    },
]

# Mirror the manifest's ``outputs:`` block (informational).
OUTPUT_METRICS = [
    {"name": "annual_heating_kWh", "type": "number", "unit": "kWh"},
    {"name": "annual_cooling_kWh", "type": "number", "unit": "kWh"},
    {"name": "peak_heating_kW", "type": "number", "unit": "kW"},
    {"name": "notes", "type": "string"},
]

# Still sync-only (per ADR-0014). The single-zone annual run is fast
# (seconds), so there's no need for progress callbacks yet; async
# streaming stays a future capability for heavier multi-zone models.
SUPPORTS_STREAMING = False
