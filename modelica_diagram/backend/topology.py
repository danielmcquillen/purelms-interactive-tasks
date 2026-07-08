"""Topology checker for the ``modelica_diagram`` InteractiveTask.

Pure-Python, dependency-free: takes a learner's parsed diagram (the
``purelms.diagram.v1`` graph) plus a scenario definition and decides whether
the diagram's TOPOLOGY matches the scenario's expected system — returning one
learner-readable message per discrepancy.

The structural contract (``purelms.diagram.v1`` JSON Schema: shape, limits,
closedness) is validated upstream in ``main.py`` against the canonical schema
in ``purelms_shared``. This module is the SEMANTIC layer: component types,
ports, per-kind edge canonicalization, and multiset matching against the
expected graph. It is deliberately defensive — a malformed diagram yields a
contract-violation message, never an exception — so a bad payload is graded as
a topology failure rather than crashing the run.

Edge identity is canonicalized PER CONNECTOR KIND, declared per port in the
scenario palette:

- a ``fluid`` connector is acausal (flow goes either way), so a fluid edge's
  identity is the UNORDERED pair of its two ``(type, port)`` endpoints — a
  "backwards" but physically identical connection still matches;
- a ``signal`` connector is causal (``output -> input``), so a signal edge's
  identity is the ORDERED pair, and the checker requires the source port to be
  an output and the target an input.

So the expected-graph comparison matches on physics, not on drawing order.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from typing import Any

_UNREADABLE = "This diagram couldn't be read. Please reload the page and rebuild it."


class _MalformedDiagramError(Exception):
    """Internal: the diagram isn't a readable graph (contract violation)."""


@dataclass
class TopologyResult:
    """Outcome of a topology check.

    ``correct`` is True only when the diagram matches the scenario's expected
    graph exactly; ``messages`` carries one learner-readable line per
    discrepancy (empty when correct).
    """

    correct: bool
    messages: list[str] = field(default_factory=list)


def check_topology(diagram: Any, scenario: dict) -> TopologyResult:
    """Check ``diagram`` against ``scenario``'s expected graph."""
    palette = _index_palette(scenario)
    labels = {ptype: meta["label"] for ptype, meta in palette.items()}

    nodes = _safe_list(diagram, "nodes")
    edges = _safe_list(diagram, "edges")
    if nodes is None or edges is None:
        return TopologyResult(correct=False, messages=[_UNREADABLE])

    try:
        id_to_type, messages = _grade_nodes(nodes, palette, labels, scenario)
        messages += _grade_edges(edges, id_to_type, palette, labels, scenario)
    except _MalformedDiagramError:
        return TopologyResult(correct=False, messages=[_UNREADABLE])

    return TopologyResult(correct=not messages, messages=messages)


def _grade_nodes(
    nodes: list,
    palette: dict[str, dict],
    labels: dict[str, str],
    scenario: dict,
) -> tuple[dict[str, str], list[str]]:
    """Map node id -> type, validating each type + the node multiset vs expected."""
    id_to_type: dict[str, str] = {}
    messages: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise _MalformedDiagramError
        nid, ntype = node.get("id"), node.get("type")
        if not isinstance(nid, str) or not isinstance(ntype, str):
            raise _MalformedDiagramError
        id_to_type[nid] = ntype
        if ntype not in palette:
            messages.append(
                f"'{ntype}' isn't a component you can use in this exercise."
            )

    # Each component type appears once in the MVP loop.
    have = Counter(t for t in id_to_type.values() if t in palette)
    want = Counter(scenario["expected"]["nodes"])
    for ptype, count in (want - have).items():
        messages += [
            f"Add a {labels.get(ptype, ptype)} — your system needs one."
        ] * count
    for ptype, count in (have - want).items():
        messages += [
            f"Remove the extra {labels.get(ptype, ptype)} — it isn't part of this system.",
        ] * count
    return id_to_type, messages


def _grade_edges(
    edges: list,
    id_to_type: dict[str, str],
    palette: dict[str, dict],
    labels: dict[str, str],
    scenario: dict,
) -> list[str]:
    """Canonicalize the learner's edges + diff them against the expected graph."""
    messages: list[str] = []

    have_edges: Counter = Counter()
    for edge in edges:
        if not isinstance(edge, dict):
            raise _MalformedDiagramError
        key, err = _canonical_learner_edge(edge, id_to_type, palette, labels)
        if err:
            messages.append(err)
        elif key is not None:
            have_edges[key] += 1

    for key, count in have_edges.items():
        if count > 1:
            messages.append(
                f"You've drawn {_render_edge(key, labels)} more than once — "
                "remove the duplicate.",
            )

    want_edges = {
        key
        for edge in scenario["expected"]["edges"]
        if (key := _canonical_expected_edge(edge, palette)) is not None
    }
    have_set = set(have_edges)
    for key in want_edges - have_set:
        messages.append(f"Connect {_render_edge(key, labels)}.")
    for key in have_set - want_edges:
        messages.append(f"Remove the connection: {_render_edge(key, labels)}.")
    return messages


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _index_palette(scenario: dict) -> dict[str, dict]:
    """``type -> {label, ports: {name -> {kind, direction}}}`` from the palette."""
    out: dict[str, dict] = {}
    for entry in scenario.get("palette", []):
        ports = {
            p["name"]: {"kind": p.get("kind"), "direction": p.get("direction")}
            for p in entry.get("ports", [])
        }
        out[entry["type"]] = {
            "label": entry.get("label", entry["type"]),
            "ports": ports,
        }
    return out


def _safe_list(diagram: Any, key: str) -> list | None:
    """The list at ``diagram[key]``, or None if the diagram is malformed."""
    if not isinstance(diagram, dict):
        return None
    value = diagram.get(key)
    return value if isinstance(value, list) else None


def _canonical_learner_edge(
    edge: dict,
    id_to_type: dict[str, str],
    palette: dict[str, dict],
    labels: dict[str, str],
) -> tuple[tuple | None, str | None]:
    """Canonical key for a learner edge (node-qualified), or an error message."""
    src = edge.get("source") or {}
    tgt = edge.get("target") or {}
    s_node, s_port = src.get("node"), src.get("port")
    t_node, t_port = tgt.get("node"), tgt.get("port")

    if s_node == t_node:
        return None, "A component can't be connected to itself."
    s_type = id_to_type.get(s_node)
    t_type = id_to_type.get(t_node)
    if s_type is None or t_type is None:
        return None, "A connection points at a component that isn't on the canvas."
    s_meta = palette.get(s_type, {}).get("ports", {}).get(s_port)
    t_meta = palette.get(t_type, {}).get("ports", {}).get(t_port)
    if s_meta is None:
        return None, f"The {labels.get(s_type, s_type)} has no '{s_port}' port."
    if t_meta is None:
        return None, f"The {labels.get(t_type, t_type)} has no '{t_port}' port."
    return _build_key(s_type, s_port, s_meta, t_type, t_port, t_meta)


def _canonical_expected_edge(edge: dict, palette: dict[str, dict]) -> tuple | None:
    """Canonical key for an expected edge (type-qualified). Scenario is trusted."""
    s_type, s_port = edge["source"]["type"], edge["source"]["port"]
    t_type, t_port = edge["target"]["type"], edge["target"]["port"]
    s_meta = palette[s_type]["ports"][s_port]
    t_meta = palette[t_type]["ports"][t_port]
    key, _err = _build_key(s_type, s_port, s_meta, t_type, t_port, t_meta)
    return key


def _build_key(
    s_type: str,
    s_port: str,
    s_meta: dict,
    t_type: str,
    t_port: str,
    t_meta: dict,
) -> tuple[tuple | None, str | None]:
    """Per-kind canonical edge key: fluid + heat undirected, signal directed."""
    if s_meta["kind"] != t_meta["kind"]:
        return None, (
            "Those two ports are different types and can't be connected — "
            "match fluid to fluid, heat to heat, signal to signal."
        )
    # fluid (water) and heat (thermal) connectors are acausal — an edge's
    # identity is the unordered pair of endpoints, so a reversed but otherwise
    # identical connection still matches.
    if s_meta["kind"] in ("fluid", "heat"):
        return (s_meta["kind"], frozenset({(s_type, s_port), (t_type, t_port)})), None
    # signal: causal, must run output -> input
    if s_meta.get("direction") != "out" or t_meta.get("direction") != "in":
        return None, "A signal connection must run from an output to an input."
    return ("signal", (s_type, s_port), (t_type, t_port)), None


def _render_edge(key: tuple, labels: dict[str, str]) -> str:
    """Render a canonical edge key as a learner-readable phrase."""
    if key[0] in ("fluid", "heat"):
        (a_type, a_port), (b_type, b_port) = sorted(key[1])
        return (
            f"the {labels.get(a_type, a_type)} ({a_port}) and the "
            f"{labels.get(b_type, b_type)} ({b_port})"
        )
    _, (s_type, s_port), (t_type, t_port) = key
    return (
        f"the {labels.get(s_type, s_type)} ({s_port}) output to the "
        f"{labels.get(t_type, t_type)} ({t_port}) input"
    )
