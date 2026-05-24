"""
TODO: backend container entrypoint for the <your-slug> InteractiveTask.

Per ADR-0014 §runtime contract, every InteractiveTask backend reads a
SimulationInputEnvelope from ``$PURELMS_INPUT_DIR/input.json``, does
its domain work, and writes a SimulationOutputEnvelope to
``$PURELMS_OUTPUT_DIR/output.json``.

This file is the skeleton. Replace the "TODO: domain work" section
with your simulation / pipeline / analysis. Everything else (I/O,
envelope parsing, exit codes) is contract boilerplate.
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


def main() -> int:
    """Container entrypoint. Returns the process exit code.

    Three contractual touchpoints (see ADR-0014):

    1. **Input read.** ``$PURELMS_INPUT_DIR/input.json`` MUST exist and
       parse as ``SimulationInputEnvelope``. If not → exit 1.
    2. **Domain work.** Replace the TODO block below with your
       simulation. Output values go into ``outputs={...}`` keyed by
       the output names declared in ``interactive_task.yaml``.
    3. **Output write.** ``$PURELMS_OUTPUT_DIR/output.json`` MUST be
       written before exit 0. The LMS treats a missing file as a
       contract violation regardless of the exit code.
    """
    input_dir = Path(os.environ.get("PURELMS_INPUT_DIR", "/purelms/input"))
    output_dir = Path(os.environ.get("PURELMS_OUTPUT_DIR", "/purelms/output"))
    run_id = os.environ.get("PURELMS_RUN_ID", "unknown")

    started_at = time.monotonic()

    # 1. Read + parse the input envelope.
    input_path = input_dir / "input.json"
    if not input_path.exists():
        print(
            f"TODO_your_slug_here: missing input envelope at {input_path}",
            file=sys.stderr,
        )
        return 1

    try:
        envelope = SimulationInputEnvelope.model_validate_json(input_path.read_text())
    except Exception as exc:
        print(
            f"TODO_your_slug_here: input envelope invalid: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        f"TODO_your_slug_here: run_id={run_id} "
        f"backend_slug={envelope.backend_slug} "
        f"parameters={envelope.parameters!r}",
    )

    # 2. TODO: domain work goes here. ``envelope.parameters`` is the
    #    learner-submitted parameter dict (already validated by the
    #    LMS against ``interactive_task.yaml``). Populate ``outputs``
    #    with values matching your manifest's ``outputs:`` block.
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

    # 3. Write the output envelope.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "output.json").write_text(output.model_dump_json(indent=2))

    print(
        f"TODO_your_slug_here: wrote output envelope ({output_dir / 'output.json'})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
