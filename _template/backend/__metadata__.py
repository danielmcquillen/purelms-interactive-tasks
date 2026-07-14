"""
Per-backend runtime self-description.

This file is informational. ``interactive_task.yaml`` is the LMS source of
truth; keep this self-description synchronized for operator inspection and
future automated drift detection.
"""

# TODO: fill in for your backend.
BACKEND_TYPE = "TODO_YOUR_TYPE"  # SCREAMING_SNAKE_CASE
BACKEND_NAME = "TODO Your Backend Name"
BACKEND_DESCRIPTION = (
    "TODO: a sentence or two describing what this backend computes "
    "and what its outputs mean."
)
BACKEND_VERSION = "0.1.0"

# Documented parameters surfaced to the LMS authoring UI. v1 is
# advisory — the source of truth is interactive_task.yaml's
# ``parameters:`` block.
EXPOSED_PARAMETERS = [
    # {"name": "glazing_u_value", "type": "number", "unit": "W/m²K"},
]

# Documented outputs surfaced to the LMS authoring UI. v1 is
# advisory — the source of truth is interactive_task.yaml's
# ``outputs:`` block.
OUTPUT_METRICS = [
    # {"name": "annual_heating_kWh", "type": "number", "unit": "kWh"},
]

# Whether the domain implementation is a long-lived streaming service. This
# does not control local Docker vs asynchronous Cloud Run Jobs transport.
SUPPORTS_STREAMING = False
