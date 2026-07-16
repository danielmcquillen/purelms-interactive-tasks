/**
 * TODO: rename this file to <your-slug>.ts (matching your slug) and
 * update package.json's build script + interactive_task.yaml's
 * frontend.bundle accordingly.
 *
 * Every InteractiveTask frontend
 * exports a `mount(element, config, helpers)` function. The bundle
 * owns its element completely after `mount()` returns — the LMS-side
 * dispatcher never re-enters this element.
 *
 * Replace the placeholder UI below with your domain interaction
 * (3D viewer, form, chart, etc.). The echo fixture
 * (echo/frontend/src/echo.ts) is a worked example showing the full
 * submit → poll → terminal-result flow.
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
  meta: { bundle: string; unitBlockId: number };
}

export async function mount(
  element: HTMLElement,
  _config: Record<string, unknown>,
  helpers: MountHelpers,
): Promise<void> {
  // TODO: replace this placeholder with your real UI.
  const root = document.createElement("div");
  root.className = "purelms-todo-your-slug-here";
  root.textContent = `Hello from bundle ${helpers.meta.bundle}.`;
  element.replaceChildren(root);
}

export default mount;
