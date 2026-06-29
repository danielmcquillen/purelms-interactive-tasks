"""
Per-backend runtime self-description (see ``echo`` for the convention).

PureLMS reads this at backend-registration time (future work) to verify the
declared ``SimulationBackendRegistration`` matches the container's self-report.
Until that check ships, this file is informational — but the convention is
established so backends ship with it from day one.
"""

BACKEND_TYPE = "MODELICA_DIAGRAM"
BACKEND_NAME = "Modelica FMU Diagram"
BACKEND_DESCRIPTION = (
    "Topology-checks a learner's component diagram against a scenario's "
    "expected graph, then runs a pre-compiled Modelica Buildings Library FMU "
    "to report how the system responds."
)
BACKEND_VERSION = "0.1.0"

# Parameter knobs the authoring UI can expose (mirrors interactive_task.yaml).
EXPOSED_PARAMETERS = [
    "scenario",
    "diagram_json",
    "boiler_nominal_power_kw",
    "temperature_setpoint_c",
]

# Metrics the run produces.
OUTPUT_METRICS = [
    "topology_correct",
    "room_temp_final_c",
    "energy_used_kwh",
    "series_json",
]

# Sync-only (one run -> one envelope); no async streaming.
SUPPORTS_STREAMING = False
