"""``modelica_diagram`` backend — container entrypoint (ADR-0019).

Reads the input envelope, grades the learner's diagram (structural
``purelms.diagram.v1`` validation + scenario topology check), and — only if the
topology is correct — runs the scenario's pre-compiled FMU. Writes a SUCCESS
output envelope either way: a wrong diagram is a *valid* run with
``topology_correct=false`` (the run worked; the answer was wrong). Only an
infrastructure failure (can't read the input) is a nonzero exit.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fmpy_runner import FmuRunError
from fmpy_runner import run_fmu
from grading import grade_diagram
from purelms_itask_runtime import RuntimeLocation
from purelms_itask_runtime import read_input_envelope
from purelms_itask_runtime import write_output_envelope
from purelms_shared.constants import OutputStatus
from purelms_shared.envelopes import Message
from purelms_shared.envelopes import SimulationOutputEnvelope

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"
_DEFAULT_SCENARIO = "hydronic_loop"


def main() -> int:
    location = RuntimeLocation.from_env()
    started_at = time.monotonic()

    try:
        envelope = read_input_envelope(location)
    except Exception as exc:  # infra failure — the only nonzero exit
        print(f"modelica_diagram: cannot read input envelope: {exc}", file=sys.stderr)
        return 1

    params = envelope.parameters or {}
    scenario_id = params.get("scenario", _DEFAULT_SCENARIO)
    scenario = _load_scenario(scenario_id)

    outputs: dict = {"topology_correct": False}
    messages: list[Message] = []

    if scenario is None:
        messages.append(_msg("error", "SCENARIO", f"Unknown scenario '{scenario_id}'."))
    else:
        grade = grade_diagram(params.get("diagram_json", ""), scenario)
        outputs["topology_correct"] = grade.topology_correct
        level = "info" if grade.topology_correct else "warning"
        messages.extend(_msg(level, "TOPOLOGY", line) for line in grade.messages)
        if grade.topology_correct:
            messages.extend(_run_simulation(scenario, params, envelope, outputs))

    output = SimulationOutputEnvelope(
        run_id=envelope.run_id,
        status=OutputStatus.SUCCESS,
        outputs=outputs,
        artifacts=[],
        messages=messages,
        metrics={},
        runtime_seconds=time.monotonic() - started_at,
    )
    write_output_envelope(location, output, envelope.context)
    return 0


def _run_simulation(scenario, params, envelope, outputs) -> list[Message]:
    """Run the FMU for a correct diagram; return any learner messages.

    A correct topology that can't simulate (FMU absent, timeout, solver error)
    is NOT a failed run — ``topology_correct`` stays true and the learner gets a
    message. ``fmpy`` itself is imported lazily inside ``fmpy_runner`` so the
    topology path never loads native code.
    """
    fmu_path = _SCENARIOS_DIR / scenario["id"] / "model.fmu"
    timeout_s = getattr(envelope.context, "timeout_seconds", 60) or 60
    try:
        outputs.update(run_fmu(fmu_path, params, scenario, timeout_s=timeout_s))
    except FmuRunError as exc:
        return [_msg("warning", "FMU", str(exc))]
    return []


def _load_scenario(scenario_id: str) -> dict | None:
    """Load ``scenarios/<id>/scenario.json``; None if unknown.

    ``scenario_id`` is a locked enum parameter, but sanitise it anyway so a
    crafted value can never escape the scenarios directory.
    """
    safe = "".join(c for c in str(scenario_id) if c.isalnum() or c in "_-")
    path = _SCENARIOS_DIR / safe / "scenario.json"
    if not safe or not path.is_file():
        return None
    return json.loads(path.read_text())


def _msg(level: str, code: str, text: str) -> Message:
    return Message.model_validate({"level": level, "code": code, "text": text})


if __name__ == "__main__":
    raise SystemExit(main())
