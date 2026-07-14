"""
Per-backend runtime self-description.

This file is informational. ``interactive_task.yaml`` is the LMS source of
truth; keep this self-description synchronized for operator inspection and
future automated drift detection.
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

# The domain implementation is not a long-lived streaming service. This does
# not control local Docker vs asynchronous Cloud Run Jobs transport.
SUPPORTS_STREAMING = False
