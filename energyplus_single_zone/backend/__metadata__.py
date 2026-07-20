"""
Per-backend runtime self-description for the EnergyPlus single-zone
InteractiveTask.

Informational only—the LMS reads ``interactive_task.yaml`` as the source of
truth. Keep this file synchronized for release/operator inventory.
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
BACKEND_VERSION = "0.3.4"

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

# The domain implementation is not a long-lived streaming service. It does
# report phase progress through the shared runtime on managed deployments.
SUPPORTS_STREAMING = False
