/**
 * Echo frontend bundle tests.
 *
 * Light unit coverage: mount() inserts a form, submit calls
 * helpers.api.submit with the input value, polling cycles render
 * startup, resume, and learner-facing terminal results. We don't test the
 * dispatcher → bundle integration here — that belongs to the LMS-side
 * integration test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, mountContract } from "../src/echo.js";
import { restoreLastRun, resumeRun } from "../../../_shared_frontend/run_lifecycle.js";

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

it("declares the supported browser mount contract", () => {
  expect(mountContract).toBe("purelms.interactive_mount.v1");
});

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

  it("polls and renders a learner-facing result for async runs", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: {
        attempt: null,
        run: {
          id: "abc-123",
          status: "queued",
          status_url: "/api/v1/sims/runs/abc-123/status",
          poll_interval_seconds: 0,
          websocket_url: null,
          deadline_at: null,
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
          outputs: { echoed_parameters: { value: "hello" } },
          messages: [],
        },
      ],
    });
    await mount(host, {}, helpers as never);

    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((r) => setTimeout(r, 10));

    const result = host.querySelector<HTMLElement>(".alert-success");
    expect(result?.textContent).toContain("Backend response");
    expect(result?.textContent).toContain("hello");
    expect(result?.textContent).toContain("abc-123");
    expect(host.querySelector("pre")).toBeNull();
  });

  it("delegates polled progress to the LMS-owned progress controller", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: {
        attempt: null,
        run: {
          id: "progress-123",
          status: "dispatched",
          status_url: "",
          poll_interval_seconds: 1,
          websocket_url: null,
          deadline_at: null,
        },
        is_complete: false,
      },
      pollStatuses: [
        {
          id: "progress-123",
          status: "running",
          progress_pct: null,
          progress_step: "Echoing parameters",
          is_terminal: false,
        },
        {
          id: "progress-123",
          status: "success",
          progress_pct: 100,
          progress_step: "Complete",
          is_terminal: true,
          runtime_seconds: 0.1,
          outputs: { echoed_parameters: { value: "hello" } },
          messages: [],
        },
      ],
    });
    const update = vi.fn();
    const complete = vi.fn();
    Object.assign(helpers, {
      ui: {
        createProgressBar: () => ({
          element: document.createElement("div"),
          update,
          indeterminate: vi.fn(),
          complete,
          error: vi.fn(),
          remove: vi.fn(),
        }),
      },
    });

    await mount(host, {}, helpers as never);
    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(update).toHaveBeenCalledWith(null, "Echoing parameters");
    expect(complete).toHaveBeenCalled();
  });

  it("describes provider acceptance as startup, not execution", async () => {
    let releasePoll!: () => void;
    const gate = new Promise<void>((resolve) => {
      releasePoll = resolve;
    });
    const { helpers } = makeHelpers({
      submitOutcome: {
        attempt: null,
        run: {
          id: "starting-123",
          status: "dispatched",
          status_url: "",
          poll_interval_seconds: 1,
          websocket_url: null,
          deadline_at: null,
        },
        is_complete: false,
      },
    });
    helpers.api.pollStatus = vi.fn(async function* () {
      yield {
        id: "starting-123",
        status: "dispatched",
        progress_pct: 0,
        progress_step: "",
        is_terminal: false,
      };
      await gate;
    });

    await mount(host, {}, helpers as never);
    host.querySelector<HTMLFormElement>("form")!.requestSubmit();
    await vi.waitFor(() => {
      expect(host.textContent).toContain("Starting the simulation environment");
    });
    expect(host.textContent).not.toContain("running (0%)");
    releasePoll();
  });

  it("restores parameters and resumes an in-flight run after navigation", async () => {
    const { helpers } = makeHelpers({
      submitOutcome: { attempt: null, run: null, is_complete: true },
      pollStatuses: [
        {
          id: "resume-123",
          status: "success",
          progress_pct: 100,
          progress_step: "done",
          is_terminal: true,
          completed_at: "2026-05-21T12:00:00Z",
          runtime_seconds: 0.2,
          outputs: { echoed_parameters: { value: "restored" } },
          messages: [],
        },
      ],
    });

    await mount(
      host,
      {
        last_run: {
          parameters: { value: "restored" },
          run: {
            id: "resume-123",
            status: "dispatched",
            status_url: "",
            poll_interval_seconds: 1,
            websocket_url: null,
            deadline_at: null,
          },
        },
      },
      helpers as never,
    );

    await vi.waitFor(() => {
      expect(host.querySelector(".alert-success")?.textContent).toContain("restored");
    });
    expect(host.querySelector<HTMLInputElement>("input")?.value).toBe("restored");
    expect(helpers.api.submit).not.toHaveBeenCalled();
    expect(helpers.api.pollStatus).toHaveBeenCalledWith("resume-123", {
      intervalSeconds: 1,
      deadlineAt: null,
    });
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
          websocket_url: null,
          deadline_at: null,
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

describe("shared run lifecycle", () => {
  it("restores a saved result instead of resuming its terminal run", () => {
    const completed = vi.fn();
    const inFlight = vi.fn();
    const incomplete = vi.fn();

    restoreLastRun(
      {
        run: {
          id: "complete-123",
          status: "success",
          status_url: "",
          poll_interval_seconds: 2,
          websocket_url: null,
          deadline_at: null,
        },
        outputs: { echoed_parameters: { value: "saved" } },
      },
      { onCompleted: completed, onInFlight: inFlight, onIncomplete: incomplete },
    );

    expect(completed).toHaveBeenCalledOnce();
    expect(inFlight).not.toHaveBeenCalled();
    expect(incomplete).not.toHaveBeenCalled();
  });

  it("does not expose a polling transport error to the learner", async () => {
    const onPollingError = vi.fn();
    async function* pollStatus(): AsyncIterable<{
      status: string;
      is_terminal: boolean;
      progress_pct: number | null;
      progress_step: string;
    }> {
      throw new Error("provider endpoint leaked a secret");
    }

    await resumeRun({
      run: {
        id: "broken-123",
        status: "dispatched",
        status_url: "",
        poll_interval_seconds: 2,
        websocket_url: null,
        deadline_at: null,
      },
      pollStatus,
      ui: { bar: null, setStatus: vi.fn() },
      onTerminal: vi.fn(),
      onProgress: vi.fn(),
      onPollingError,
    });

    expect(onPollingError).toHaveBeenCalledWith(
      "We lost contact with this simulation. Please try again.",
    );
  });
});
