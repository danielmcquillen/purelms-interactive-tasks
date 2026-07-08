/**
 * Canvas restore tests — the inverse of the serializer.
 *
 * A capable Drawflow mock tracks ``addNode`` (handing back incrementing ids,
 * recording positions), ``addConnection``, a structural ``export()`` rebuilt
 * from those records, and ``import()`` (the reroute-restore path). So we can
 * assert ``restore()`` re-creates nodes at their saved positions, maps named
 * ports to Drawflow's positional classes, and injects saved waypoints.
 */

import { describe, expect, it, vi } from "vitest";

import scenarioJson from "../src/vendor/hydronic_loop.scenario.json";
import type { Diagram, Scenario } from "../src/types";

const { addNodeSpy, addConnSpy, importSpy } = vi.hoisted(() => ({
  addNodeSpy: vi.fn(),
  addConnSpy: vi.fn(),
  importSpy: vi.fn(),
}));

vi.mock("drawflow", () => ({
  default: class MockDrawflow {
    reflow = "fixed";
    reroute = false;
    reroute_fix_curvature = false;
    private counter = 0;
    private nodes = new Map<number, { type: string; x: number; y: number }>();
    private conns: Array<{ src: number; tgt: number; oc: string; ic: string }> = [];
    start(): void {}
    clear(): void {
      this.counter = 0;
      this.nodes.clear();
      this.conns = [];
    }
    on(): void {}
    addNode(
      _name: string,
      _inputs: number,
      _outputs: number,
      x: number,
      y: number,
      _cls: string,
      data: { type: string },
    ): number {
      this.counter += 1;
      this.nodes.set(this.counter, { type: data.type, x, y });
      addNodeSpy(data.type, this.counter, x, y);
      return this.counter;
    }
    addConnection(src: number, tgt: number, oc: string, ic: string): void {
      this.conns.push({ src, tgt, oc, ic });
      addConnSpy(src, tgt, oc, ic);
    }
    getNodeFromId(id: string | number): { data?: { type?: string } } | undefined {
      const n = this.nodes.get(Number(id));
      return n ? { data: { type: n.type } } : undefined;
    }
    export(): unknown {
      const data: Record<string, unknown> = {};
      for (const [id, n] of this.nodes) {
        const outputs: Record<
          string,
          { connections: Array<{ node: string; output: string }> }
        > = {};
        for (const c of this.conns.filter((x) => x.src === id)) {
          (outputs[c.oc] ??= { connections: [] }).connections.push({
            node: String(c.tgt),
            output: c.ic,
          });
        }
        data[String(id)] = {
          id,
          name: n.type,
          data: { type: n.type },
          inputs: {},
          outputs,
          pos_x: n.x,
          pos_y: n.y,
        };
      }
      return { drawflow: { Home: { data } } };
    }
    import(e: unknown): void {
      importSpy(e);
    }
  },
}));

const { createCanvas } = await import("../src/canvas");

const SCENARIO = scenarioJson as unknown as Scenario;

/** The known-good hydronic loop as a diagram.v1 graph. */
function correctLoop(): Diagram {
  return {
    schema: "purelms.diagram.v1",
    nodes: [
      { id: "10", type: "boiler" },
      { id: "20", type: "pump" },
      { id: "30", type: "radiator" },
      { id: "40", type: "room" },
    ],
    edges: [
      { source: { node: "10", port: "port_b" }, target: { node: "20", port: "port_a" } },
      { source: { node: "20", port: "port_b" }, target: { node: "30", port: "port_a" } },
      { source: { node: "30", port: "port_b" }, target: { node: "10", port: "port_a" } },
      { source: { node: "30", port: "heat" }, target: { node: "40", port: "heat" } },
      { source: { node: "40", port: "T_room" }, target: { node: "10", port: "T_room" } },
    ],
  };
}

describe("createCanvas.restore", () => {
  it("re-adds every node, in graph order", () => {
    addNodeSpy.mockClear();
    const canvas = createCanvas(document.createElement("div"), SCENARIO);

    canvas.restore(correctLoop());

    expect(addNodeSpy).toHaveBeenCalledTimes(4);
    expect(addNodeSpy.mock.calls.map((call) => call[0])).toEqual([
      "boiler",
      "pump",
      "radiator",
      "room",
    ]);
  });

  it("maps named ports back to positional output_N / input_M classes", () => {
    addConnSpy.mockClear();
    const canvas = createCanvas(document.createElement("div"), SCENARIO);

    canvas.restore(correctLoop());

    // New ids are assigned 1..4 in node order: boiler=1, pump=2, radiator=3, room=4.
    expect(addConnSpy).toHaveBeenCalledTimes(5);
    // boiler.port_b (only output -> output_1) -> pump.port_a (first input -> input_1)
    expect(addConnSpy).toHaveBeenCalledWith(1, 2, "output_1", "input_1");
    // radiator.heat (second output -> output_2) -> room.heat (first input -> input_1)
    expect(addConnSpy).toHaveBeenCalledWith(3, 4, "output_2", "input_1");
    // room.T_room (only output -> output_1) -> boiler.T_room (second input -> input_2)
    expect(addConnSpy).toHaveBeenCalledWith(4, 1, "output_1", "input_2");
  });

  it("places nodes at their saved positions", () => {
    addNodeSpy.mockClear();
    const canvas = createCanvas(document.createElement("div"), SCENARIO);

    canvas.restore(
      {
        schema: "purelms.diagram.v1",
        nodes: [
          { id: "10", type: "boiler" },
          { id: "20", type: "pump" },
        ],
        edges: [],
      },
      {
        positions: { "10": { x: 111, y: 222 }, "20": { x: 333, y: 444 } },
        waypoints: {},
      },
    );

    // addNodeSpy(type, id, x, y)
    expect(addNodeSpy).toHaveBeenCalledWith("boiler", 1, 111, 222);
    expect(addNodeSpy).toHaveBeenCalledWith("pump", 2, 333, 444);
  });

  it("injects saved reroute waypoints into the re-imported canvas", () => {
    importSpy.mockClear();
    const canvas = createCanvas(document.createElement("div"), SCENARIO);

    canvas.restore(
      {
        schema: "purelms.diagram.v1",
        nodes: [
          { id: "10", type: "boiler" },
          { id: "20", type: "pump" },
        ],
        edges: [
          { source: { node: "10", port: "port_b" }, target: { node: "20", port: "port_a" } },
        ],
      },
      {
        positions: {},
        waypoints: { "10|port_b|20|port_a": [{ x: 150, y: 300 }] },
      },
    );

    expect(importSpy).toHaveBeenCalledTimes(1);
    const imported = importSpy.mock.calls[0]?.[0] as {
      drawflow: {
        Home: {
          data: Record<
            string,
            {
              outputs: Record<
                string,
                { connections: Array<{ points?: Array<{ pos_x: number; pos_y: number }> }> }
              >;
            }
          >;
        };
      };
    };
    // boiler is id 1, port_b -> output_1, the single connection to pump.
    const conn = imported.drawflow.Home.data["1"]?.outputs["output_1"]?.connections[0];
    expect(conn?.points).toEqual([{ pos_x: 150, pos_y: 300 }]);
  });

  it("skips edges whose ports don't resolve, without throwing", () => {
    addConnSpy.mockClear();
    const canvas = createCanvas(document.createElement("div"), SCENARIO);

    canvas.restore({
      schema: "purelms.diagram.v1",
      nodes: [
        { id: "1", type: "boiler" },
        { id: "2", type: "pump" },
      ],
      edges: [
        { source: { node: "1", port: "nope" }, target: { node: "2", port: "port_a" } },
      ],
    });

    expect(addConnSpy).not.toHaveBeenCalled();
  });
});
