/**
 * Tests for the Drawflow → purelms.diagram.v1 serialiser.
 *
 * Builds a Drawflow ``export()`` object representing a correctly-wired
 * hydronic loop and asserts it serialises to the exact diagram.v1 graph (node
 * ids → types, Drawflow port indices → scenario port names).
 */

import { describe, expect, it } from "vitest";

import { buildPortMaps, drawflowToDiagram } from "../src/diagram";
import type { DrawflowExport, DrawflowNode, Scenario } from "../src/types";
import scenarioJson from "../src/vendor/hydronic_loop.scenario.json";

const SCENARIO = scenarioJson as unknown as Scenario;

function node(
  id: number,
  type: string,
  outputs: DrawflowNode["outputs"],
): DrawflowNode {
  return { id, name: type, data: { type }, inputs: {}, outputs };
}

function out(...connections: Array<{ node: string; output: string }>) {
  return { connections };
}

/** A correctly-wired hydronic loop as Drawflow would export it. */
function correctLoopExport(): DrawflowExport {
  const data: Record<string, DrawflowNode> = {
    "1": node(1, "boiler", { output_1: out({ node: "2", output: "input_1" }) }),
    "2": node(2, "pump", { output_1: out({ node: "3", output: "input_1" }) }),
    "3": node(3, "radiator", { output_1: out({ node: "4", output: "input_1" }) }),
    "4": node(4, "room", {
      output_1: out({ node: "1", output: "input_1" }), // port_b -> boiler.port_a
      output_2: out({ node: "1", output: "input_2" }), // T_room -> boiler.T_set
    }),
  };
  return { drawflow: { Home: { data } } };
}

describe("buildPortMaps", () => {
  it("splits ports by ui_side, preserving palette order", () => {
    const maps = buildPortMaps(SCENARIO);
    expect(maps.boiler).toEqual({ inputs: ["port_a", "T_set"], outputs: ["port_b"] });
    expect(maps.room).toEqual({ inputs: ["port_a"], outputs: ["port_b", "T_room"] });
    expect(maps.pump).toEqual({ inputs: ["port_a"], outputs: ["port_b"] });
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
      { source: { node: "3", port: "port_b" }, target: { node: "4", port: "port_a" } },
      { source: { node: "4", port: "port_b" }, target: { node: "1", port: "port_a" } },
      { source: { node: "4", port: "T_room" }, target: { node: "1", port: "T_set" } },
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
