/**
 * Echo backend frontend bundle.
 *
 * Renders a one-input form, POSTs the value as a parameter via
 * `helpers.api.submit`, polls via `helpers.api.pollStatus` until
 * terminal, then renders the run's terminal status + a "Run again"
 * affordance.
 *
 * Used as the LMS's integration-test fixture for the dispatcher
 * → bundle → API → run-status pipeline. Real backends follow the
 * same shape but with domain-specific forms and result UIs.
 *
 * The bundle owns its element completely after `mount()` returns —
 * the LMS-side dispatcher never re-enters this element.
 */

// Inline type definitions for the dispatcher contract. At v1 we
// vendor these rather than publishing a shared @purelms/types
// package — when a second backend exists we'll extract.

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

export async function mount(
  element: HTMLElement,
  _config: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> {
  element.replaceChildren(buildFormUi(helpers));
}

function buildFormUi(helpers: MountHelpers): HTMLElement {
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
  input.value = "hello";

  const submitBtn = document.createElement("button");
  submitBtn.type = "submit";
  submitBtn.className = "btn btn-primary mt-2";
  submitBtn.textContent = "Run echo";

  const statusEl = document.createElement("div");
  statusEl.className = "mt-3 small text-muted";
  statusEl.setAttribute("aria-live", "polite");

  const resultEl = document.createElement("pre");
  resultEl.className = "mt-3 mb-0 bg-light p-2 small";
  resultEl.hidden = true;

  form.append(label, input, submitBtn);
  root.append(form, statusEl, resultEl);

  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    void handleSubmit({ input, submitBtn, statusEl, resultEl, helpers });
  });

  return root;
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
  resultEl.hidden = true;
  statusEl.textContent = "Submitting…";

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
    statusEl.textContent = `Submission failed: ${msg}`;
    submitBtn.disabled = false;
    return;
  }

  if (outcome.run === null) {
    statusEl.textContent = "Run complete (synchronous).";
    submitBtn.disabled = false;
    return;
  }

  const run = outcome.run;
  statusEl.textContent = `Run ${run.id} dispatched; polling…`;

  try {
    for await (const status of helpers.api.pollStatus(run.id, {
      intervalSeconds: run.poll_interval_seconds || 2,
      deadlineAt: run.deadline_at,
    })) {
      statusEl.textContent = `Run ${status.id}: ${status.status} (${status.progress_pct}%)`;
      if (status.is_terminal) {
        const learnerError = (status.messages ?? []).find(
          (message) => message.level === "error",
        );
        if (learnerError !== undefined) {
          statusEl.textContent = learnerError.text;
          statusEl.classList.remove("text-muted");
          statusEl.classList.add("text-danger");
        }
        renderTerminalResult(resultEl, status);
        break;
      }
    }
  } catch (err) {
    statusEl.textContent = `Polling failed: ${String(err)}`;
  } finally {
    submitBtn.disabled = false;
  }
}

function renderTerminalResult(resultEl: HTMLElement, status: RunStatusResponse): void {
  // JSON.stringify produces safe text — assign via textContent (no
  // innerHTML). Even though the data comes from our own LMS, the
  // bundle treats it as untrusted on principle.
  resultEl.textContent = JSON.stringify(status, null, 2);
  resultEl.hidden = false;
}

export default mount;
