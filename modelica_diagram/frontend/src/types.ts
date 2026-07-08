/**
 * Types for the modelica_diagram frontend.
 *
 * Three groups:
 *  1. The LMS dispatcher contract (vendored from
 *     ``purelms/static/src/ts/sims/contract.ts`` — bundles vendor these,
 *     never import from the LMS).
 *  2. The scenario shape (mirrors ``scenario.json``, the single source the
 *     backend grades against and the frontend vendors).
 *  3. The ``purelms.diagram.v1`` graph + the Drawflow export shape, so the
 *     canvas can be serialised into the contract the backend validates.
 */

// ---------------------------------------------------------------------
// 1. Dispatcher contract (vendored)
// ---------------------------------------------------------------------

export interface RunReference {
  id: string;
  status: string;
  status_url: string;
  poll_interval_seconds: number;
}

export interface SubmissionOutcomeResponse {
  attempt: unknown;
  run: RunReference | null;
  is_complete: boolean;
}

export interface SimulationRunMessage {
  level: "debug" | "info" | "warning" | "error";
  code: string;
  text: string;
}

export interface SimulationRunStatusResponse {
  id: string;
  status: string;
  progress_pct: number;
  progress_step: string;
  is_terminal: boolean;
  completed_at: string | null;
  runtime_seconds: number | null;
  outputs: Record<string, unknown>;
  messages: SimulationRunMessage[];
}

export interface PollHelperOptions {
  intervalSeconds?: number;
  maxAttempts?: number;
  signal?: AbortSignal;
}

export interface ProgressBarController {
  readonly element: HTMLElement;
  indeterminate(label?: string): void;
  determinate(pct: number, label?: string): void;
  complete(label?: string): void;
  error(label?: string): void;
  remove(): void;
}

export interface MountHelpers {
  api: {
    submit(parameters: Record<string, unknown>): Promise<SubmissionOutcomeResponse>;
    pollStatus(
      runId: string,
      options?: PollHelperOptions,
    ): AsyncIterable<SimulationRunStatusResponse>;
  };
  escape(value: string): string;
  ui?: {
    createProgressBar(): ProgressBarController;
    renderSubmissionError?(error: unknown): HTMLElement | null;
  };
  meta: {
    bundle: string;
    unitBlockId: number;
    creditCost: number | null;
    backendAvailable: boolean | null;
    reportsProgress?: boolean;
  };
}

export type MountFn = (
  element: HTMLElement,
  config: Record<string, unknown>,
  helpers: MountHelpers,
) => void | Promise<void>;

// Layer-2 parameter overrides the LMS injects as ``config``.
export interface NumberParamConfig {
  visible?: boolean;
  enabled?: boolean;
  default?: number;
  min?: number;
  max?: number;
  step?: number;
}

/** The learner's most recent run for this placement, injected by the LMS into
 * the bundle config so the canvas + sliders + last result can be restored on
 * return (instead of starting from an empty canvas). */
export interface ModelicaLastRun {
  /** Submitted parameters — includes ``diagram_json`` plus the slider values. */
  parameters?: Record<string, unknown>;
  /** Result outputs; present only when the run succeeded. */
  outputs?: ModelicaOutputs;
  /** Backend messages from that run. */
  messages?: SimulationRunMessage[];
}

export interface ModelicaConfig {
  parameters?: {
    boiler_nominal_power_kw?: NumberParamConfig;
    room_setpoint_c?: NumberParamConfig;
    heat_loss_w_per_k?: NumberParamConfig;
    outdoor_temp_c?: NumberParamConfig;
  };
  last_run?: ModelicaLastRun;
}

/** Strong-typed view of the backend's terminal outputs. */
export interface ModelicaOutputs {
  topology_correct: boolean;
  room_temp_final_c?: number;
  energy_used_kwh?: number;
  time_to_setpoint_min?: number;
  series_json?: string;
}

// ---------------------------------------------------------------------
// 2. Scenario (mirrors scenario.json)
// ---------------------------------------------------------------------

export type PortKind = "fluid" | "heat" | "signal";
export type UiSide = "input" | "output";

export interface ScenarioPort {
  name: string;
  kind: PortKind;
  /** Causal direction for ``signal`` ports. Absent for acausal ``fluid``. */
  direction?: "in" | "out";
  /** Which side of the node the port renders on in the canvas. */
  ui_side?: UiSide;
  /** Display text for the port; falls back to ``name`` when absent. */
  label?: string;
}

export interface PaletteEntry {
  type: string;
  label: string;
  ports: ScenarioPort[];
}

export interface Scenario {
  id: string;
  label: string;
  description: string;
  palette: PaletteEntry[];
  expected: { nodes: string[]; edges: unknown[] };
  parameter_map: Record<string, string>;
  outputs: Record<string, { fmu_variable: string; summarize?: string; unit?: string }>;
}

// ---------------------------------------------------------------------
// 3. purelms.diagram.v1 + Drawflow export
// ---------------------------------------------------------------------

export interface DiagramEndpoint {
  node: string;
  port: string;
}

export interface DiagramEdge {
  source: DiagramEndpoint;
  target: DiagramEndpoint;
}

export interface DiagramNode {
  id: string;
  type: string;
}

export interface Diagram {
  schema: "purelms.diagram.v1";
  nodes: DiagramNode[];
  edges: DiagramEdge[];
}

/**
 * Neutral, library-agnostic canvas layout — node positions + edge reroute
 * waypoints in canvas pixel coordinates. Stored ALONGSIDE (never inside) the
 * semantic ``diagram.v1`` graph, whose schema is deliberately closed and
 * semantic-only. Positions are keyed by diagram node id; waypoints by
 * ``src|srcPort|tgt|tgtPort``. The backend ignores it; only the canvas adapter
 * reads it, so the stored data stays free of any Drawflow-specific shape.
 */
export interface Layout {
  positions: Record<string, { x: number; y: number }>;
  waypoints: Record<string, Array<{ x: number; y: number }>>;
}

/** A single connection recorded on a Drawflow OUTPUT port. ``output``
 * (Drawflow's confusing field name) is the TARGET node's input class,
 * e.g. ``"input_1"``. */
export interface DrawflowOutputConnection {
  node: string;
  output: string;
  /** Reroute waypoints Drawflow stores on the connection (its own format). */
  points?: Array<{ pos_x: number; pos_y: number }>;
}

export interface DrawflowNode {
  id: number;
  name: string;
  data: { type: string } & Record<string, unknown>;
  inputs: Record<string, { connections: unknown[] }>;
  outputs: Record<string, { connections: DrawflowOutputConnection[] }>;
  pos_x: number;
  pos_y: number;
}

export interface DrawflowExport {
  drawflow: { Home: { data: Record<string, DrawflowNode> } };
}
