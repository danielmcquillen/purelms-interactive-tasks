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
import type { MountHelpers, SubmissionOutcomeResponse } from "../src/types";

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
    meta: {
      bundle: "energyplus_single_zone.js",
      unitBlockId: 42,
      creditCost: 1,
      backendAvailable: true,
      ...overrides.meta,
    },
  };
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

    expect(element.querySelector("#purelms-glazing_u_value")).not.toBeNull();
    expect(element.querySelector("#purelms-window_area")).not.toBeNull();
    expect(element.querySelector("#purelms-climate_zone")).not.toBeNull();
  });

  it("uses manifest defaults when config is empty", async () => {
    const element = document.createElement("div");
    await mount(element, {}, makeHelpers());

    const uvalue = element.querySelector(
      "#purelms-glazing_u_value",
    ) as HTMLInputElement;
    const area = element.querySelector(
      "#purelms-window_area",
    ) as HTMLInputElement;
    const climate = element.querySelector(
      "#purelms-climate_zone",
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
      "#purelms-glazing_u_value",
    ) as HTMLInputElement;
    const area = element.querySelector(
      "#purelms-window_area",
    ) as HTMLInputElement;
    const climate = element.querySelector(
      "#purelms-climate_zone",
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
      "#purelms-glazing_u_value",
    )?.parentElement;
    expect(uvalueRow?.style.display).toBe("none");
    // Other parameters still visible.
    const areaRow = element.querySelector("#purelms-window_area")?.parentElement;
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
      "#purelms-window_area",
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
      "#purelms-climate_zone",
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
      "#purelms-glazing_u_value",
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
        run: { id: "run-123", status: "running", status_url: "", poll_interval_seconds: 1 },
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

  it("renders an error box on terminal failure", async () => {
    const element = document.createElement("div");
    const submit = vi.fn(
      async (): Promise<SubmissionOutcomeResponse> => ({
        attempt: null,
        run: { id: "run-x", status: "running", status_url: "", poll_interval_seconds: 1 },
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
