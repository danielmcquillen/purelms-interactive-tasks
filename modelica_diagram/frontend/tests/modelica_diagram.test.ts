/**
 * Mount-contract tests for the modelica_diagram bundle.
 *
 * Drawflow is mocked (it needs a real browser canvas); its ``export()`` returns
 * a canned graph so the submit path — serialise → ``helpers.api.submit`` →
 * poll → render — is exercised end-to-end in happy-dom.
 */

import { describe, expect, it, vi } from "vitest";

import type {
  MountHelpers,
  SimulationRunStatusResponse,
  SubmissionOutcomeResponse,
} from "../src/types";

// Mock Drawflow before importing the module under test. ``exportFn`` is
// hoisted so the factory can reference it; tests set its return value.
const { exportFn } = vi.hoisted(() => ({ exportFn: vi.fn() }));
vi.mock("drawflow", () => ({
  default: class MockDrawflow {
    reflow = "fixed";
    editor_mode = "edit";
    start(): void {}
    addNode(): number {
      return 1;
    }
    clear(): void {}
    removeNodeId(): void {}
    on(): void {}
    export(): unknown {
      return exportFn();
    }
  },
}));

const { mount, mountContract } = await import("../src/modelica_diagram");

it("declares the supported browser mount contract", () => {
  expect(mountContract).toBe("purelms.interactive_mount.v1");
});

function correctLoopExport(): unknown {
  const node = (id: number, type: string, outputs: object) => ({
    id,
    name: type,
    data: { type },
    inputs: {},
    outputs,
  });
  return {
    drawflow: {
      Home: {
        data: {
          "1": node(1, "boiler", { output_1: { connections: [{ node: "2", output: "input_1" }] } }),
          "2": node(2, "pump", { output_1: { connections: [{ node: "3", output: "input_1" }] } }),
          "3": node(3, "radiator", {
            output_1: { connections: [{ node: "1", output: "input_1" }] }, // port_b -> boiler.port_a
            output_2: { connections: [{ node: "4", output: "input_1" }] }, // heat -> room.heat
          }),
          "4": node(4, "room", {
            output_1: { connections: [{ node: "1", output: "input_2" }] }, // T_room -> boiler.T_room
          }),
        },
      },
    },
  };
}

function emptyExport(): unknown {
  return { drawflow: { Home: { data: {} } } };
}

function makeHelpers(overrides: Partial<MountHelpers> = {}): MountHelpers {
  const terminal: SimulationRunStatusResponse = {
    id: "r1",
    status: "success",
    progress_pct: 100,
    progress_step: "",
    is_terminal: true,
    completed_at: null,
    runtime_seconds: 0.12,
    outputs: {
      topology_correct: true,
      room_temp_final_c: 21.4,
      energy_used_kwh: 5.2,
      series_json: "[[0,18],[1,20],[2,21.4]]",
    },
    messages: [{ level: "info", code: "TOPOLOGY", text: "Looks good." }],
  };
  return {
    api: {
      submit: vi.fn(
        async (): Promise<SubmissionOutcomeResponse> => ({
          attempt: null,
          run: {
            id: "r1",
            status: "running",
            status_url: "",
            poll_interval_seconds: 1,
            websocket_url: null,
            deadline_at: null,
          },
          is_complete: false,
        }),
      ),
      pollStatus: async function* () {
        yield terminal;
      },
      ...overrides.api,
    },
    escape: (v: string) => v,
    ...(overrides.ui ? { ui: overrides.ui } : {}),
    meta: {
      bundle: "modelica_diagram.js",
      unitBlockId: 7,
      creditCost: 2,
      backendAvailable: true,
      ...overrides.meta,
    },
  };
}

describe("mount", () => {
  it("renders the palette, parameter sliders, and run button", async () => {
    exportFn.mockReturnValue(emptyExport());
    const host = document.createElement("div");
    await mount(host, {}, makeHelpers());

    expect(host.querySelectorAll(".mdl-palette button")).toHaveLength(4);
    expect(host.querySelectorAll(".mdl-field input[type=range]")).toHaveLength(4);
    expect(host.querySelector(".mdl-run")).not.toBeNull();
  });

  it("scopes slider IDs to the placement", async () => {
    exportFn.mockReturnValue(emptyExport());
    const first = document.createElement("div");
    const second = document.createElement("div");
    await mount(first, {}, makeHelpers());
    await mount(
      second,
      {},
      makeHelpers({ meta: { ...makeHelpers().meta, unitBlockId: 8 } }),
    );

    const ids = [...first.querySelectorAll("[id]"), ...second.querySelectorAll("[id]")]
      .map((node) => node.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("shows the unavailable notice when the backend is deactivated", async () => {
    const host = document.createElement("div");
    await mount(host, {}, makeHelpers({ meta: { backendAvailable: false } as MountHelpers["meta"] }));

    expect(host.querySelector(".mdl-notice")).not.toBeNull();
    expect(host.querySelectorAll(".mdl-palette button")).toHaveLength(0);
  });

  it("refuses to submit an empty canvas", async () => {
    exportFn.mockReturnValue(emptyExport());
    const host = document.createElement("div");
    const helpers = makeHelpers();
    await mount(host, {}, helpers);

    (host.querySelector(".mdl-run") as HTMLButtonElement).click();
    await Promise.resolve();

    expect(helpers.api.submit).not.toHaveBeenCalled();
    expect(host.querySelector(".mdl-status")?.textContent).toContain("Add some components");
  });

  it("serialises the canvas and submits diagram_json, then renders the verdict", async () => {
    exportFn.mockReturnValue(correctLoopExport());
    const host = document.createElement("div");
    const helpers = makeHelpers();
    await mount(host, {}, helpers);

    (host.querySelector(".mdl-run") as HTMLButtonElement).click();
    await vi.waitFor(() => {
      expect(host.querySelector(".mdl-verdict")).not.toBeNull();
    });

    expect(helpers.api.submit).toHaveBeenCalledTimes(1);
    const params = vi.mocked(helpers.api.submit).mock.calls[0]?.[0];
    expect(params?.scenario).toBe("hydronic_loop");
    expect(params?.boiler_nominal_power_kw).toBe(10);
    expect(params?.room_setpoint_c).toBe(21);
    const diagram = JSON.parse(String(params?.diagram_json));
    expect(diagram.schema).toBe("purelms.diagram.v1");
    expect(diagram.nodes).toHaveLength(4);
    expect(diagram.edges).toHaveLength(5);

    const verdict = host.querySelector(".mdl-verdict");
    expect(verdict?.classList.contains("ok")).toBe(true);
    expect(host.querySelector(".mdl-chart")).not.toBeNull();
  });

  it("delegates polled progress to the LMS-owned progress controller", async () => {
    exportFn.mockReturnValue(correctLoopExport());
    const host = document.createElement("div");
    const update = vi.fn();
    const complete = vi.fn();
    const helpers = makeHelpers({
      api: {
        submit: vi.fn(async () => ({
          attempt: null,
          run: {
            id: "progress-modelica",
            status: "dispatched",
            status_url: "",
            poll_interval_seconds: 1,
            websocket_url: null,
            deadline_at: null,
          },
          is_complete: false,
        })),
        pollStatus: async function* () {
          yield {
            id: "progress-modelica",
            status: "running",
            progress_pct: null,
            progress_step: "Checking diagram",
            is_terminal: false,
            completed_at: null,
            runtime_seconds: null,
            outputs: {},
            messages: [],
          };
          yield {
            id: "progress-modelica",
            status: "success",
            progress_pct: 100,
            progress_step: "Complete",
            is_terminal: true,
            completed_at: null,
            runtime_seconds: 0.12,
            outputs: { topology_correct: true },
            messages: [],
          };
        },
      },
      ui: {
        createProgressBar: () => ({
          element: document.createElement("div"),
          update,
          indeterminate: vi.fn(),
          determinate: vi.fn(),
          complete,
          error: vi.fn(),
          remove: vi.fn(),
        }),
      },
    });

    await mount(host, {}, helpers);
    (host.querySelector(".mdl-run") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(complete).toHaveBeenCalled());

    expect(update).toHaveBeenCalledWith(null, "Checking diagram");
  });

  it("restores and resumes an in-flight run after navigation", async () => {
    exportFn.mockReturnValue(emptyExport());
    const host = document.createElement("div");
    let releasePoll!: () => void;
    const gate = new Promise<void>((resolve) => {
      releasePoll = resolve;
    });
    const pollStatus = vi.fn(async function* () {
      yield {
        id: "resume-modelica",
        status: "dispatched",
        progress_pct: 0,
        progress_step: "",
        is_terminal: false,
        completed_at: null,
        runtime_seconds: null,
        outputs: {},
        messages: [],
      };
      await gate;
      yield {
        id: "resume-modelica",
        status: "success",
        progress_pct: 100,
        progress_step: "done",
        is_terminal: true,
        completed_at: "2026-05-24T00:00:00Z",
        runtime_seconds: 0.2,
        outputs: {
          topology_correct: true,
          room_temp_final_c: 21,
          energy_used_kwh: 4,
        },
        messages: [],
      };
    });
    const helpers = makeHelpers({
      api: {
        submit: vi.fn(),
        pollStatus,
      },
    });

    await mount(
      host,
      {
        last_run: {
          parameters: { boiler_nominal_power_kw: 14 },
          run: {
            id: "resume-modelica",
            status: "dispatched",
            status_url: "",
            poll_interval_seconds: 1,
            websocket_url: null,
            deadline_at: null,
          },
        },
      },
      helpers,
    );

    await vi.waitFor(() => {
      expect(host.querySelector(".mdl-status")?.textContent).toContain(
        "Starting the simulation environment",
      );
    });
    expect(
      (host.querySelector("#mdl-7-boiler_nominal_power_kw") as HTMLInputElement).value,
    ).toBe("14");
    expect((host.querySelector(".mdl-run") as HTMLButtonElement).disabled).toBe(true);
    expect(helpers.api.submit).not.toHaveBeenCalled();
    expect(pollStatus).toHaveBeenCalledWith("resume-modelica", {
      intervalSeconds: 1,
      deadlineAt: null,
    });

    releasePoll();
    await vi.waitFor(() => {
      expect(host.querySelector(".mdl-verdict")?.textContent).toContain(
        "matches the system",
      );
    });
  });
});
