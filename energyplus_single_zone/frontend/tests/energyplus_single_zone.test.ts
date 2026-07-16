/**
 * Vitest + happy-dom tests for the EnergyPlus single-zone frontend.
 *
 * happy-dom doesn't provide a WebGL context, so the Three.js scene
 * module returns ``null`` from :func:`createScene` and the rest of
 * the UI renders without it. These tests cover the form + result-
 * rendering paths — the 3D viz is a pedagogical bonus, not the
 * contract surface.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mount } from "../src/energyplus_single_zone";
import type {
  MountHelpers,
  ProgressBarController,
  SubmissionOutcomeResponse,
} from "../src/types";

// ---------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------

function makeHelpers(
  overrides: Partial<MountHelpers> = {},
): MountHelpers {
  return {
    api: {
      submit: vi.fn(async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: null,
        is_complete: true,
      })),
      pollStatus: async function* () {
        // Default: yield nothing — synchronous outcome path.
      },
      ...overrides.api,
    },
    escape: (v: string) =>
      v.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
    // Thread an optional ``ui`` through so progress-bar tests can
    // inject a recording controller. OMITTED (not set to undefined)
    // when not supplied — ``exactOptionalPropertyTypes`` forbids an
    // explicit ``undefined`` on an optional field, and omission is
    // what keeps the other tests on the no-bar fallback path anyway.
    ...(overrides.ui ? { ui: overrides.ui } : {}),
    meta: {
      bundle: "energyplus_single_zone.js",
      unitBlockId: 42,
      creditCost: 1,
      backendAvailable: true,
      ...overrides.meta,
    },
  };
}

/**
 * A :class:`ProgressBarController` that records every mode call so a
 * test can assert the bar-decision matrix (indeterminate vs.
 * determinate vs. terminal). The real bar's DOM behavior is tested
 * separately in the LMS; here we only care about which method the
 * widget chose to call.
 */
interface RecordingBar {
  controller: ProgressBarController;
  calls: Array<{ mode: string; args: unknown[] }>;
}

function makeRecordingBar(): RecordingBar {
  const calls: Array<{ mode: string; args: unknown[] }> = [];
  const record = (mode: string) => (...args: unknown[]) => {
    calls.push({ mode, args });
  };
  const controller: ProgressBarController = {
    element: document.createElement("div"),
    indeterminate: record("indeterminate"),
    determinate: record("determinate"),
    complete: record("complete"),
    error: record("error"),
    remove: record("remove"),
  };
  return { controller, calls };
}

// happy-dom doesn't provide a WebGL context, so every test hits the
// graceful-degradation path in ``scene.ts`` which logs a `console.warn`
// ("WebGL unavailable, skipping 3D viz") + Three.js emits its own
// stderr line. The fallback is the *expected* behavior — silencing
// the predictable noise here keeps real warnings visible. We restore
// the spies in afterEach so a real bug elsewhere can't hide behind
// these mocks.
let warnSpy: ReturnType<typeof vi.spyOn>;
let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  warnSpy.mockRestore();
  errorSpy.mockRestore();
  document.body.replaceChildren();
});

// ---------------------------------------------------------------------
// Form rendering
// ---------------------------------------------------------------------

describe("mount() — form rendering", () => {
  it("renders three parameter inputs by default", async () => {
    const element = document.createElement("div");
    document.body.append(element);

    await mount(element, {}, makeHelpers());

    expect(element.querySelector("#purelms-42-glazing-u-value")).not.toBeNull();
    expect(element.querySelector("#purelms-42-window-area")).not.toBeNull();
    expect(element.querySelector("#purelms-42-climate-zone")).not.toBeNull();
  });

  it("scopes form IDs to the placement", async () => {
    const first = document.createElement("div");
    const second = document.createElement("div");
    await mount(first, {}, makeHelpers());
    await mount(
      second,
      {},
      makeHelpers({ meta: { ...makeHelpers().meta, unitBlockId: 43 } }),
    );

    const ids = [...first.querySelectorAll("[id]"), ...second.querySelectorAll("[id]")]
      .map((node) => node.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("uses manifest defaults when config is empty", async () => {
    const element = document.createElement("div");
    await mount(element, {}, makeHelpers());

    const uvalue = element.querySelector(
      "#purelms-42-glazing-u-value",
    ) as HTMLInputElement;
    const area = element.querySelector(
      "#purelms-42-window-area",
    ) as HTMLInputElement;
    const climate = element.querySelector(
      "#purelms-42-climate-zone",
    ) as HTMLSelectElement;

    expect(uvalue.value).toBe("2.5");
    expect(area.value).toBe("5");
    expect(climate.value).toBe("5A");
  });

  it("applies Layer 2 default overrides from config", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {
        parameters: {
          glazing_u_value: { default: 1.0 },
          window_area: { default: 12.0 },
          climate_zone: { default: "6A" },
        },
      },
      makeHelpers(),
    );

    const uvalue = element.querySelector(
      "#purelms-42-glazing-u-value",
    ) as HTMLInputElement;
    const area = element.querySelector(
      "#purelms-42-window-area",
    ) as HTMLInputElement;
    const climate = element.querySelector(
      "#purelms-42-climate-zone",
    ) as HTMLSelectElement;

    expect(uvalue.value).toBe("1");
    expect(area.value).toBe("12");
    expect(climate.value).toBe("6A");
  });

  it("hides parameters with visible=false", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {
        parameters: {
          glazing_u_value: { visible: false },
        },
      },
      makeHelpers(),
    );

    const uvalueRow = element.querySelector(
      "#purelms-42-glazing-u-value",
    )?.parentElement;
    expect(uvalueRow?.style.display).toBe("none");
    // Other parameters still visible.
    const areaRow = element.querySelector("#purelms-42-window-area")?.parentElement;
    expect(areaRow?.style.display).not.toBe("none");
  });

  it("disables parameters with enabled=false", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {
        parameters: {
          window_area: { enabled: false },
        },
      },
      makeHelpers(),
    );

    const area = element.querySelector(
      "#purelms-42-window-area",
    ) as HTMLInputElement;
    expect(area.disabled).toBe(true);
  });

  it("restricts climate-zone choices to L2 subset", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {
        parameters: {
          climate_zone: {
            // Course author tightened to a single zone.
            choices: [{ value: "5A", label: "Chicago only" }],
          },
        },
      },
      makeHelpers(),
    );

    const climate = element.querySelector(
      "#purelms-42-climate-zone",
    ) as HTMLSelectElement;
    expect(climate.options.length).toBe(1);
    expect(climate.options[0]!.value).toBe("5A");
  });

  it("tightens numeric bounds when L2 specifies min/max", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {
        parameters: {
          glazing_u_value: { min: 1.0, max: 3.0 },
        },
      },
      makeHelpers(),
    );

    const uvalue = element.querySelector(
      "#purelms-42-glazing-u-value",
    ) as HTMLInputElement;
    expect(uvalue.min).toBe("1");
    expect(uvalue.max).toBe("3");
  });

  it("renders the unavailable notice when backendAvailable is false", async () => {
    const element = document.createElement("div");
    await mount(
      element,
      {},
      makeHelpers({ meta: { ...makeHelpers().meta, backendAvailable: false } }),
    );

    expect(element.textContent).toContain("no longer available");
    // No submit button.
    expect(element.querySelector("button")).toBeNull();
  });
});

// ---------------------------------------------------------------------
// Submission flow
// ---------------------------------------------------------------------

describe("mount() — submission", () => {
  it("calls helpers.api.submit with the current parameter values", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: null,
        is_complete: true,
      }),
    );

    await mount(
      element,
      {},
      makeHelpers({
        api: {
          submit,
          pollStatus: async function* () {},
        },
      }),
    );

    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    // Let microtasks settle.
    await new Promise((r) => setTimeout(r, 0));

    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledWith({
      glazing_u_value: 2.5,
      window_area: 5.0,
      climate_zone: "5A",
    });
  });

  it("renders result cards on terminal success", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-123", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-123",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.42,
        outputs: {
          annual_heating_kWh: 1650.0,
          annual_cooling_kWh: 240.0,
          peak_heating_kW: 0.4,
          notes: "test notes",
        },
        // Empty array (not null) on the success path — matches the
        // server-side serializer's default for the messages_payload
        // JSONField.
        messages: [],
      };
    };

    await mount(
      element,
      {},
      makeHelpers({ api: { submit, pollStatus } }),
    );

    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    // Allow the async iterator + DOM update to settle.
    await new Promise((r) => setTimeout(r, 10));

    const results = element.querySelector(".purelms-task-results");
    expect(results).not.toBeNull();
    expect(results!.textContent).toContain("Annual heating");
    expect(results!.textContent).toContain("1,650");
    expect(results!.textContent).toContain("test notes");
  });

  it("renders notes verbatim — no HTML double-escaping", async () => {
    // Regression: notes were assigned via `helpers.escape(...)` into
    // `textContent`, which double-encodes — "U < 2" rendered as the
    // literal "U &lt; 2". textContent already escapes; assign raw.
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-esc", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-esc",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.4,
        outputs: {
          annual_heating_kWh: 1650,
          annual_cooling_kWh: 240,
          peak_heating_kW: 0.4,
          notes: "Analytical model: U < 2.0 & A > 5 m²",
        },
        messages: [],
      };
    };

    await mount(element, {}, makeHelpers({ api: { submit, pollStatus } }));

    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await new Promise((r) => setTimeout(r, 10));

    const results = element.querySelector(".purelms-task-results")!;
    // Raw characters survive...
    expect(results.textContent).toContain("U < 2.0 & A > 5 m²");
    // ...and the HTML-entity forms (what double-escaping produces) do not.
    expect(results.textContent).not.toContain("&lt;");
    expect(results.textContent).not.toContain("&amp;");
  });

  it("renders an error box on terminal failure", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-x", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-x",
        status: "failed_runtime",
        progress_pct: 0,
        progress_step: "",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0,
        // FAILED_RUNTIME paths send empty outputs (not null) — matches
        // the server-side serializer's non-nullable JSONField default.
        outputs: {},
        messages: [
          { level: "error" as const, code: "EPLUS_SZ.BAD_PARAMETERS", text: "bad climate" },
        ],
      };
    };

    await mount(
      element,
      {},
      makeHelpers({ api: { submit, pollStatus } }),
    );

    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await new Promise((r) => setTimeout(r, 10));

    const status = element.querySelector(".purelms-task-status");
    expect(status?.textContent).toContain("Run failed");
    const results = element.querySelector(".purelms-task-results");
    expect(results?.textContent).toContain("bad climate");
  });

  it("surfaces a submission error without polling", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(async () => {
      throw new Error("network down");
    });

    await mount(
      element,
      {},
      makeHelpers({
        api: {
          submit,
          pollStatus: async function* () {
            throw new Error("should not be called");
          },
        },
      }),
    );

    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    await new Promise((r) => setTimeout(r, 10));

    const status = element.querySelector(".purelms-task-status");
    expect(status?.textContent).toContain("Submission failed");
    expect(status?.textContent).toContain("network down");
  });
});

// ---------------------------------------------------------------------
// Progress bar — the indeterminate/determinate decision matrix
// ---------------------------------------------------------------------

describe("mount() — progress bar", () => {
  function submitAndSettle(element: HTMLElement) {
    const form = element.querySelector("form")!;
    form.dispatchEvent(new Event("submit", { cancelable: true }));
    return new Promise((r) => setTimeout(r, 10));
  }

  it("uses a DETERMINATE bar on the async path when reportsProgress is true", async () => {
    const element = document.createElement("div");
    const bar = makeRecordingBar();
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-async", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-async",
        status: "running",
        progress_pct: 55,
        progress_step: "Running EnergyPlus",
        is_terminal: false,
        completed_at: null,
        runtime_seconds: null,
        outputs: {},
        messages: [],
      };
      yield {
        id: "run-async",
        status: "success",
        progress_pct: 100,
        progress_step: "Complete",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.42,
        outputs: {
          annual_heating_kWh: 1650.0,
          annual_cooling_kWh: 240.0,
          peak_heating_kW: 0.4,
          notes: "ok",
        },
        messages: [],
      };
    };

    await mount(
      element,
      {},
      makeHelpers({
        api: { submit, pollStatus },
        ui: { createProgressBar: () => bar.controller },
        meta: { ...makeHelpers().meta, reportsProgress: true },
      }),
    );

    await submitAndSettle(element);

    const modes = bar.calls.map((c) => c.mode);
    // The in-flight poll drove a determinate update with the polled pct.
    expect(bar.calls).toContainEqual({
      mode: "determinate",
      args: [55, "Running EnergyPlus"],
    });
    // Terminal success → solid green complete bar; never an error.
    expect(modes).toContain("complete");
    expect(modes).not.toContain("error");
  });

  it("stays INDETERMINATE on the async path when reportsProgress is absent", async () => {
    const element = document.createElement("div");
    const bar = makeRecordingBar();
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-noprog", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-noprog",
        status: "running",
        progress_pct: 55,
        progress_step: "halfway",
        is_terminal: false,
        completed_at: null,
        runtime_seconds: null,
        outputs: {},
        messages: [],
      };
      yield {
        id: "run-noprog",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.4,
        outputs: { annual_heating_kWh: 1650, annual_cooling_kWh: 240, peak_heating_kW: 0.4, notes: "ok" },
        messages: [],
      };
    };

    await mount(
      element,
      {},
      // No reportsProgress in meta — the bundle treats absent as false.
      makeHelpers({ api: { submit, pollStatus }, ui: { createProgressBar: () => bar.controller } }),
    );

    await submitAndSettle(element);

    const modes = bar.calls.map((c) => c.mode);
    // Never a determinate call — the polled progress_pct is ignored.
    expect(modes).not.toContain("determinate");
    expect(modes).toContain("indeterminate");
    expect(modes).toContain("complete");
  });

  it("uses the declared progress mode whenever a run reference is polled", async () => {
    const element = document.createElement("div");
    const bar = makeRecordingBar();
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-sync", status: "success", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: true,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-sync",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.42,
        outputs: { annual_heating_kWh: 1650, annual_cooling_kWh: 240, peak_heating_kW: 0.4, notes: "ok" },
        messages: [],
      };
    };

    await mount(
      element,
      {},
      makeHelpers({
        api: { submit, pollStatus },
        ui: { createProgressBar: () => bar.controller },
        meta: { ...makeHelpers().meta, reportsProgress: true },
      }),
    );

    await submitAndSettle(element);

    const modes = bar.calls.map((c) => c.mode);
    expect(modes).toContain("determinate");
    expect(modes).toContain("indeterminate");
    expect(modes).toContain("complete");
  });

  it("drives the bar to ERROR on a submission failure", async () => {
    const element = document.createElement("div");
    const bar = makeRecordingBar();
    const submit = vi.fn(async () => {
      throw new Error("network down");
    });

    await mount(
      element,
      {},
      makeHelpers({
        api: {
          submit,
          pollStatus: async function* () {
            throw new Error("should not be called");
          },
        },
        ui: { createProgressBar: () => bar.controller },
      }),
    );

    await submitAndSettle(element);

    const modes = bar.calls.map((c) => c.mode);
    expect(modes).toContain("error");
    expect(modes).not.toContain("complete");
  });

  it("works without helpers.ui (older dispatcher) — falls back to the text status", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-old", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-old",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.4,
        outputs: { annual_heating_kWh: 1650, annual_cooling_kWh: 240, peak_heating_kW: 0.4, notes: "ok" },
        messages: [],
      };
    };

    // No ``ui`` supplied → helpers.ui is undefined → bar is null.
    await mount(element, {}, makeHelpers({ api: { submit, pollStatus } }));

    await submitAndSettle(element);

    // Still renders the terminal result via the text/status fallback.
    const results = element.querySelector(".purelms-task-results");
    expect(results).not.toBeNull();
    expect(results!.textContent).toContain("1,650");
  });

  it("does NOT duplicate status text — the standalone line is hidden when a bar is present", async () => {
    // Regression: the bundle used to write every status string to BOTH
    // its own text line and the bar's caption, so "Submitting…" (etc.)
    // showed — and was announced — twice. With a bar present the bar's
    // aria-live caption is the single status surface; the text line is
    // hidden + empty.
    const element = document.createElement("div");
    const bar = makeRecordingBar();
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-dup", status: "running", status_url: "", poll_interval_seconds: 1, websocket_url: null, deadline_at: null },
        is_complete: false,
      }),
    );
    const pollStatus = async function* () {
      yield {
        id: "run-dup",
        status: "running",
        progress_pct: 40,
        progress_step: "Running EnergyPlus",
        is_terminal: false,
        completed_at: null,
        runtime_seconds: null,
        outputs: {},
        messages: [],
      };
      yield {
        id: "run-dup",
        status: "success",
        progress_pct: 100,
        progress_step: "Complete",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.42,
        outputs: { annual_heating_kWh: 1650, annual_cooling_kWh: 240, peak_heating_kW: 0.4, notes: "ok" },
        messages: [],
      };
    };

    await mount(
      element,
      {},
      makeHelpers({
        api: { submit, pollStatus },
        ui: { createProgressBar: () => bar.controller },
        meta: { ...makeHelpers().meta, reportsProgress: true },
      }),
    );

    await submitAndSettle(element);

    // The bar drove the status (its caption carries the step text)...
    expect(bar.calls.length).toBeGreaterThan(0);
    // ...and the standalone text line is hidden + empty, so nothing is
    // shown or announced twice.
    const statusLine = element.querySelector<HTMLElement>(".purelms-task-status")!;
    expect(statusLine.style.display).toBe("none");
    expect(statusLine.textContent).toBe("");
    // The result still renders (proves completion ran through the bar path).
    expect(element.querySelector(".purelms-task-results")?.textContent).toContain("1,650");
  });
});

// ---------------------------------------------------------------------
// Default export
// ---------------------------------------------------------------------

describe("module exports", () => {
  it("exports mount as named AND default", async () => {
    const namedMount = mount;
    const mod = await import("../src/energyplus_single_zone");
    expect(typeof mod.mount).toBe("function");
    expect(mod.default).toBe(namedMount);
  });
});
