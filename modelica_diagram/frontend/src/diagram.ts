/**
 * Serialise a Drawflow canvas into a ``purelms.diagram.v1`` graph.
 *
 * Drawflow numbers a node's ports ``input_1``/``output_1``/… by position.
 * The scenario palette names them (``port_a``, ``T_set``, …) and assigns each
 * a ``ui_side``. {@link buildPortMaps} turns the palette into per-type ordered
 * input/output name lists, so a Drawflow connection (sourceNode ``output_k`` →
 * targetNode ``input_m``) resolves back to named ``(node, port)`` endpoints.
 *
 * Pure + dependency-free — the canvas hands its exported object in, this hands
 * back the graph the backend validates against diagram.v1 + topology-checks.
 */

import type { Diagram, DiagramEdge, DrawflowExport, Scenario } from "./types";

export interface PortMaps {
  [type: string]: { inputs: string[]; outputs: string[] };
}

/** Per-type ordered port-name lists, split by canvas side. */
export function buildPortMaps(scenario: Scenario): PortMaps {
  const maps: PortMaps = {};
  for (const entry of scenario.palette) {
    const inputs: string[] = [];
    const outputs: string[] = [];
    for (const port of entry.ports) {
      const side = port.ui_side ?? (port.direction === "out" ? "output" : "input");
      if (side === "output") {
        outputs.push(port.name);
      } else {
        inputs.push(port.name);
      }
    }
    maps[entry.type] = { inputs, outputs };
  }
  return maps;
}

/** ``"output_1"`` / ``"input_2"`` → zero-based index (``-1`` if unparseable). */
function portIndex(key: string): number {
  const last = key.split("_").pop();
  const n = Number(last);
  return Number.isInteger(n) && n > 0 ? n - 1 : -1;
}

/**
 * Convert a Drawflow ``export()`` object into a diagram.v1 graph.
 *
 * Edges are read from OUTPUT-side connections only (each is one directed
 * ``output → input`` link), so every edge is counted exactly once. Nodes or
 * ports that don't resolve against the scenario are skipped rather than
 * emitted malformed — the backend grades the result, and a dropped edge simply
 * shows up there as a missing connection.
 */
export function drawflowToDiagram(
  exported: DrawflowExport,
  scenario: Scenario,
): Diagram {
  const maps = buildPortMaps(scenario);
  const data = exported.drawflow?.Home?.data ?? {};
  const nodes = Object.values(data).map((node) => ({
    id: String(node.id),
    type: node.data.type,
  }));
  const edges: DiagramEdge[] = [];

  for (const node of Object.values(data)) {
    const srcMap = maps[node.data.type];
    if (!srcMap) {
      continue;
    }
    for (const [outKey, outPort] of Object.entries(node.outputs ?? {})) {
      const srcPort = srcMap.outputs[portIndex(outKey)];
      if (srcPort === undefined) {
        continue;
      }
      for (const conn of outPort.connections ?? []) {
        const target = data[String(conn.node)];
        if (!target) {
          continue;
        }
        const tgtPort = maps[target.data.type]?.inputs[portIndex(conn.output)];
        if (tgtPort === undefined) {
          continue;
        }
        edges.push({
          source: { node: String(node.id), port: srcPort },
          target: { node: String(target.id), port: tgtPort },
        });
      }
    }
  }

  return { schema: "purelms.diagram.v1", nodes, edges };
}
