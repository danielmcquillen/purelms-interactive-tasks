"""Grade a learner's diagram for the ``modelica_diagram`` task.

Two steps, both BEFORE any FMU runs:

1. **Structural** — validate the submitted ``diagram_json`` string against the
   canonical ``purelms.diagram.v1`` JSON Schema (shipped in ``purelms_shared``)
   via ``jsonschema``. A contract violation is graded as a topology failure,
   never a crash.
2. **Semantic** — run the scenario-aware topology checker
   (``topology.check_topology``): component types, ports, per-kind edge
   canonicalization, multiset match against the expected graph.

The result the runner turns into output-envelope fields. A wrong diagram is a
*valid* run with ``topology_correct=False`` — the run worked, the answer was
wrong (mirroring how assessments separate evaluation from correctness).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import field

import jsonschema
from purelms_shared.diagram import diagram_v1_schema
from topology import check_topology


@dataclass
class GradeResult:
    """Outcome of grading a diagram.

    ``diagram`` is the parsed graph, populated only when the topology is
    correct (the runner needs it to drive the FMU); ``None`` otherwise.
    """

    topology_correct: bool
    messages: list[str] = field(default_factory=list)
    diagram: dict | None = None


def grade_diagram(diagram_json: str, scenario: dict) -> GradeResult:
    """Structurally + semantically grade ``diagram_json`` against ``scenario``."""
    if not diagram_json:
        return GradeResult(False, ["No diagram was submitted."])
    try:
        diagram = json.loads(diagram_json)
    except (TypeError, ValueError):
        return GradeResult(False, ["This diagram couldn't be read (invalid JSON)."])
    if not isinstance(diagram, dict):
        return GradeResult(False, ["This diagram couldn't be read."])

    # 1. Structural contract (purelms.diagram.v1). A violation is a graded
    # topology failure, not an exception.
    try:
        jsonschema.validate(diagram, diagram_v1_schema())
    except jsonschema.ValidationError as exc:
        return GradeResult(
            False,
            [f"The diagram doesn't match the expected format: {exc.message}"],
        )

    # 2. Semantic topology check (scenario-aware).
    result = check_topology(diagram, scenario)
    return GradeResult(
        topology_correct=result.correct,
        messages=result.messages,
        diagram=diagram if result.correct else None,
    )
