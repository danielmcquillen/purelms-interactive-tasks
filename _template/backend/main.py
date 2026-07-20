"""
TODO: backend container entrypoint for the <your-slug> InteractiveTask.

Every InteractiveTask backend reads a SimulationInputEnvelope, does its
domain work, and writes a SimulationOutputEnvelope. The shared
``purelms_itask_runtime`` handles the I/O (local dir vs GCS URI) and the
worker callbacks (progress mid-run + the required ``/complete`` notification at
the end), so the SAME container satisfies the contract on BOTH the local
DockerCompose path and managed provider paths — with no
"am I local or cloud?" branching here.

This file is the skeleton. Replace the "TODO: domain work" section with
your simulation / pipeline / analysis. Everything else (envelope I/O,
progress + completion callbacks, exit codes) is contract boilerplate the
runtime helper provides.
"""

from __future__ import annotations

import sys
import time

from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import make_progress_reporter
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import Message
from purelms_shared.envelopes import SimulationOutputEnvelope


def main() -> int:
    """Container entrypoint. Returns the process exit code.

    Three contractual touchpoints, all mode-agnostic via the runtime helper:

    1. **Input read.** ``read_input_envelope`` reads from
       ``PURELMS_INPUT_URI`` (async/GCS) or ``PURELMS_INPUT_DIR/input.json``
       (local). A missing / invalid envelope → exit 1.
    2. **Domain work.** Replace the TODO block. ``envelope.parameters`` is
       the learner-submitted parameter dict (already validated by the LMS
       against ``interactive_task.yaml``). Populate ``outputs`` with values
       matching your manifest's ``outputs:`` block.
    3. **Output write + completion.** ``write_output_envelope`` writes to
       ``PURELMS_OUTPUT_URI`` (async/GCS) or ``PURELMS_OUTPUT_DIR/output.json``
       (local) and, on the async path, POSTs the authoritative
       ``/complete`` callback (retried; raises if undeliverable so the run
       is salvaged rather than lost).
    """
    location = RuntimeLocation.from_env()
    started_at = time.monotonic()

    # 1. Read + parse the input envelope (GCS URI or local dir).
    try:
        envelope = read_input_envelope(location)
    except Exception as exc:
        print(
            f"TODO_your_slug_here: could not read input envelope: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"TODO_your_slug_here: run_id={location.run_id} "
        f"backend_slug={envelope.backend_slug} "
        f"parameters={envelope.parameters!r}",
    )

    # Optional progress reporting. ``on_progress`` is None on the local
    # sync path (no endpoint to POST to), so guard before calling. Declare
    # ``progress_reporting: percentage`` only when these values measure real
    # work. The shared reporter floors noisy tool updates to 0/25/50/75/100
    # and applies the LMS-provided minimum callback interval by default.
    on_progress = make_progress_reporter(envelope.context, started_at)
    if on_progress:
        on_progress(0, "starting")

    # 2. TODO: domain work goes here. Feed genuine raw tool percentages to
    #    ``on_progress(pct, step)``; do not manufacture time-based progress.
    #    Populate ``outputs`` with
    #    values matching your manifest's ``outputs:`` block.
    outputs: dict[str, object] = {
        # "annual_heating_kWh": 1234.5,
    }

    runtime_seconds = time.monotonic() - started_at
    output = SimulationOutputEnvelope(
        run_id=envelope.run_id,
        status=OutputStatus.SUCCESS,
        outputs=outputs,
        artifacts=[],
        messages=[
            Message.model_validate(
                {
                    "level": "info",
                    "code": "TODO_YOUR_SLUG.OK",
                    "text": f"Completed in {runtime_seconds:.3f}s.",
                },
            ),
        ],
        metrics={},
        runtime_seconds=runtime_seconds,
    )

    # 3. Write the output envelope (GCS URI or local dir) + signal
    #    completion to the worker on the async path.
    write_output_envelope(location, output, envelope.context)

    print("TODO_your_slug_here: wrote output envelope")
    return 0


if __name__ == "__main__":
    sys.exit(main())
