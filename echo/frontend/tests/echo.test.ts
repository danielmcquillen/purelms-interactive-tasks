/**
 * Echo frontend bundle tests.
 *
 * Light unit coverage: mount() inserts a form, submit calls
 * helpers.api.submit with the input value, polling cycles render
 * progress and terminal status. We don't try to test the
 * dispatcher → bundle integration here — that belongs to the LMS-side
 * integration test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "../src/echo.js";

interface CapturedSubmit {
  parameters: Record<string, unknown>;
}

function makeHelpers(opts: {
  submitOutcome: unknown;
  pollStatuses?: Array<Record<string, unknown>>;
  pollThrows?: Error;
}) {
  const submitCalls: CapturedSubmit[] = [];
  const helpers = {
    api: {
      submit: vi.fn(async (parameters: Record<string, unknown>) => {
        submitCalls.push({ parameters });
        return opts.submitOutcome;
      }),
      pollStatus: vi.fn(async function* () {
        if (opts.pollThrows) throw opts.pollThrows;
        for (const s of opts.pollStatuses ?? []) {
          yield s;
        }
      }),
    },
    escape: (s: string) => s,
    meta: { bundle: "echo", unitBlockId: 42 },
  };
  return { helpers, submitCalls };
}

let host: HTMLElement;

beforeEach(() => {
  document.body.replaceChildren();
  host = document.createElement("div");
  document.body.appendChild(host);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("echo mount()", () => {
  it("renders a form with a value input and submit button", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: { attempt: null, run: null, is_complete: true },
    });
    await mount(host, {}, helpers as never);

    const input = host.querySelector<HTMLInputElement>("input[type=text]");
    const btn = host.querySelector<HTMLButtonElement>("button[type=submit]");
    expect(input).not.toBeNull();
    expect(btn?.textContent).toContain("Run echo");
  });

  it("submits the input value as a parameter", async () => {
    const { helpers, submitCalls } = makeHelpers({
      submitOutcome: { attempt: null, run: null, is_complete: true },
    });
    await mount(host, {}, helpers as never);

    const input = host.querySelector<HTMLInputElement>("input[type=text]")!;
    input.value = "world";
    host.querySelector<HTMLFormElement>("form")!.requestSubmit();

    // Let the microtask queue drain.
    await new Promise((r) => setTimeout(r, 0));

    expect(submitCalls).toHaveLength(1);
    expect(submitCalls[0]?.parameters).toEqual({ value: "world" });
  });

  it("reports synchronous completion without polling", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: { attempt: null, run: null, is_complete: true },
    });
    await mount(host, {}, helpers as never);

    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((r) => setTimeout(r, 0));

    const status = host.querySelector(".purelms-echo-task .small");
    expect(status?.textContent).toContain("complete");
    expect(helpers.api.pollStatus).not.toHaveBeenCalled();
  });

  it("polls and renders terminal status for async runs", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: {
        attempt: null,
        run: {
          id: "abc-123",
          status: "queued",
          status_url: "/api/v1/sims/runs/abc-123/status",
          poll_interval_seconds: 0,
        },
        is_complete: false,
      },
      pollStatuses: [
        { id: "abc-123", status: "running", progress_pct: 50, is_terminal: false },
        {
          id: "abc-123",
          status: "success",
          progress_pct: 100,
          is_terminal: true,
          completed_at: "2026-05-21T12:00:00Z",
          runtime_seconds: 0.5,
        },
      ],
    });
    await mount(host, {}, helpers as never);

    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((r) => setTimeout(r, 10));

    const result = host.querySelector<HTMLPreElement>("pre");
    expect(result?.hidden).toBe(false);
    expect(result?.textContent).toContain("success");
    expect(result?.textContent).toContain("abc-123");
  });

  it("shows the learner-safe message when a run fails at runtime", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: {
        attempt: null,
        run: {
          id: "failed-123",
          status: "running",
          status_url: "/api/v1/sims/runs/failed-123/status",
          poll_interval_seconds: 0,
        },
        is_complete: false,
      },
      pollStatuses: [
        {
          id: "failed-123",
          status: "failed_runtime",
          progress_pct: 0,
          is_terminal: true,
          messages: [
            {
              level: "error",
              code: "simulation_platform_error",
              text: "We couldn't complete this simulation. Please try again.",
            },
          ],
          outputs: {},
        },
      ],
    });
    await mount(host, {}, helpers as never);

    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((r) => setTimeout(r, 10));

    const status = host.querySelector(".purelms-echo-task .small");
    expect(status?.textContent).toContain("Please try again");
    expect(status?.textContent).not.toContain("failed_runtime");
  });

  it("surfaces submit errors", async () => {
    const helpers = {
      api: {
        submit: vi.fn(async () => {
          const err = new Error("kaboom") as Error & { detail?: string };
          err.detail = "tier gate";
          throw err;
        }),
        pollStatus: vi.fn(),
      },
      escape: (s: string) => s,
      meta: { bundle: "echo", unitBlockId: 42 },
    };
    await mount(host, {}, helpers as never);

    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((r) => setTimeout(r, 0));

    const status = host.querySelector(".purelms-echo-task .small");
    expect(status?.textContent).toContain("tier gate");
  });
});
