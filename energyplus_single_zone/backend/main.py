"""
EnergyPlus single-zone backend — container entrypoint.

Wires the envelope contract (read input → call runner → write output)
to the pure-function domain code in ``runner.simulate``. Pattern
matches the ``_template/backend/main.py`` skeleton + ``echo``
reference; the only real difference is the dispatch to ``runner``
instead of inline echo logic.

When real EnergyPlus replaces the stubbed runner (Slice 4+), this
file shouldn't need to change — ``runner.simulate`` is the swap-in
seam.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import Message
from purelms_shared.envelopes import SimulationInputEnvelope
from purelms_shared.envelopes import SimulationOutputEnvelope
from runner import simulate


def main() -> int:
    """Container entrypoint. Returns the process exit code.

    Three contractual touchpoints (per ADR-0014):

    1. **Input read.** ``$PURELMS_INPUT_DIR/input.json`` MUST exist
       and parse as ``SimulationInputEnvelope``. If not → exit 1.
    2. **Domain work.** Delegated to ``runner.simulate(parameters)``.
       Validation errors (missing / out-of-range parameter) are
       caught + reported via a FAILED_RUNTIME output envelope
       rather than a non-zero exit — the envelope path gives the
       learner a clean error UI; non-zero exit falls back to a
       log-tail error.
    3. **Output write.** ``$PURELMS_OUTPUT_DIR/output.json`` MUST be
       written before exit 0. Missing file → contract violation.
    """
    input_dir = Path(os.environ.get("PURELMS_INPUT_DIR", "/purelms/input"))
    output_dir = Path(os.environ.get("PURELMS_OUTPUT_DIR", "/purelms/output"))
    run_id_env = os.environ.get("PURELMS_RUN_ID", "unknown")

    started_at = time.monotonic()

    # 1. Read + parse the input envelope.
    input_path = input_dir / "input.json"
    if not input_path.exists():
        print(
            f"energyplus_single_zone: missing input envelope at {input_path}",
            file=sys.stderr,
        )
        return 1

    try:
        envelope = SimulationInputEnvelope.model_validate_json(input_path.read_text())
    except Exception as exc:
        print(
            f"energyplus_single_zone: input envelope invalid: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"energyplus_single_zone: run_id={run_id_env} "
        f"backend={envelope.backend_slug}@{envelope.backend_version} "
        f"parameters={envelope.parameters!r}",
    )

    # 2. Run the domain code. Catch validation errors + surface
    # them via the envelope (FAILED_RUNTIME path) so the learner
    # sees a clean error, not a log tail.
    try:
        outputs = simulate(envelope.parameters)
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
        # The LMS refunds credits on FAILED_RUNTIME per ADR-0011.
        # We catch all three of:
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

    # 3. Write the output envelope. Always — even on
    # FAILED_RUNTIME — so the LMS reads the clean error path.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "output.json").write_text(output.model_dump_json(indent=2))

    print(
        f"energyplus_single_zone: wrote output envelope "
        f"({output_dir / 'output.json'}) status={status}",
    )
    # Exit 0 even on FAILED_RUNTIME — the envelope status carries
    # the failure information. Non-zero exit is reserved for
    # contract violations (couldn't read input / couldn't write
    # output / unexpected exception escaping this function).
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Last-resort net for unexpected exceptions — log the trace,
        # exit 1 so the LMS marks the run FAILED_RUNTIME via the
        # exit-code path (since we don't have a safe output envelope
        # to write in this catastrophic state).
        print(
            f"energyplus_single_zone: unexpected fatal error: {exc!r}",
            file=sys.stderr,
        )
        import traceback

        traceback.print_exc()
        sys.exit(1)
