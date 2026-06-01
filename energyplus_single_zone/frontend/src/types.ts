/**
 * Vendored types from the LMS dispatcher contract
 * (``purelms/static/src/ts/sims/contract.ts``).
 *
 * Bundles SHOULD NOT import from the LMS — they vendor
 * the contract types. Structural typing makes the runtime contract
 * the only thing that has to match. When the contract evolves (v2
 * envelope additions, for example), this file gets updated alongside
 * a major-version bump of the InteractiveTask.
 */

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

/**
 * One entry from the backend's ``output_envelope.messages`` list —
 * debug / info / warning / error notice surfaced to the learner.
 * Levels mirror ``purelms_shared.constants.MessageLevel``.
 */
export interface SimulationRunMessage {
  level: "debug" | "info" | "warning" | "error";
  code: string;
  text: string;
}

export interface SimulationRunStatusResponse {
  id: string;
  /** Mirrors ``purelms_shared.constants.RunStatus``. Kept as
   * ``string`` (rather than a union) for forward-compat: a server
   * adding a new status value shouldn't break the bundle compile. */
  status: string;
  progress_pct: number;
  progress_step: string;
  is_terminal: boolean;
  completed_at: string | null;
  runtime_seconds: number | null;
  /** Backend's result-value dict (keys match
   * ``interactive_task.yaml``'s ``outputs[].name``). Populated by
   * the LMS from the parsed output envelope. Empty `{}` for in-
   * flight runs and FAILED_RUNTIME envelope-less paths. */
  outputs: Record<string, unknown>;
  /** Info/warning/error entries the backend emitted. Empty `[]`
   * until a terminal envelope arrives. */
  messages: SimulationRunMessage[];
}

export interface PollHelperOptions {
  intervalSeconds?: number;
  maxAttempts?: number;
  signal?: AbortSignal;
}

/**
 * Shared Bootstrap progress bar handed to the bundle via
 * ``helpers.ui.createProgressBar()``. The LMS owns the markup +
 * styling; the bundle owns placement (insert ``element`` once) and
 * driving (call the mode methods as the run advances).
 */
export interface ProgressBarController {
  readonly element: HTMLElement;
  /** Animated striped full-width bar — work in progress, no measurable %. */
  indeterminate(label?: string): void;
  /** Filled bar at `pct` (0–100) with an optional label. */
  determinate(pct: number, label?: string): void;
  /** Solid success bar — terminal success. */
  complete(label?: string): void;
  /** Solid danger bar — terminal failure. */
  error(label?: string): void;
  /** Remove the bar from the DOM. */
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
  /**
   * Shared UI components the LMS provides. Optional in the vendored
   * type because a bundle may be loaded by an older LMS dispatcher
   * that predates ``ui`` — the bundle guards at runtime and falls
   * back to a text status line.
   */
  ui?: {
    createProgressBar(): ProgressBarController;
    /**
     * For a known learner-actionable rejection (`insufficient_credits`,
     * `tier_gate`) returns a polished Bootstrap alert with a top-up /
     * upgrade CTA; `null` otherwise. Optional in the vendored type so a
     * bundle loaded by an older dispatcher guards at runtime.
     */
    renderSubmissionError?(error: unknown): HTMLElement | null;
  };
  meta: {
    bundle: string;
    unitBlockId: number;
    creditCost: number | null;
    backendAvailable: boolean | null;
    /**
     * Whether the backend declares it emits intermediate progress
     * (manifest ``frontend.reports_progress``). Optional for
     * backward-compat with older dispatchers; treat absent as false.
     */
    reportsProgress?: boolean;
  };
}

export type MountFn = (
  element: HTMLElement,
  config: Record<string, unknown>,
  helpers: MountHelpers,
) => void | Promise<void>;

// -----------------------------------------------------------------
// EnergyPlus-specific Layer 2 config shape (mirrors the manifest's
// parameters[] block — what the LMS injects per-block).
// -----------------------------------------------------------------

export interface NumberParamConfig {
  visible?: boolean;
  enabled?: boolean;
  default?: number;
  min?: number;
  max?: number;
  step?: number;
}

export interface EnumChoice {
  value: string;
  label: string;
}

export interface EnumParamConfig {
  visible?: boolean;
  enabled?: boolean;
  default?: string;
  choices?: EnumChoice[];
}

/**
 * Layer 2 config shape the LMS injects as the ``config`` arg to
 * ``mount()``. Each entry mirrors a manifest parameter; the bundle
 * applies these as overrides on top of the manifest defaults.
 *
 * v1 LMS doesn't yet populate this fully — the bundle should treat
 * missing entries as "use manifest defaults."
 */
export interface EnergyPlusConfig {
  parameters?: {
    glazing_u_value?: NumberParamConfig;
    window_area?: NumberParamConfig;
    climate_zone?: EnumParamConfig;
  };
}

/** Strong-typed view of the terminal-status outputs. */
export interface EnergyPlusOutputs {
  annual_heating_kWh: number;
  annual_cooling_kWh: number;
  peak_heating_kW: number;
  notes: string;
}
