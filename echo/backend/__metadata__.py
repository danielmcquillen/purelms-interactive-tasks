"""
Per-backend runtime self-description.

PureLMS reads this at backend-registration time (future work) to
verify the
declared `SimulationBackendRegistration` matches the container's
self-report. Until that registration-time check ships, this file is
informational only — but the convention is established so future
backends ship with it from day one.
"""

BACKEND_TYPE = "ECHO"
BACKEND_NAME = "Echo Test Backend"
BACKEND_DESCRIPTION = (
    "Reads the input envelope, echoes its parameters as outputs, "
    "writes a SUCCESS output envelope. Used as the LMS-side "
    "integration-test fixture — never delete."
)
BACKEND_VERSION = "0.1.0"

# What the backend can do. Used by the LMS's authoring UI to show
# course authors what parameter knobs exist + what metrics will be
# produced.
EXPOSED_PARAMETERS = [
    # Echo accepts arbitrary parameters and echoes them back; no
    # fixed schema. Real backends declare their parameter shape here.
]

OUTPUT_METRICS = [
    # Echo doesn't produce real metrics; just echoes the input.
]

# Whether this backend can run as an async streaming service
# . Echo is sync-only.
SUPPORTS_STREAMING = False
