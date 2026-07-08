"""Tests for the modelica_diagram topology checker.

Pure Python — no FMU, no runtime, no Docker. Builds diagrams from the real
``hydronic_loop`` scenario and mutates them to exercise each discrepancy path.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

# Make the backend package importable without installing it (mirrors echo).
sys.path.insert(0, str(Path(__file__).parent.parent))
from topology import check_topology

_SCENARIO_PATH = (
    Path(__file__).parent.parent / "scenarios" / "hydronic_loop" / "scenario.json"
)
SCENARIO = json.loads(_SCENARIO_PATH.read_text())


def _correct_diagram() -> dict:
    """A diagram that exactly matches the scenario (node id == type)."""
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


def test_correct_diagram_passes():
    result = check_topology(_correct_diagram(), SCENARIO)
    assert result.correct is True
    assert result.messages == []


def test_backwards_fluid_edge_still_passes():
    """A fluid connector is acausal — drawing it reversed is the same edge."""
    d = _correct_diagram()
    # Edge 0 is the boiler.port_b -> pump.port_a fluid edge; reverse it.
    d["edges"][0]["source"], d["edges"][0]["target"] = (
        d["edges"][0]["target"],
        d["edges"][0]["source"],
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is True, result.messages


def test_backwards_heat_edge_still_passes():
    """A heat connector is acausal too — the radiator<->room link matches reversed."""
    d = _correct_diagram()
    heat = next(
        e
        for e in d["edges"]
        if e["source"]["port"] == "heat" or e["target"]["port"] == "heat"
    )
    heat["source"], heat["target"] = heat["target"], heat["source"]
    result = check_topology(d, SCENARIO)
    assert result.correct is True, result.messages


def test_heat_to_fluid_connection_is_rejected():
    """Heat and fluid are different connector kinds — they can't be joined."""
    d = _correct_diagram()
    d["edges"].append(
        {
            "source": {"node": "radiator", "port": "heat"},  # heat
            "target": {"node": "pump", "port": "port_a"},  # fluid
        },
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("different types" in m for m in result.messages)


def test_reversed_signal_edge_is_rejected():
    """A signal connector is directed — output -> input only."""
    d = _correct_diagram()
    # The last edge is the signal edge room.T_room(out) -> boiler.T_room(in).
    d["edges"][-1]["source"], d["edges"][-1]["target"] = (
        d["edges"][-1]["target"],
        d["edges"][-1]["source"],
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("output to an input" in m for m in result.messages)


def test_missing_fluid_edge_is_reported():
    d = _correct_diagram()
    d["edges"].pop(0)  # drop boiler.port_b -> pump.port_a
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any(m.startswith("Connect ") for m in result.messages)


def test_extra_edge_is_reported():
    d = _correct_diagram()
    # A valid-port but unexpected fluid connection: boiler.port_b -> radiator.port_a
    # (short-circuiting the pump). Not part of the expected loop.
    d["edges"].append(
        {
            "source": {"node": "boiler", "port": "port_b"},
            "target": {"node": "radiator", "port": "port_a"},
        },
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any(m.startswith("Remove the connection") for m in result.messages)


def test_missing_component_is_reported():
    d = _correct_diagram()
    d["nodes"] = [n for n in d["nodes"] if n["type"] != "pump"]
    d["edges"] = [
        e
        for e in d["edges"]
        if "pump" not in (e["source"]["node"], e["target"]["node"])
    ]
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("Add a Pump" in m for m in result.messages)


def test_unknown_component_is_reported():
    d = _correct_diagram()
    d["nodes"].append({"id": "furnace", "type": "furnace"})
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("furnace" in m for m in result.messages)


def test_fluid_to_signal_connection_is_rejected():
    d = _correct_diagram()
    d["edges"].append(
        {
            "source": {"node": "room", "port": "T_room"},  # signal
            "target": {"node": "pump", "port": "port_a"},  # fluid
        },
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("different types" in m for m in result.messages)


def test_unknown_port_is_reported():
    d = _correct_diagram()
    d["edges"].append(
        {
            "source": {"node": "boiler", "port": "nonexistent"},
            "target": {"node": "pump", "port": "port_a"},
        },
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("has no 'nonexistent' port" in m for m in result.messages)


def test_self_loop_is_rejected():
    d = _correct_diagram()
    d["edges"].append(
        {
            "source": {"node": "pump", "port": "port_a"},
            "target": {"node": "pump", "port": "port_b"},
        },
    )
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("connected to itself" in m for m in result.messages)


def test_duplicate_edge_is_reported():
    d = _correct_diagram()
    d["edges"].append(copy.deepcopy(d["edges"][0]))  # duplicate edge 0
    result = check_topology(d, SCENARIO)
    assert result.correct is False
    assert any("more than once" in m for m in result.messages)


@pytest.mark.parametrize(
    "bad",
    [None, "not a dict", {"nodes": "x", "edges": []}, {"edges": []}, 42],
)
def test_malformed_diagram_never_crashes(bad):
    result = check_topology(bad, SCENARIO)
    assert result.correct is False
    assert result.messages  # a contract-violation message, not an exception
