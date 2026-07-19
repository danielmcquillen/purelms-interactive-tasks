"""
EnergyPlus single-zone backend — container entrypoint.

Wires the envelope contract (read input -> call runner -> write output)
to the pure-function domain code in ``runner.simulate``. The I/O itself
(local-dir vs GCS URI) and the worker callbacks (progress mid-run +
the authoritative ``/complete`` at the end) are delegated to the shared
:mod:`purelms_itask_runtime`, so this file meets the runtime contract on
BOTH the local DockerCompose path and the async Cloud Run Jobs path
without any mode branching here.

``runner.simulate`` is the swap-in seam: real EnergyPlus vs the
analytical fallback is decided inside it, not here.
"""

from __future__ import annotations

import sys
import time
import traceback

from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import make_progress_reporter
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import Message
from purelms_shared.envelopes import SimulationOutputEnvelope
from runner import simulate


def main() -> int:
    """Container entrypoint. Returns the process exit code.

    Three contractual touchpoints:

    1. **Input read.** The input envelope MUST exist + parse as
       ``SimulationInputEnvelope`` (from ``PURELMS_INPUT_URI`` on the
       async path, else ``PURELMS_INPUT_DIR/input.json``). If not → exit 1.
    2. **Domain work.** Delegated to ``runner.simulate(parameters)``.
       Validation errors (missing / out-of-range parameter) are caught +
       reported via a FAILED_RUNTIME output envelope rather than a
       non-zero exit — the envelope path gives the learner a clean error
       UI; non-zero exit falls back to a log-tail error.
    3. **Output write.** The output envelope MUST be written before
       exit 0 (to ``PURELMS_OUTPUT_URI`` on the async path, else
       ``PURELMS_OUTPUT_DIR/output.json``). On the async path the helper
       also POSTs the required ``/complete`` notification.
    """
    location = RuntimeLocation.from_env()
    started_at = time.monotonic()

    # 1. Read + parse the input envelope (GCS URI or local dir).
    try:
        envelope = read_input_envelope(location)
    except Exception as exc:
        print(
            f"energyplus_single_zone: could not read input envelope: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"energyplus_single_zone: run_id={location.run_id} "
        f"backend={envelope.backend_slug}@{envelope.backend_version} "
        f"parameter_keys={sorted(envelope.parameters)}",
    )

    # 2. Run the domain code. Catch validation errors + surface them via
    # the envelope (FAILED_RUNTIME path) so the learner sees a clean
    # error, not a log tail.
    #
    # Wire phase progress to the worker's progress endpoint when there's
    # a real one to POST to (async/Cloud Run). On the local sync path the
    # reporter is ``None`` and ``simulate`` runs silently — a blocking
    # sync run is observed via its output envelope, not callbacks.
    on_progress = make_progress_reporter(envelope.context, started_at)
    try:
        outputs = simulate(
            envelope.parameters,
            on_progress=on_progress,
            timeout_seconds=envelope.context.timeout_seconds,
        )
        status = OutputStatus.SUCCESS
        messages = [
            Message.model_validate(
                {
                    "level": "info",
                    "code": "EPLUS_SZ.OK",
                    "text": f"Completed in {time.monotonic() - started_at:.3f}s.",
                },
            ),
        ]
    except (KeyError, ValueError, TypeError) as exc:
        # Bad parameters → FAILED_RUNTIME with a useful message.
        # The LMS refunds credits on FAILED_RUNTIME.
        #   - KeyError: missing required parameter
        #   - TypeError: wrong type (e.g. int where str expected)
        #   - ValueError: out of range / unparseable
        outputs = {}
        status = OutputStatus.FAILED_RUNTIME
        messages = [
            Message.model_validate(
                {
                    "level": "error",
                    "code": "EPLUS_SZ.BAD_PARAMETERS",
                    "text": f"Parameter validation failed: {exc}",
                },
            ),
        ]
        print(
            f"energyplus_single_zone: parameter validation failed: {exc}",
            file=sys.stderr,
        )

    runtime_seconds = time.monotonic() - started_at

    output = SimulationOutputEnvelope(
        run_id=envelope.run_id,
        status=status,
        outputs=outputs,
        artifacts=[],
        messages=messages,
        metrics={},
        runtime_seconds=runtime_seconds,
    )

    # 3. Write the output envelope (GCS URI or local dir) + signal
    # completion to the worker on the async path. Always written — even
    # on FAILED_RUNTIME — so the LMS reads the clean error path. Exit 0
    # even on FAILED_RUNTIME (the envelope status carries the failure);
    # non-zero exit is reserved for contract violations.
    write_output_envelope(location, output, envelope.context)

    print(f"energyplus_single_zone: wrote output envelope status={status}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Last-resort net for unexpected exceptions — log the trace,
        # exit 1 so the LMS marks the run FAILED_RUNTIME via the
        # exit-code path (we don't have a safe output envelope to write
        # in this catastrophic state; the worker's lost-callback sweeper
        # reconciles a GCS run left RUNNING).
        print(
            f"energyplus_single_zone: unexpected fatal error: {exc!r}",
            file=sys.stderr,
        )
        traceback.print_exc()
        sys.exit(1)
