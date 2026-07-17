/**
 * Build-time helper for the common InteractiveTask run lifecycle.
 *
 * This module is imported into each task's ES-module build, not loaded from
 * PureLMS at runtime. Every released task bundle therefore remains
 * self-contained while shared restore, polling, progress, and safe-failure
 * behavior stay consistent.
 */

export interface RunReference {
  id: string;
  status: string;
  status_url: string;
  poll_interval_seconds: number;
  websocket_url: string | null;
  deadline_at: string | null;
}

export interface RunStatus {
  status: string;
  is_terminal: boolean;
  progress_pct: number | null;
  progress_step: string;
}

export interface ProgressBarController {
  readonly element: HTMLElement;
  update(pct: number | null, label?: string): void;
  indeterminate(label?: string): void;
  determinate(pct: number, label?: string): void;
  complete(label?: string): void;
  error(label?: string): void;
  remove(): void;
}

export interface RunUi {
  bar: ProgressBarController | null;
  setStatus(text: string): void;
}

export interface RestorableLastRun {
  run?: RunReference;
  outputs?: unknown;
}

/** Return whether one LMS status is terminal under mount-contract v1. */
export function isTerminalRun(status: string): boolean {
  return [
    "success",
    "failed_simulation",
    "failed_runtime",
    "cancelled",
    "timed_out",
  ].includes(status);
}

/** Render accurate non-terminal copy without pretending provisioning has progress. */
export function activeRunStatusText(
  status: string,
  progressPct: number | null = null,
  progressStep = "",
): string {
  if (isTerminalRun(status)) return "Loading the completed result…";
  if (status === "pending") return "Preparing your simulation…";
  if (status === "dispatched") {
    return "Starting the simulation environment… The first run can take a minute.";
  }
  const progress = typeof progressPct === "number" && progressPct > 0
    ? ` (${progressPct}%)`
    : "";
  const step = progressStep ? ` — ${progressStep}` : "";
  return `Simulation is running${progress}${step}…`;
}

/** Do not expose transport exceptions or provider details to learners. */
export function safeRunFailureText(): string {
  return "We lost contact with this simulation. Please try again.";
}

/**
 * Attach LMS-owned progress chrome while leaving each task in charge of layout
 * and fallback styling.
 */
export function prepareRunUi(options: {
  statusEl: HTMLElement;
  progressEl: HTMLElement;
  createProgressBar: (() => ProgressBarController) | undefined;
  setFallbackStatus(text: string): void;
  setFallbackVisible(visible: boolean): void;
}): RunUi {
  const bar = options.createProgressBar?.() ?? null;
  if (bar) {
    options.statusEl.textContent = "";
    options.setFallbackVisible(false);
    options.progressEl.replaceChildren(bar.element);
  } else {
    options.setFallbackVisible(true);
  }
  return {
    bar,
    setStatus(text: string): void {
      if (!bar) options.setFallbackStatus(text);
    },
  };
}

/**
 * Restore a task-specific last result or resume an in-flight run.
 *
 * Task bundles retain their own result presentation; this helper owns only the
 * mutually exclusive lifecycle choice so every bundle handles reloads alike.
 */
export function restoreLastRun<T extends RestorableLastRun>(
  lastRun: T | undefined,
  callbacks: {
    onCompleted(saved: T): void;
    onInFlight(run: RunReference): void;
    onIncomplete(run: RunReference): void;
  },
): void {
  if (!lastRun) return;
  if (lastRun.outputs !== undefined) {
    callbacks.onCompleted(lastRun);
    return;
  }
  if (!lastRun.run) return;
  if (isTerminalRun(lastRun.run.status)) {
    callbacks.onIncomplete(lastRun.run);
    return;
  }
  callbacks.onInFlight(lastRun.run);
}

/**
 * Resume polling with consistent copy, progress handling, and safe transport
 * failure semantics. Terminal rendering remains task-specific.
 */
export async function resumeRun<TStatus extends RunStatus>(options: {
  run: RunReference;
  pollStatus(
    runId: string,
    options: { intervalSeconds: number; deadlineAt: string | null },
  ): AsyncIterable<TStatus>;
  ui: RunUi;
  onTerminal(status: TStatus): void;
  onProgress(status: TStatus, label: string): void;
  onPollingError(message: string): void;
}): Promise<void> {
  const { run, ui } = options;
  const initialLabel = activeRunStatusText(run.status);
  ui.setStatus(initialLabel);
  ui.bar?.update(null, initialLabel);
  try {
    for await (const status of options.pollStatus(run.id, {
      intervalSeconds: run.poll_interval_seconds || 2,
      deadlineAt: run.deadline_at,
    })) {
      if (status.is_terminal) {
        options.onTerminal(status);
        return;
      }
      const label = activeRunStatusText(
        status.status,
        status.progress_pct,
        status.progress_step,
      );
      options.onProgress(status, label);
    }
  } catch {
    options.onPollingError(safeRunFailureText());
  }
}
