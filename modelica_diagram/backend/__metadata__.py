"""
Per-backend runtime self-description (see ``echo`` for the convention).

This file is informational. ``interactive_task.yaml`` is the LMS source of
truth; keep this self-description synchronized for release/operator inventory.
"""

BACKEND_TYPE = "MODELICA_DIAGRAM"
BACKEND_NAME = "Modelica FMU Diagram"
BACKEND_DESCRIPTION = (
    "Topology-checks a learner's component diagram against a scenario's "
    "expected graph, then runs a pre-compiled Modelica Buildings Library FMU "
    "to report how the system responds."
)
BACKEND_VERSION = "0.2.5"

# Parameter knobs the authoring UI can expose (mirrors interactive_task.yaml).
EXPOSED_PARAMETERS = [
    "scenario",
    "diagram_json",
    "layout_json",
    "boiler_nominal_power_kw",
    "room_setpoint_c",
    "heat_loss_w_per_k",
    "outdoor_temp_c",
]

# Metrics the run produces.
OUTPUT_METRICS = [
    "topology_correct",
    "room_temp_final_c",
    "energy_used_kwh",
    "time_to_setpoint_min",
    "series_json",
]

# The domain implementation is not a long-lived streaming service. This does
# not control local Docker vs asynchronous Cloud Run Jobs transport.
SUPPORTS_STREAMING = False
