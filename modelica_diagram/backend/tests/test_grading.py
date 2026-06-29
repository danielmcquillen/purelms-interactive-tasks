"""Tests for ``grade_diagram`` — diagram.v1 structural validation + topology.

Needs ``jsonschema`` + ``purelms_shared`` (for ``purelms.diagram.v1``) + the
local ``topology`` module. Run via the backend env (``just test
modelica_diagram``) or an ephemeral env carrying those deps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the backend modules importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent))
from grading import grade_diagram

_SCENARIO_PATH = (
    Path(__file__).parent.parent / "scenarios" / "hydronic_loop" / "scenario.json"
)
SCENARIO = json.loads(_SCENARIO_PATH.read_text())


def _correct_diagram() -> dict:
    """A diagram.v1 graph that exactly matches the scenario (node id == type)."""
    return {
        "schema": "purelms.diagram.v1",
        "nodes": [{"id": t, "type": t} for t in SCENARIO["expected"]["nodes"]],
        "edges": [
            {
                "source": {"node": e["source"]["type"], "port": e["source"]["port"]},
                "target": {"node": e["target"]["type"], "port": e["target"]["port"]},
            }
            for e in SCENARIO["expected"]["edges"]
        ],
    }


def test_correct_diagram_grades_correct():
    result = grade_diagram(json.dumps(_correct_diagram()), SCENARIO)
    assert result.topology_correct is True
    assert result.messages == []
    assert result.diagram is not None  # handed to the FMU runner


def test_empty_submission():
    result = grade_diagram("", SCENARIO)
    assert result.topology_correct is False
    assert result.diagram is None


def test_invalid_json_is_graded_not_raised():
    result = grade_diagram("{not json", SCENARIO)
    assert result.topology_correct is False
    assert any("couldn't be read" in m for m in result.messages)


def test_structural_violation_wrong_schema_const():
    diagram = _correct_diagram()
    diagram["schema"] = "purelms.diagram.v2"
    result = grade_diagram(json.dumps(diagram), SCENARIO)
    assert result.topology_correct is False
    assert any("expected format" in m for m in result.messages)


def test_structural_violation_node_missing_type():
    diagram = _correct_diagram()
    diagram["nodes"][0] = {"id": "boiler"}  # no `type` → schema rejects
    result = grade_diagram(json.dumps(diagram), SCENARIO)
    assert result.topology_correct is False
    assert any("expected format" in m for m in result.messages)


def test_structurally_valid_but_semantically_wrong():
    diagram = _correct_diagram()
    diagram["edges"].pop(0)  # drop a required connection
    result = grade_diagram(json.dumps(diagram), SCENARIO)
    assert result.topology_correct is False
    assert any(m.startswith("Connect ") for m in result.messages)
    assert result.diagram is None
