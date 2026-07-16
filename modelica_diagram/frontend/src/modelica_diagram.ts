/**
 * Modelica FMU diagram InteractiveTask — frontend bundle.
 *
 * Exports ``mount(element, config, helpers)``; the LMS dispatcher
 * dynamic-imports the built bundle and calls it once per placement.
 *
 *   ┌────────────────────┬──────────────────────────────┐
 *   │ palette (add parts)│  Drawflow canvas              │
 *   │ parameter sliders  │  (drag output → input ports)  │
 *   │ Run / Clear        │                               │
 *   │ status + results   │                               │
 *   └────────────────────┴──────────────────────────────┘
 *
 * On Run, the canvas is serialised to a ``purelms.diagram.v1`` graph and sent
 * as the ``diagram_json`` parameter; the backend topology-checks it (and, once
 * the FMU ships, simulates). A wrong diagram comes back as a successful run
 * with ``topology_correct=false`` plus per-discrepancy messages.
 */

import { createCanvas } from "./canvas";
import type { CanvasHandle } from "./canvas";
import { injectStyles } from "./styles";
import type {
  Diagram,
  Layout,
  ModelicaConfig,
  ModelicaLastRun,
  ModelicaOutputs,
  MountFn,
  MountHelpers,
  Scenario,
  SimulationRunMessage,
  SimulationRunStatusResponse,
} from "./types";
import scenarioJson from "./vendor/hydronic_loop.scenario.json";

const SCENARIO = scenarioJson as unknown as Scenario;

// Mirrors the manifest's numeric parameters (bounds + defaults).
const PARAMS = {
  boiler_nominal_power_kw: { label: "Boiler power", unit: "kW", def: 10, min: 2, max: 20, step: 1 },
  room_setpoint_c: { label: "Target room temp", unit: "°C", def: 21, min: 16, max: 26, step: 1 },
  heat_loss_w_per_k: { label: "Heat loss", unit: "W/K", def: 150, min: 50, max: 400, step: 10 },
  outdoor_temp_c: { label: "Outdoor temp", unit: "°C", def: 0, min: -15, max: 15, step: 1 },
} as const;
type ParamKey = keyof typeof PARAMS;

// ---------------------------------------------------------------------
// mount()
// ---------------------------------------------------------------------

export const mount: MountFn = async (element, configRaw, helpers): Promise<void> => {
  const config = configRaw as ModelicaConfig;
  injectStyles(element.ownerDocument);

  const root = el("div", "mdl-task");
  const sidebar = el("div", "mdl-sidebar");
  const stage = el("div", "mdl-stage");
  root.append(sidebar, stage);
  element.replaceChildren(root);

  if (helpers.meta.backendAvailable === false) {
    sidebar.append(
      notice(
        "This InteractiveTask is no longer available. Your instructor may have " +
          "deactivated it; check back later.",
      ),
    );
    return;
  }

  // ---- Canvas (right) ----
  const hint = el("div", "mdl-hint");
  hint.textContent =
    "Click a component to add it, then drag from an output port (right) to an " +
    "input port (left). Double-click a wire to add a point you can drag.";
  const canvasEl = el("div", "mdl-canvas");
  stage.append(hint, canvasEl);

  let canvas: CanvasHandle | null = null;
  try {
    canvas = createCanvas(canvasEl, SCENARIO);
  } catch {
    stage.append(notice("The diagram canvas failed to load in this browser."));
  }

  // ---- Palette (left) ----
  const palette = el("div", "mdl-palette");
  for (const entry of SCENARIO.palette) {
    const button = el("button");
    button.type = "button";
    button.textContent = `+ ${entry.label}`;
    button.addEventListener("click", () => canvas?.addComponent(entry.type));
    palette.append(button);
  }

  // ---- Parameter sliders ----
  const paramInputs = buildParams(config, helpers.meta.unitBlockId);

  // ---- Toolbar + status + results ----
  const toolbar = el("div", "mdl-toolbar");
  const runBtn = el("button", "mdl-run");
  runBtn.type = "button";
  runBtn.textContent = "Run simulation";
  const clearBtn = el("button", "mdl-secondary");
  clearBtn.type = "button";
  clearBtn.textContent = "Start over";
  toolbar.append(runBtn, clearBtn);

  const statusEl = el("div", "mdl-status");
  statusEl.setAttribute("aria-live", "polite");
  const resultsEl = el("div", "mdl-results");

  sidebar.append(
    palette,
    ...paramInputs.rows,
    toolbar,
    statusEl,
    resultsEl,
  );

  clearBtn.addEventListener("click", () => {
    canvas?.clear();
    paramInputs.reset();
    resultsEl.replaceChildren();
    statusEl.textContent = "";
  });
  runBtn.addEventListener("click", () => {
    void handleSubmit({ canvas, paramInputs, runBtn, statusEl, resultsEl, helpers });
  });

  // Restore the learner's last attempt (diagram + last result). The sliders are
  // already seeded from config.last_run inside buildParams.
  restoreLastRun(config.last_run, canvas, statusEl, resultsEl);
};

export default mount;

// ---------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------

interface ParamInputs {
  rows: HTMLElement[];
  inputs: Map<ParamKey, HTMLInputElement>;
  /** Reset every slider to its default (author override or spec default). */
  reset(): void;
}

function buildParams(config: ModelicaConfig, unitBlockId: number): ParamInputs {
  const rows: HTMLElement[] = [];
  const inputs = new Map<ParamKey, HTMLInputElement>();
  const resets: Array<() => void> = [];
  const restored = config.last_run?.parameters ?? {};

  for (const key of Object.keys(PARAMS) as ParamKey[]) {
    const spec = PARAMS[key];
    const override = config.parameters?.[key];
    const min = override?.min ?? spec.min;
    const max = override?.max ?? spec.max;
    const step = override?.step ?? spec.step;
    const fallback = override?.default ?? spec.def;
    const prior = restored[key];
    const value = typeof prior === "number" ? prior : fallback;

    const row = el("div", "mdl-field");
    const label = el("label");
    const inputId = `mdl-${unitBlockId}-${key}`;
    label.htmlFor = inputId;
    const labelText = el("span");
    labelText.textContent = spec.label;
    const valueText = el("span", "mdl-val");
    label.append(labelText, valueText);

    const input = el("input");
    input.id = inputId;
    input.type = "range";
    input.min = String(min);
    input.max = String(max);
    input.step = String(step);
    if (override?.enabled === false) {
      input.disabled = true;
    }
    const sync = (): void => {
      valueText.textContent = `${input.value} ${spec.unit}`;
    };
    input.addEventListener("input", sync);
    const apply = (next: number): void => {
      input.value = String(next);
      sync();
    };
    apply(value);
    resets.push(() => apply(fallback));

    row.append(label, input);
    if (override?.visible === false) {
      row.style.display = "none";
    }
    rows.push(row);
    inputs.set(key, input);
  }

  return {
    rows,
    inputs,
    reset: () => {
      for (const r of resets) {
        r();
      }
    },
  };
}

function readParams(paramInputs: ParamInputs): Record<ParamKey, number> {
  const out = {} as Record<ParamKey, number>;
  for (const [key, input] of paramInputs.inputs) {
    out[key] = Number(input.value);
  }
  return out;
}

// ---------------------------------------------------------------------
// Submit → poll → render
// ---------------------------------------------------------------------

interface SubmitArgs {
  canvas: CanvasHandle | null;
  paramInputs: ParamInputs;
  runBtn: HTMLButtonElement;
  statusEl: HTMLElement;
  resultsEl: HTMLElement;
  helpers: MountHelpers;
}

async function handleSubmit(args: SubmitArgs): Promise<void> {
  const { canvas, paramInputs, runBtn, statusEl, resultsEl, helpers } = args;
  if (!canvas) {
    return;
  }
  const setStatus = (text: string): void => {
    statusEl.textContent = text;
  };

  const diagram = canvas.serialize();
  if (diagram.nodes.length === 0) {
    setStatus("Add some components and connect them before running.");
    return;
  }

  runBtn.disabled = true;
  resultsEl.replaceChildren();
  setStatus("Submitting…");

  const parameters = {
    scenario: SCENARIO.id,
    diagram_json: JSON.stringify(diagram),
    layout_json: JSON.stringify(canvas.serializeLayout()),
    ...readParams(paramInputs),
  };

  let outcome;
  try {
    outcome = await helpers.api.submit(parameters);
  } catch (err) {
    const alertEl = helpers.ui?.renderSubmissionError?.(err) ?? null;
    if (alertEl) {
      resultsEl.replaceChildren(alertEl);
    } else {
      setStatus(`Submission failed: ${humanize(err)}`);
    }
    runBtn.disabled = false;
    return;
  }

  if (!outcome.run) {
    setStatus("Run complete.");
    runBtn.disabled = false;
    return;
  }

  setStatus("Running…");
  try {
    for await (const status of helpers.api.pollStatus(outcome.run.id, {
      intervalSeconds: outcome.run.poll_interval_seconds || 1,
      deadlineAt: outcome.run.deadline_at,
    })) {
      if (status.is_terminal) {
        renderResult(status, resultsEl, setStatus);
        break;
      }
    }
  } catch (err) {
    setStatus(`Polling failed: ${humanize(err)}`);
  } finally {
    runBtn.disabled = false;
  }
}

function renderResult(
  status: SimulationRunStatusResponse,
  resultsEl: HTMLElement,
  setStatus: (text: string) => void,
): void {
  if (status.status !== "success") {
    setStatus(`Run failed: ${status.status}.`);
    resultsEl.append(messageList(status.messages ?? [], true));
    return;
  }
  setStatus(`Done (${(status.runtime_seconds ?? 0).toFixed(2)}s).`);
  renderSuccessOutputs(
    status.outputs as Partial<ModelicaOutputs>,
    status.messages ?? [],
    resultsEl,
  );
}

/** Render the verdict + notes + result cards + chart for a successful run.
 * Shared by the live-run path and the restore path. */
function renderSuccessOutputs(
  outputs: Partial<ModelicaOutputs>,
  messages: SimulationRunMessage[],
  resultsEl: HTMLElement,
): void {
  const verdict = el("div", `mdl-verdict ${outputs.topology_correct ? "ok" : "no"}`);
  verdict.textContent = outputs.topology_correct
    ? "Your diagram matches the system."
    : "Not quite — check the notes below.";
  resultsEl.append(verdict, messageList(messages, false));

  if (outputs.topology_correct) {
    const cards = el("div", "mdl-cards");
    cards.append(
      valueCard("Final room temp", outputs.room_temp_final_c, "°C"),
      valueCard("Heat delivered", outputs.energy_used_kwh, "kWh"),
      reachCard(outputs.time_to_setpoint_min),
    );
    resultsEl.append(cards);
    if (outputs.series_json) {
      const chart = sparkline(outputs.series_json);
      if (chart) {
        resultsEl.append(chart);
      }
    }
  }
}

/** Restore the learner's last attempt: rebuild the canvas from the stored
 * diagram and re-render the last result. Sliders are restored in buildParams. */
function restoreLastRun(
  lastRun: ModelicaLastRun | undefined,
  canvas: CanvasHandle | null,
  statusEl: HTMLElement,
  resultsEl: HTMLElement,
): void {
  if (!lastRun) {
    return;
  }
  const diagramJson = lastRun.parameters?.["diagram_json"];
  const layoutJson = lastRun.parameters?.["layout_json"];
  let restoredDiagram = false;
  if (canvas && typeof diagramJson === "string") {
    try {
      const diagram = JSON.parse(diagramJson) as Diagram;
      const layout =
        typeof layoutJson === "string"
          ? (JSON.parse(layoutJson) as Layout)
          : undefined;
      if (Array.isArray(diagram.nodes) && diagram.nodes.length > 0) {
        canvas.restore(diagram, layout);
        restoredDiagram = true;
      }
    } catch {
      // Malformed stored diagram/layout — leave the canvas empty.
    }
  }
  if (lastRun.outputs) {
    renderSuccessOutputs(lastRun.outputs, lastRun.messages ?? [], resultsEl);
    statusEl.textContent = "Showing your last result — adjust and run again.";
  } else if (restoredDiagram) {
    statusEl.textContent = "Restored your last diagram — run it again to simulate.";
  }
}

function messageList(
  messages: SimulationRunMessage[],
  errorsOnly: boolean,
): HTMLElement {
  const list = el("ul", "mdl-msgs");
  for (const msg of messages) {
    if (msg.level === "debug") {
      continue;
    }
    if (errorsOnly && msg.level !== "error") {
      continue;
    }
    const item = el("li");
    item.textContent = msg.text;
    list.append(item);
  }
  return list;
}

function valueCard(label: string, value: number | undefined, unit: string): HTMLElement {
  const card = el("div", "mdl-card");
  const k = el("div", "k");
  k.textContent = label;
  const v = el("div", "v");
  v.textContent =
    typeof value === "number"
      ? `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`
      : "—";
  card.append(k, v);
  return card;
}

/** Like valueCard, but renders the simulation length as "didn't reach": an
 * undersized boiler never gets to the setpoint, so tReach_min comes back at the
 * cap (the run length). */
function reachCard(minutes: number | undefined): HTMLElement {
  const card = el("div", "mdl-card");
  const k = el("div", "k");
  k.textContent = "Time to reach setpoint";
  const v = el("div", "v");
  if (typeof minutes !== "number") {
    v.textContent = "—";
  } else if (minutes >= 179) {
    v.textContent = "didn’t reach";
  } else {
    v.textContent = `${minutes.toLocaleString(undefined, { maximumFractionDigits: 0 })} min`;
  }
  card.append(k, v);
  return card;
}

/** Minimal inline-SVG line chart of the ``[[t, v], …]`` series. */
function sparkline(seriesJson: string): SVGSVGElement | null {
  let series: Array<[number, number]>;
  try {
    series = JSON.parse(seriesJson) as Array<[number, number]>;
  } catch {
    return null;
  }
  if (!Array.isArray(series) || series.length < 2) {
    return null;
  }

  const W = 320;
  const H = 120;
  const pad = 6;
  const ts = series.map((p) => p[0]);
  const vs = series.map((p) => p[1]);
  const [tMin, tMax] = [Math.min(...ts), Math.max(...ts)];
  const [vMin, vMax] = [Math.min(...vs), Math.max(...vs)];
  const sx = (t: number): number =>
    tMax === tMin ? pad : pad + ((t - tMin) / (tMax - tMin)) * (W - 2 * pad);
  const sy = (v: number): number =>
    vMax === vMin ? H / 2 : H - pad - ((v - vMin) / (vMax - vMin)) * (H - 2 * pad);

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "mdl-chart");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const path = document.createElementNS(ns, "polyline");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#2563eb");
  path.setAttribute("stroke-width", "2");
  path.setAttribute("points", series.map((p) => `${sx(p[0])},${sy(p[1])}`).join(" "));
  svg.append(path);
  return svg;
}

// ---------------------------------------------------------------------
// Small DOM helpers
// ---------------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  return node;
}

function notice(text: string): HTMLElement {
  const box = el("div", "mdl-notice");
  box.textContent = text;
  return box;
}

function humanize(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === "object" && err !== null && "detail" in err) {
    return String((err as { detail: unknown }).detail);
  }
  return String(err);
}
