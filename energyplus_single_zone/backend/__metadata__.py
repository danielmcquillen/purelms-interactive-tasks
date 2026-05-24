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
    "Single-zone building energy through one window. v1 ships an "
    "analytical steady-state model (U × A × HDD × hours) in lieu of "
    "a real EnergyPlus binary; real EnergyPlus is Slice 4+ work."
)
BACKEND_VERSION = "0.1.0"

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

# v1 manifests are sync-only (per ADR-0014). Async streaming is
# Slice 4+ work and will require real EnergyPlus with progress
# callbacks during the multi-month annual run.
SUPPORTS_STREAMING = False
