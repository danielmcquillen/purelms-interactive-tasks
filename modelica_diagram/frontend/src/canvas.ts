/**
 * Drawflow canvas wiring for the modelica_diagram task.
 *
 * Wraps a Drawflow editor behind a small handle: ``addComponent`` drops a typed
 * node (its input/output ports come from the scenario palette), and
 * ``serialize`` exports the canvas to a ``purelms.diagram.v1`` graph via
 * {@link drawflowToDiagram}. The Drawflow-specific surface lives here so the
 * mount module stays about layout + the submit/poll lifecycle.
 *
 * Each port renders on its own row — input label on the left, output label on
 * the right — coloured by connector kind (fluid / heat / signal), and the
 * dots are CSS-aligned to those rows (Drawflow reads dot geometry via
 * getBoundingClientRect, so moving them keeps connections attached).
 */

import Drawflow from "drawflow";

import { buildLayout, buildPortMaps, drawflowToDiagram } from "./diagram";
import type {
  Diagram,
  DrawflowExport,
  Layout,
  PaletteEntry,
  Scenario,
  ScenarioPort,
} from "./types";

export interface CanvasHandle {
  /** Drop a node of ``type`` onto the canvas (no-op for unknown types). */
  addComponent(type: string): void;
  /** Export the current canvas as a diagram.v1 graph. */
  serialize(): Diagram;
  /** Export the neutral canvas layout (node positions + reroute waypoints). */
  serializeLayout(): Layout;
  /** Rebuild the canvas from a diagram.v1 graph + optional neutral layout. */
  restore(diagram: Diagram, layout?: Layout): void;
  /** Remove every node. */
  clear(): void;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Which canvas side a port renders on (mirrors diagram.ts buildPortMaps). */
function portSide(port: ScenarioPort): "input" | "output" {
  return port.ui_side ?? (port.direction === "out" ? "output" : "input");
}

/** Drawflow's connectionCreated payload (only the fields we use). */
interface ConnectionInfo {
  output_id: string | number;
  input_id: string | number;
  output_class: string;
  input_class: string;
}

/** A connection laid down during restore, with the neutral edge key used to
 * match its saved reroute waypoints. */
interface WiredConnection {
  srcId: number;
  tgtId: number;
  outputClass: string;
  inputClass: string;
  key: string;
}

/**
 * Tag a freshly-created connection with ``mdl-wire-<kind>`` so the stylesheet
 * can stroke it to match its port labels. The kind comes from the SOURCE node's
 * output port, matched by output index — the same ordering the serializer uses.
 */
function colorConnection(
  container: HTMLElement,
  editor: Drawflow,
  palette: Map<string, PaletteEntry>,
  info: ConnectionInfo,
): void {
  const type = editor.getNodeFromId(info.output_id)?.data?.type;
  const entry = type ? palette.get(type) : undefined;
  if (!entry) {
    return;
  }
  const outputs = entry.ports.filter((p) => portSide(p) === "output");
  const index = Number(String(info.output_class).replace("output_", "")) - 1;
  const kind = outputs[index]?.kind;
  if (!kind) {
    return;
  }
  const selector =
    `.connection.node_in_node-${info.input_id}` +
    `.node_out_node-${info.output_id}.${info.output_class}.${info.input_class}`;
  container.querySelector(selector)?.classList.add(`mdl-wire-${kind}`);
}

/** Tidy default positions for the known hydronic-loop parts: boiler -> pump ->
 * radiator along the top, room below the radiator (short heat link). */
const DEFAULT_LAYOUT: Record<string, { x: number; y: number }> = {
  boiler: { x: 60, y: 80 },
  pump: { x: 280, y: 80 },
  radiator: { x: 500, y: 80 },
  room: { x: 500, y: 260 },
};

/**
 * One labelled row per port. Drawflow stacks input dot N + output dot N at the
 * same height, so row N carries input[N] (left) and output[N] (right); the dots
 * are aligned to these rows in CSS.
 */
function nodeHtml(
  label: string,
  inputs: ScenarioPort[],
  outputs: ScenarioPort[],
): string {
  const rowCount = Math.max(inputs.length, outputs.length);
  let rows = "";
  for (let i = 0; i < rowCount; i += 1) {
    const inPort = inputs[i];
    const outPort = outputs[i];
    const left = inPort
      ? `<span class="mdl-port mdl-kind-${inPort.kind}">${esc(inPort.label ?? inPort.name)}</span>`
      : "";
    const right = outPort
      ? `<span class="mdl-port mdl-port-out mdl-kind-${outPort.kind}">${esc(outPort.label ?? outPort.name)}</span>`
      : "";
    rows += `<div class="mdl-port-row">${left}${right}</div>`;
  }
  return (
    `<div class="mdl-node-title">${esc(label)}</div>` +
    `<div class="mdl-ports">${rows}</div>`
  );
}

export function createCanvas(
  container: HTMLElement,
  scenario: Scenario,
): CanvasHandle {
  const editor = new Drawflow(container);
  editor.reflow = "fixed";
  // Let learners bend the wires: double-click a connection to drop a draggable
  // point, double-click that point to remove it. Saved in the neutral layout
  // (layout_json), never in the semantic diagram.v1 graph.
  editor.reroute = true;
  editor.reroute_fix_curvature = true;
  editor.start();

  const palette = new Map<string, PaletteEntry>(
    scenario.palette.map((entry) => [entry.type, entry]),
  );

  // Colour each wire by its connector kind, to match the port labels. Drawflow
  // fires connectionCreated once the connection SVG is in the DOM, so we can
  // look up the source port's kind and tag the element for the stylesheet.
  editor.on("connectionCreated", (raw) => {
    colorConnection(container, editor, palette, raw as ConnectionInfo);
  });

  const maps = buildPortMaps(scenario);
  let placed = 0;

  // Add one node of ``type`` at its tidy default slot (stagger fallback) and
  // return the Drawflow id it was assigned. Shared by addComponent (learner
  // drops) and restore (rehydration).
  function placeNode(
    type: string,
    position?: { x: number; y: number },
  ): number | null {
    const entry = palette.get(type);
    if (!entry) {
      return null;
    }
    const inputs = entry.ports.filter((p) => portSide(p) === "input");
    const outputs = entry.ports.filter((p) => portSide(p) === "output");
    const slot = DEFAULT_LAYOUT[type];
    const x = position?.x ?? (slot ? slot.x : 30 + (placed % 3) * 200);
    const y = position?.y ?? (slot ? slot.y : 24 + Math.floor(placed / 3) * 150);
    placed += 1;
    return editor.addNode(
      type,
      inputs.length,
      outputs.length,
      x,
      y,
      `mdl-node mdl-${type}`,
      { type },
      nodeHtml(entry.label, inputs, outputs),
      false,
    );
  }

  // Restore reroute bends. Drawflow has no public per-connection waypoint API,
  // so inject the saved points into its OWN export structure and re-import (its
  // load() redraws the bends). Best-effort + isolated in a try/catch — if
  // anything is off, the already-built canvas (positions + topology + colours)
  // stands.
  function injectWaypoints(wired: WiredConnection[], layout?: Layout): void {
    const waypoints = layout?.waypoints;
    if (!waypoints || !wired.some((w) => waypoints[w.key]?.length)) {
      return;
    }
    // Snapshot the as-built canvas (positions + topology, no bends). import()
    // clears before loading, so if the bend-import throws we re-import this
    // snapshot rather than leaving a half-cleared canvas.
    const exported = editor.export() as DrawflowExport;
    const asBuilt: unknown = JSON.parse(JSON.stringify(exported));
    const data = exported.drawflow?.Home?.data ?? {};
    for (const w of wired) {
      const points = waypoints[w.key];
      if (!points || points.length === 0) {
        continue;
      }
      const conn = data[String(w.srcId)]?.outputs?.[w.outputClass]?.connections.find(
        (c) => String(c.node) === String(w.tgtId) && c.output === w.inputClass,
      );
      if (conn) {
        conn.points = points.map((p) => ({ pos_x: p.x, pos_y: p.y }));
      }
    }
    try {
      editor.import(exported);
    } catch {
      try {
        editor.import(asBuilt);
      } catch {
        // Nothing safe left to do; leave whatever import() produced.
      }
    }
    // import() redrew every wire; re-apply the kind colours.
    for (const w of wired) {
      colorConnection(container, editor, palette, {
        output_id: w.srcId,
        input_id: w.tgtId,
        output_class: w.outputClass,
        input_class: w.inputClass,
      });
    }
  }

  return {
    addComponent(type: string): void {
      placeNode(type);
    },
    serialize(): Diagram {
      return drawflowToDiagram(editor.export() as DrawflowExport, scenario);
    },
    serializeLayout(): Layout {
      return buildLayout(editor.export() as DrawflowExport, scenario);
    },
    restore(diagram: Diagram, layout?: Layout): void {
      editor.clear();
      placed = 0;
      // diagram.v1 node ids are the OLD Drawflow ids; re-adding assigns new
      // ones, so map old -> new (and remember each node's type) before wiring.
      const idMap = new Map<string, number>();
      const typeById = new Map<string, string>();
      for (const node of diagram.nodes) {
        typeById.set(node.id, node.type);
        const newId = placeNode(node.type, layout?.positions[node.id]);
        if (newId !== null) {
          idMap.set(node.id, newId);
        }
      }
      // Wire each connection, remembering its handles + neutral edge key so
      // reroute waypoints can be injected afterwards.
      const wired: WiredConnection[] = [];
      for (const edge of diagram.edges) {
        const srcId = idMap.get(edge.source.node);
        const tgtId = idMap.get(edge.target.node);
        const srcType = typeById.get(edge.source.node);
        const tgtType = typeById.get(edge.target.node);
        if (
          srcId === undefined ||
          tgtId === undefined ||
          srcType === undefined ||
          tgtType === undefined
        ) {
          continue;
        }
        // Named ports -> Drawflow's positional output_N / input_M classes.
        const outIndex = maps[srcType]?.outputs.indexOf(edge.source.port) ?? -1;
        const inIndex = maps[tgtType]?.inputs.indexOf(edge.target.port) ?? -1;
        if (outIndex < 0 || inIndex < 0) {
          continue;
        }
        const outputClass = `output_${outIndex + 1}`;
        const inputClass = `input_${inIndex + 1}`;
        editor.addConnection(srcId, tgtId, outputClass, inputClass);
        const key = `${edge.source.node}|${edge.source.port}|${edge.target.node}|${edge.target.port}`;
        // Programmatic addConnection may not fire connectionCreated, so colour
        // the restored wire directly.
        colorConnection(container, editor, palette, {
          output_id: srcId,
          input_id: tgtId,
          output_class: outputClass,
          input_class: inputClass,
        });
        wired.push({ srcId, tgtId, outputClass, inputClass, key });
      }
      injectWaypoints(wired, layout);
    },
    clear(): void {
      editor.clear();
      placed = 0;
    },
  };
}
