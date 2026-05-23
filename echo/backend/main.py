"""
Echo backend — container entrypoint.

Reads the input envelope from ``$PURELMS_INPUT_DIR/input.json``,
echoes its parameters back as outputs, writes a ``SUCCESS`` output
envelope to ``$PURELMS_OUTPUT_DIR/output.json``, exits 0.

This is the permanent LMS-side integration-test fixture. Real
backends (EnergyPlus, FMU, etc.) follow the same pattern but
actually do domain work between the read and the write.
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
    """Container entrypoint. Returns the exit code.

    Three contractual touchpoints:

    1. **Input read.** ``$PURELMS_INPUT_DIR/input.json`` MUST exist
       and parse as ``SimulationInputEnvelope``. If not → exit 1.
    2. **Domain work.** For echo: nothing. Real backends run their
       solver / pipeline / etc. here.
    3. **Output write.** ``$PURELMS_OUTPUT_DIR/output.json`` MUST be
       written before exit 0 (the LMS reads it and treats missing
       file as a contract violation).
    """
    input_dir = Path(os.environ.get("PURELMS_INPUT_DIR", "/purelms/input"))
    output_dir = Path(os.environ.get("PURELMS_OUTPUT_DIR", "/purelms/output"))
    run_id = os.environ.get("PURELMS_RUN_ID", "unknown")

    started_at = time.monotonic()

    # 1. Read + parse the input envelope.
    input_path = input_dir / "input.json"
    if not input_path.exists():
        print(
            f"echo: missing input envelope at {input_path}",
            file=sys.stderr,
        )
        return 1

    try:
        envelope = SimulationInputEnvelope.model_validate_json(input_path.read_text())
    except Exception as exc:
        print(f"echo: input envelope invalid: {exc}", file=sys.stderr)
        return 1

    print(
        f"echo: run_id={run_id} backend_slug={envelope.backend_slug} "
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

    # 3. Write the output envelope.
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "output.json").write_text(output.model_dump_json(indent=2))

    print(f"echo: wrote output envelope ({output_dir / 'output.json'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
