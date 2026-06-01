"""
Echo backend — container entrypoint.

Reads the input envelope, echoes its parameters back as outputs, writes a
``SUCCESS`` output envelope, exits 0. The actual I/O (local-dir vs GCS
URI) and the completion signalling (worker ``/complete`` callback on the
async path) are handled by the shared :mod:`purelms_itask_runtime` so
this backend meets the runtime contract on BOTH the local DockerCompose
path and the Cloud Run Jobs path without any mode branching here.

This is the permanent LMS-side integration-test fixture. Real backends
(EnergyPlus, FMU, etc.) follow the same pattern but actually do domain
work between the read and the write.
"""

from __future__ import annotations

import sys
import time

from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import Message
from purelms_shared.envelopes import SimulationOutputEnvelope


def main() -> int:
    """Container entrypoint. Returns the exit code.

    Three contractual touchpoints:

    1. **Input read.** The input envelope MUST exist + parse as
       ``SimulationInputEnvelope`` (read from ``PURELMS_INPUT_URI`` on the
       async path, else ``PURELMS_INPUT_DIR/input.json``). If not → exit 1.
    2. **Domain work.** For echo: nothing. Real backends run their
       solver / pipeline / etc. here.
    3. **Output write.** The output envelope MUST be written before
       exit 0 (to ``PURELMS_OUTPUT_URI`` on the async path, else
       ``PURELMS_OUTPUT_DIR/output.json``). On the async path the helper
       also POSTs the authoritative ``/complete`` callback.
    """
    location = RuntimeLocation.from_env()
    started_at = time.monotonic()

    # 1. Read + parse the input envelope (GCS URI or local dir). A
    # missing / invalid envelope is a contract violation → exit 1 (the
    # LMS surfaces it via the log tail; no output envelope is written).
    try:
        envelope = read_input_envelope(location)
    except Exception as exc:
        print(f"echo: could not read input envelope: {exc}", file=sys.stderr)
        return 1

    print(
        f"echo: run_id={location.run_id} backend_slug={envelope.backend_slug} "
        f"parameters={envelope.parameters!r}",
    )

    # 2. Do the "work" — just echo. Real backends would run their
    # domain code here.
    runtime_seconds = time.monotonic() - started_at
    output = SimulationOutputEnvelope(
        run_id=envelope.run_id,
        status=OutputStatus.SUCCESS,
        outputs={
            "echoed_parameters": envelope.parameters,
            "echoed_backend_slug": envelope.backend_slug,
        },
        artifacts=[],
        messages=[
            Message.model_validate(
                {
                    "level": "info",
                    "code": "ECHO.OK",
                    "text": (
                        f"Echoed {len(envelope.parameters)} parameters in "
                        f"{runtime_seconds:.3f}s."
                    ),
                },
            ),
        ],
        metrics={},
        runtime_seconds=runtime_seconds,
    )

    # 3. Write the output envelope (GCS URI or local dir) + signal
    # completion to the worker on the async path.
    write_output_envelope(location, output, envelope.context)

    print("echo: wrote output envelope")
    return 0


if __name__ == "__main__":
    sys.exit(main())
