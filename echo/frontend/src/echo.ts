/**
 * Echo backend frontend bundle.
 *
 * Renders a one-input form, POSTs the value as a parameter via
 * `helpers.api.submit`, polls via `helpers.api.pollStatus` until
 * terminal, then renders a learner-facing result.
 *
 * Used as the LMS's integration-test fixture for the dispatcher
 * → bundle → API → run-status pipeline. Real backends follow the
 * same shape but with domain-specific forms and result UIs.
 *
 * The bundle owns its element completely after `mount()` returns —
 * the LMS-side dispatcher never re-enters this element.
 */

// Inline type definitions for the small dispatcher contract. Bundles vendor
// this shape so a task release remains self-contained.

import {
  prepareRunUi as prepareSharedRunUi,
  restoreLastRun,
  resumeRun,
} from "../../../_shared_frontend/run_lifecycle";
import type { RunUi } from "../../../_shared_frontend/run_lifecycle";

interface RunReference {
  id: string;
  status: string;
  status_url: string;
  poll_interval_seconds: number;
  websocket_url: string | null;
  deadline_at: string | null;
}

interface SubmissionOutcomeResponse {
  attempt: unknown;
  run: RunReference | null;
  is_complete: boolean;
}

interface RunStatusResponse {
  id: string;
  status: string;
  progress_pct: number | null;
  progress_step: string;
  is_terminal: boolean;
  completed_at: string | null;
  runtime_seconds: number | null;
  created?: string;
  dispatched_at?: string | null;
  started_at?: string | null;
  // Populated by the LMS from the parsed output envelope on
  // terminal SUCCESS. Empty `{}` / `[]` for in-flight runs.
  outputs: Record<string, unknown>;
  messages: Array<{ level: string; code: string; text: string }>;
}

interface ApiError extends Error {
  status: number;
  code: string;
  detail: string;
}

interface PollOptions {
  intervalSeconds?: number;
  signal?: AbortSignal;
  deadlineAt?: string | null;
}

interface ProgressBarController {
  readonly element: HTMLElement;
  update(pct: number | null, label?: string): void;
  indeterminate(label?: string): void;
  determinate(pct: number, label?: string): void;
  complete(label?: string): void;
  error(label?: string): void;
  remove(): void;
}

interface MountHelpers {
  api: {
    submit(parameters: Record<string, unknown>): Promise<SubmissionOutcomeResponse>;
    pollStatus(runId: string, options?: PollOptions): AsyncIterable<RunStatusResponse>;
  };
  escape(value: string): string;
  ui?: {
    createProgressBar?(): ProgressBarController;
    renderSubmissionError?(error: unknown): HTMLElement | null;
  };
  meta: { bundle: string; unitBlockId: number };
}

interface EchoLastRun {
  parameters?: Record<string, unknown>;
  run?: RunReference;
  outputs?: Record<string, unknown>;
  messages?: RunStatusResponse["messages"];
}

interface EchoConfig {
  last_run?: EchoLastRun;
}

/** Stable browser-host interface implemented by this bundle. */
export const mountContract = "purelms.interactive_mount.v1";

export async function mount(
  element: HTMLElement,
  configRaw: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> {
  const config = configRaw as EchoConfig;
  const state = buildFormUi(helpers, config.last_run);
  element.replaceChildren(state.root);

  restoreLastRun(config.last_run, {
    onCompleted(saved) {
      renderSuccess(
        state.resultEl,
        saved.outputs as Record<string, unknown>,
        saved.run,
        saved.messages ?? [],
      );
      setStatus(
        state.statusEl,
        "Showing your last result — edit the value to run again.",
        "success",
      );
    },
    onInFlight(run) {
      state.submitBtn.disabled = true;
      void pollRun(run, state, prepareRunUi(state));
    },
    onIncomplete() {
      setStatus(
        state.statusEl,
        "Your last run did not complete. You can try again.",
        "danger",
      );
    },
  });
}

function buildFormUi(
  helpers: MountHelpers,
  lastRun?: EchoLastRun,
): SubmitState & { root: HTMLElement } {
  const root = document.createElement("div");
  root.className = "purelms-echo-task";

  const form = document.createElement("form");
  form.noValidate = true;

  const label = document.createElement("label");
  label.className = "form-label";
  label.htmlFor = `echo-value-${helpers.meta.unitBlockId}`;
  label.textContent = "Value to echo";

  const input = document.createElement("input");
  input.id = `echo-value-${helpers.meta.unitBlockId}`;
  input.type = "text";
  input.className = "form-control";
  input.required = true;
  const restoredValue = lastRun?.parameters?.["value"];
  input.value = typeof restoredValue === "string" ? restoredValue : "hello";

  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn btn-primary mt-2";
  submitBtn.textContent = "Run echo";

  const statusEl = document.createElement("div");
  statusEl.className = "mt-3 small text-muted";
  statusEl.setAttribute("aria-live", "polite");

  const progressEl = document.createElement("div");
  progressEl.className = "purelms-task-progress-host";

  const resultEl = document.createElement("div");
  resultEl.className = "mt-3";
  resultEl.hidden = true;

  form.append(label, input, submitBtn);
  root.append(form, statusEl, progressEl, resultEl);

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void handleSubmit({ input, submitBtn, statusEl, progressEl, resultEl, helpers });
  });

  return { root, input, submitBtn, statusEl, progressEl, resultEl, helpers };
}

interface SubmitState {
  input: HTMLInputElement;
  submitBtn: HTMLButtonElement;
  statusEl: HTMLElement;
  progressEl: HTMLElement;
  resultEl: HTMLElement;
  helpers: MountHelpers;
}

async function handleSubmit(state: SubmitState): Promise<void> {
  const { input, submitBtn, statusEl, resultEl, helpers } = state;
  const value = input.value.trim();
  if (value === "") {
    statusEl.textContent = "Please enter a value.";
    return;
  }

  submitBtn.disabled = true;
  resultEl.replaceChildren();
  resultEl.hidden = true;
  const ui = prepareRunUi(state);
  ui.setStatus("Submitting…");
  ui.bar?.indeterminate("Submitting…");

  let outcome: SubmissionOutcomeResponse;
  try {
    outcome = await helpers.api.submit({ value });
  } catch (err) {
    // Out of credits / tier gate → the LMS's shared alert with a
    // top-up / upgrade CTA. Anything else falls back to status text.
    const alertEl = helpers.ui?.renderSubmissionError?.(err) ?? null;
    if (alertEl) {
      resultEl.replaceChildren(alertEl);
      resultEl.hidden = false;
      ui.bar?.error("Submission failed");
      submitBtn.disabled = false;
      return;
    }
    const msg = (err as ApiError).detail ?? String(err);
    const failureText = `Submission failed: ${msg}`;
    ui.setStatus(failureText);
    ui.bar?.error(failureText);
    submitBtn.disabled = false;
    return;
  }

  if (outcome.run === null) {
    ui.setStatus("Run complete.");
    ui.bar?.complete("Run complete");
    submitBtn.disabled = false;
    return;
  }

  await pollRun(outcome.run, state, ui);
}

function prepareRunUi(state: SubmitState): RunUi {
  return prepareSharedRunUi({
    statusEl: state.statusEl,
    progressEl: state.progressEl,
    createProgressBar: state.helpers.ui?.createProgressBar,
    setFallbackStatus: (text) => setStatus(state.statusEl, text),
    setFallbackVisible: (visible) => {
      state.statusEl.hidden = !visible;
    },
  });
}

async function pollRun(
  run: RunReference,
  state: SubmitState,
  ui: RunUi,
): Promise<void> {
  const { submitBtn, resultEl, helpers } = state;
  try {
    await resumeRun({
      run,
      pollStatus: (runId, options) => helpers.api.pollStatus(runId, options),
      ui,
      onTerminal(status) {
        renderTerminalResult(resultEl, status);
        if (status.status === "success") {
          const seconds = status.runtime_seconds?.toFixed(2);
          const completionText = seconds
            ? `Echo complete (${seconds}s).`
            : "Echo complete.";
          ui.setStatus(completionText);
          ui.bar?.complete(completionText);
        } else {
          const learnerError = (status.messages ?? []).find(
            (message) => message.level === "error",
          );
          const failureText =
            learnerError?.text ??
            "We couldn't complete this simulation. Please try again.";
          ui.setStatus(failureText);
          ui.bar?.error(failureText);
        }
      },
      onProgress(status, label) {
        ui.setStatus(label);
        ui.bar?.update(status.progress_pct, status.progress_step || label);
      },
      onPollingError(message) {
        ui.setStatus(message);
        ui.bar?.error(message);
      },
    });
  } finally {
    submitBtn.disabled = false;
  }
}

function renderTerminalResult(resultEl: HTMLElement, status: RunStatusResponse): void {
  if (status.status === "success") {
    renderSuccess(resultEl, status.outputs ?? {}, {
      id: status.id,
      status: status.status,
      status_url: "",
      poll_interval_seconds: 2,
      websocket_url: null,
      deadline_at: null,
    }, status.messages ?? [], status.runtime_seconds);
    return;
  }
  resultEl.replaceChildren();
  resultEl.hidden = true;
}

function renderSuccess(
  resultEl: HTMLElement,
  outputs: Record<string, unknown>,
  run: RunReference | undefined,
  messages: RunStatusResponse["messages"],
  runtimeSeconds: number | null = null,
): void {
  const card = document.createElement("div");
  card.className = "alert alert-success mb-0";

  const heading = document.createElement("div");
  heading.className = "fw-semibold";
  heading.textContent = "Backend response";

  const value = document.createElement("div");
  value.className = "mt-1 fs-5";
  const echoed = outputs["echoed_parameters"];
  const echoedValue =
    typeof echoed === "object" && echoed !== null && "value" in echoed
      ? (echoed as { value: unknown }).value
      : echoed;
  value.textContent = echoedValue === undefined ? "No value returned." : String(echoedValue);
  card.append(heading, value);

  for (const message of messages) {
    if (message.level === "debug") continue;
    const note = document.createElement("div");
    note.className = "small mt-2";
    note.textContent = message.text;
    card.append(note);
  }

  if (run?.id || runtimeSeconds !== null) {
    const details = document.createElement("div");
    details.className = "small text-muted mt-2";
    const parts = [run?.id ? `Run ${run.id}` : ""];
    if (runtimeSeconds !== null) parts.push(`${runtimeSeconds.toFixed(2)}s runtime`);
    details.textContent = parts.filter(Boolean).join(" · ");
    card.append(details);
  }

  resultEl.replaceChildren(card);
  resultEl.hidden = false;
}

function setStatus(
  statusEl: HTMLElement,
  text: string,
  tone: "muted" | "success" | "danger" = "muted",
): void {
  statusEl.className = `mt-3 small text-${tone}`;
  statusEl.textContent = text;
}

export default mount;
