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
  progress_pct: number;
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

interface MountHelpers {
  api: {
    submit(parameters: Record<string, unknown>): Promise<SubmissionOutcomeResponse>;
    pollStatus(runId: string, options?: PollOptions): AsyncIterable<RunStatusResponse>;
  };
  escape(value: string): string;
  ui?: {
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

export async function mount(
  element: HTMLElement,
  configRaw: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> {
  const config = configRaw as EchoConfig;
  const state = buildFormUi(helpers, config.last_run);
  element.replaceChildren(state.root);

  const lastRun = config.last_run;
  if (lastRun?.outputs) {
    renderSuccess(state.resultEl, lastRun.outputs, lastRun.run, lastRun.messages ?? []);
    setStatus(state.statusEl, "Showing your last result — edit the value to run again.", "success");
  } else if (lastRun?.run && !isTerminalStatus(lastRun.run.status)) {
    state.submitBtn.disabled = true;
    void pollRun(lastRun.run, state);
  } else if (lastRun?.run && isTerminalStatus(lastRun.run.status)) {
    setStatus(state.statusEl, "Your last run did not complete. You can try again.", "danger");
  }
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

  const resultEl = document.createElement("div");
  resultEl.className = "mt-3";
  resultEl.hidden = true;

  form.append(label, input, submitBtn);
  root.append(form, statusEl, resultEl);

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void handleSubmit({ input, submitBtn, statusEl, resultEl, helpers });
  });

  return { root, input, submitBtn, statusEl, resultEl, helpers };
}

interface SubmitState {
  input: HTMLInputElement;
  submitBtn: HTMLButtonElement;
  statusEl: HTMLElement;
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
  setStatus(statusEl, "Submitting…");

  let outcome: SubmissionOutcomeResponse;
  try {
    outcome = await helpers.api.submit({ value });
  } catch (err) {
    // Out of credits / tier gate → the LMS's shared alert with a
    // top-up / upgrade CTA. Anything else falls back to status text.
    const alertEl = helpers.ui?.renderSubmissionError?.(err) ?? null;
    if (alertEl) {
      statusEl.replaceChildren(alertEl);
      submitBtn.disabled = false;
      return;
    }
    const msg = (err as ApiError).detail ?? String(err);
    setStatus(statusEl, `Submission failed: ${msg}`, "danger");
    submitBtn.disabled = false;
    return;
  }

  if (outcome.run === null) {
    setStatus(statusEl, "Run complete.", "success");
    submitBtn.disabled = false;
    return;
  }

  await pollRun(outcome.run, state);
}

async function pollRun(run: RunReference, state: SubmitState): Promise<void> {
  const { submitBtn, statusEl, resultEl, helpers } = state;
  setStatus(statusEl, activeStatusText(run.status));
  try {
    for await (const status of helpers.api.pollStatus(run.id, {
      intervalSeconds: run.poll_interval_seconds || 2,
      deadlineAt: run.deadline_at,
    })) {
      if (status.is_terminal) {
        renderTerminalResult(resultEl, status);
        if (status.status === "success") {
          const seconds = status.runtime_seconds?.toFixed(2);
          setStatus(
            statusEl,
            seconds ? `Echo complete (${seconds}s).` : "Echo complete.",
            "success",
          );
        } else {
          const learnerError = (status.messages ?? []).find(
            (message) => message.level === "error",
          );
          setStatus(
            statusEl,
            learnerError?.text ?? "We couldn't complete this simulation. Please try again.",
            "danger",
          );
        }
        break;
      }
      setStatus(statusEl, activeStatusText(status.status, status.progress_pct, status.progress_step));
    }
  } catch (err) {
    setStatus(statusEl, `We lost contact with this run: ${String(err)}`, "danger");
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

function activeStatusText(status: string, progressPct = 0, progressStep = ""): string {
  if (isTerminalStatus(status)) return "Loading the completed result…";
  if (status === "pending") return "Preparing your simulation…";
  if (status === "dispatched") {
    return "Starting the simulation environment… The first run can take a minute.";
  }
  const progress = progressPct > 0 ? ` (${progressPct}%)` : "";
  const step = progressStep ? ` — ${progressStep}` : "";
  return `Simulation is running${progress}${step}…`;
}

function isTerminalStatus(status: string): boolean {
  return [
    "success",
    "failed_simulation",
    "failed_runtime",
    "cancelled",
    "timed_out",
  ].includes(status);
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
