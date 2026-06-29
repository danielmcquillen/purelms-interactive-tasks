/**
 * Drawflow canvas wiring for the modelica_diagram task.
 *
 * Wraps a Drawflow editor behind a small handle: ``addComponent`` drops a typed
 * node (its input/output port counts come from the scenario palette), and
 * ``serialize`` exports the canvas to a ``purelms.diagram.v1`` graph via
 * {@link drawflowToDiagram}. The Drawflow-specific surface lives here so the
 * mount module stays about layout + the submit/poll lifecycle.
 */

import Drawflow from "drawflow";

import { buildPortMaps, drawflowToDiagram } from "./diagram";
import type { Diagram, DrawflowExport, Scenario } from "./types";

export interface CanvasHandle {
  /** Drop a node of ``type`` onto the canvas (no-op for unknown types). */
  addComponent(type: string): void;
  /** Export the current canvas as a diagram.v1 graph. */
  serialize(): Diagram;
  /** Remove every node. */
  clear(): void;
}

function esc(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function nodeHtml(label: string, inputs: string[], outputs: string[]): string {
  const ports: string[] = [];
  if (inputs.length > 0) {
    ports.push(`in: ${inputs.map(esc).join(", ")}`);
  }
  if (outputs.length > 0) {
    ports.push(`out: ${outputs.map(esc).join(", ")}`);
  }
  return (
    `<div class="mdl-node-title">${esc(label)}</div>` +
    `<div class="mdl-node-ports">${ports.join(" · ")}</div>`
  );
}

export function createCanvas(
  container: HTMLElement,
  scenario: Scenario,
): CanvasHandle {
  const editor = new Drawflow(container);
  editor.reflow = "fixed";
  editor.start();

  const maps = buildPortMaps(scenario);
  const labels = new Map(scenario.palette.map((entry) => [entry.type, entry.label]));
  let placed = 0;

  return {
    addComponent(type: string): void {
      const map = maps[type];
      if (!map) {
        return;
      }
      // Stagger placements so dropped nodes don't stack on one spot.
      const x = 40 + (placed % 3) * 170;
      const y = 30 + Math.floor(placed / 3) * 110;
      placed += 1;
      editor.addNode(
        type,
        map.inputs.length,
        map.outputs.length,
        x,
        y,
        `mdl-node mdl-${type}`,
        { type },
        nodeHtml(labels.get(type) ?? type, map.inputs, map.outputs),
        false,
      );
    },
    serialize(): Diagram {
      return drawflowToDiagram(editor.export() as DrawflowExport, scenario);
    },
    clear(): void {
      editor.clear();
      placed = 0;
    },
  };
}
