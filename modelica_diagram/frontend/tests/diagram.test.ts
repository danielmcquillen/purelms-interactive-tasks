/**
 * Tests for the Drawflow → purelms.diagram.v1 serialiser.
 *
 * Builds a Drawflow ``export()`` object representing a correctly-wired
 * hydronic loop and asserts it serialises to the exact diagram.v1 graph (node
 * ids → types, Drawflow port indices → scenario port names).
 */

import { describe, expect, it } from "vitest";

import { buildLayout, buildPortMaps, drawflowToDiagram } from "../src/diagram";
import type {
  DrawflowExport,
  DrawflowNode,
  DrawflowOutputConnection,
  Scenario,
} from "../src/types";
import scenarioJson from "../src/vendor/hydronic_loop.scenario.json";

const SCENARIO = scenarioJson as unknown as Scenario;

function node(
  id: number,
  type: string,
  outputs: DrawflowNode["outputs"],
  pos: { x: number; y: number } = { x: 0, y: 0 },
): DrawflowNode {
  return {
    id,
    name: type,
    data: { type },
    inputs: {},
    outputs,
    pos_x: pos.x,
    pos_y: pos.y,
  };
}

function out(...connections: DrawflowOutputConnection[]) {
  return { connections };
}

/**
 * A correctly-wired hydronic loop as Drawflow would export it.
 *
 * The water loop closes at the radiator (radiator.port_b → boiler.port_a); the
 * radiator's 2nd output (heat) drives the room; the room's temperature output
 * feeds the boiler's thermostat input. Mirrors the scenario's expected graph.
 */
function correctLoopExport(): DrawflowExport {
  const data: Record<string, DrawflowNode> = {
    "1": node(1, "boiler", { output_1: out({ node: "2", output: "input_1" }) }),
    "2": node(2, "pump", { output_1: out({ node: "3", output: "input_1" }) }),
    "3": node(3, "radiator", {
      output_1: out({ node: "1", output: "input_1" }), // port_b -> boiler.port_a (loop)
      output_2: out({ node: "4", output: "input_1" }), // heat -> room.heat
    }),
    "4": node(4, "room", {
      output_1: out({ node: "1", output: "input_2" }), // T_room -> boiler.T_room
    }),
  };
  return { drawflow: { Home: { data } } };
}

describe("buildPortMaps", () => {
  it("splits ports by ui_side, preserving palette order", () => {
    const maps = buildPortMaps(SCENARIO);
    expect(maps.boiler).toEqual({ inputs: ["port_a", "T_room"], outputs: ["port_b"] });
    expect(maps.pump).toEqual({ inputs: ["port_a"], outputs: ["port_b"] });
    expect(maps.radiator).toEqual({ inputs: ["port_a"], outputs: ["port_b", "heat"] });
    expect(maps.room).toEqual({ inputs: ["heat"], outputs: ["T_room"] });
  });
});

describe("drawflowToDiagram", () => {
  it("serialises a correct loop into the expected diagram.v1 graph", () => {
    const diagram = drawflowToDiagram(correctLoopExport(), SCENARIO);

    expect(diagram.schema).toBe("purelms.diagram.v1");
    expect(diagram.nodes).toEqual([
      { id: "1", type: "boiler" },
      { id: "2", type: "pump" },
      { id: "3", type: "radiator" },
      { id: "4", type: "room" },
    ]);
    expect(diagram.edges).toEqual([
      { source: { node: "1", port: "port_b" }, target: { node: "2", port: "port_a" } },
      { source: { node: "2", port: "port_b" }, target: { node: "3", port: "port_a" } },
      { source: { node: "3", port: "port_b" }, target: { node: "1", port: "port_a" } },
      { source: { node: "3", port: "heat" }, target: { node: "4", port: "heat" } },
      { source: { node: "4", port: "T_room" }, target: { node: "1", port: "T_room" } },
    ]);
  });

  it("emits isolated nodes and skips dangling connections", () => {
    const data: Record<string, DrawflowNode> = {
      "1": node(1, "boiler", {
        output_1: out({ node: "99", output: "input_1" }), // target doesn't exist
      }),
      "2": node(2, "pump", {}), // isolated
    };
    const diagram = drawflowToDiagram({ drawflow: { Home: { data } } }, SCENARIO);
    expect(diagram.nodes).toEqual([
      { id: "1", type: "boiler" },
      { id: "2", type: "pump" },
    ]);
    expect(diagram.edges).toEqual([]); // dangling edge dropped, not emitted malformed
  });

  it("handles an empty canvas", () => {
    const diagram = drawflowToDiagram({ drawflow: { Home: { data: {} } } }, SCENARIO);
    expect(diagram).toEqual({ schema: "purelms.diagram.v1", nodes: [], edges: [] });
  });
});

describe("buildLayout", () => {
  it("extracts node positions and reroute waypoints, neutrally", () => {
    const data: Record<string, DrawflowNode> = {
      "1": node(
        1,
        "boiler",
        {
          output_1: out({
            node: "2",
            output: "input_1",
            points: [
              { pos_x: 120, pos_y: 240 },
              { pos_x: 180, pos_y: 260 },
            ],
          }),
        },
        { x: 60, y: 80 },
      ),
      "2": node(2, "pump", {}, { x: 300, y: 80 }),
    };
    const layout = buildLayout({ drawflow: { Home: { data } } }, SCENARIO);

    expect(layout.positions).toEqual({
      "1": { x: 60, y: 80 },
      "2": { x: 300, y: 80 },
    });
    // Waypoints keyed by src|srcPort|tgt|tgtPort, in neutral {x, y}.
    expect(layout.waypoints).toEqual({
      "1|port_b|2|port_a": [
        { x: 120, y: 240 },
        { x: 180, y: 260 },
      ],
    });
  });

  it("omits waypoints for connections without points", () => {
    const data: Record<string, DrawflowNode> = {
      "1": node(1, "boiler", { output_1: out({ node: "2", output: "input_1" }) }),
      "2": node(2, "pump", {}),
    };
    const layout = buildLayout({ drawflow: { Home: { data } } }, SCENARIO);
    expect(layout.waypoints).toEqual({});
    expect(layout.positions).toEqual({ "1": { x: 0, y: 0 }, "2": { x: 0, y: 0 } });
  });
});
