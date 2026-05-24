"""
Per-backend runtime self-description.

PureLMS reads this at backend-registration time (future work, per
ADR-0002 Item 1's "metadata drift" open question) to verify the
declared ``SimulationBackendRegistration`` matches the container's
self-report. Until that registration-time check ships, this file is
informational only — but the convention is established so new
backends ship with it from day one (per ADR-0014).
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

# Whether this backend can run as an async streaming service
# (Tier-4 per ADR-0002). v1 InteractiveTasks are sync-only — leave
# this False unless you're prototyping async support.
SUPPORTS_STREAMING = False
