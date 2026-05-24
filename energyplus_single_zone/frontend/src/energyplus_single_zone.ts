/**
 * EnergyPlus single-zone InteractiveTask — frontend bundle.
 *
 * Per ADR-0014, this module exports a ``mount(element, config,
 * helpers)`` function. The LMS dispatcher dynamic-imports the
 * built bundle (``dist/energyplus_single_zone.js``) and calls
 * ``mount()`` exactly once per placement.
 *
 * Layout:
 *
 *   ┌──────────────────────┬───────────────────────────┐
 *   │  Form panel          │   3D scene                │
 *   │  - U-value slider    │   (Three.js — gracefully  │
 *   │  - area slider       │    degrades if WebGL is   │
 *   │  - climate dropdown  │    unavailable)           │
 *   │  - Submit button     │                           │
 *   │  - Status line       │                           │
 *   │  - Result cards      │                           │
 *   └──────────────────────┴───────────────────────────┘
 *
 * **Framework choice:** vanilla TypeScript + Three.js. The
 * ``BACKEND_AUTHORING_GUIDE.md`` notes that any framework works
 * with the dispatcher contract — vanilla TS keeps the bundle
 * small (~150 KB minified+gzipped with Three.js) and the mounting
 * code straightforward. Angular's bootstrapping ceremony for an
 * arbitrary HTMLElement target adds complexity without
 * pedagogical value here; future tasks that benefit from
 * Angular's component model can adopt it on a per-task basis.
 */

import { createScene } from "./scene";
import type { SceneHandle } from "./scene";
import {
  CLIMATE_CHOICES,
  CLIMATE_DATA,
  DEFAULT_CLIMATE_ZONE,
  DEFAULT_GLAZING_U_VALUE,
  DEFAULT_WINDOW_AREA,
  GLAZING_U_VALUE_MAX,
  GLAZING_U_VALUE_MIN,
  GLAZING_U_VALUE_STEP,
  WINDOW_AREA_MAX,
  WINDOW_AREA_MIN,
  WINDOW_AREA_STEP,
} from "./constants";
import type {
  EnergyPlusConfig,
  EnergyPlusOutputs,
  EnumChoice,
  MountFn,
  MountHelpers,
  NumberParamConfig,
  SimulationRunStatusResponse,
} from "./types";

interface ParameterState {
  glazing_u_value: number;
  window_area: number;
  climate_zone: string;
}

/**
 * Merge manifest defaults with Layer 2 config overrides. The
 * order is "manifest default → L2 override → user value." This
 * function only handles the manifest+L2 merge; user values come
 * from input elements at submit time.
 */
function buildInitialParameters(config: EnergyPlusConfig): ParameterState {
  const params = config.parameters ?? {};
  return {
    glazing_u_value: params.glazing_u_value?.default ?? DEFAULT_GLAZING_U_VALUE,
    window_area: params.window_area?.default ?? DEFAULT_WINDOW_AREA,
    climate_zone: params.climate_zone?.default ?? DEFAULT_CLIMATE_ZONE,
  };
}

/**
 * Compute the effective bounds for a numeric parameter — the L2
 * override may tighten the manifest's bounds. The bundle should
 * never expose values outside what the course author allowed.
 */
function effectiveNumberBounds(
  l2: NumberParamConfig | undefined,
  manifestMin: number,
  manifestMax: number,
  manifestStep: number,
): { min: number; max: number; step: number } {
  return {
    min: Math.max(l2?.min ?? manifestMin, manifestMin),
    max: Math.min(l2?.max ?? manifestMax, manifestMax),
    step: l2?.step ?? manifestStep,
  };
}

/**
 * Compute the effective choices for the climate zone enum. The
 * L2 override may be a subset; if absent, all manifest choices
 * are available.
 */
function effectiveEnumChoices(
  l2Choices: EnumChoice[] | undefined,
): EnumChoice[] {
  if (!l2Choices || l2Choices.length === 0) {
    return CLIMATE_CHOICES;
  }
  // Filter the manifest choices by the L2-allowed values, preserving
  // the manifest's labels (the LMS may have abbreviated them).
  return l2Choices
    .map((c) => CLIMATE_DATA[c.value])
    .filter((c): c is (typeof CLIMATE_CHOICES)[number] => c !== undefined);
}

// ---------------------------------------------------------------------
// mount() — the dispatcher contract entrypoint
// ---------------------------------------------------------------------

export const mount: MountFn = async (
  element,
  configRaw,
  helpers,
): Promise<void> => {
  const config = configRaw as EnergyPlusConfig;
  const params = buildInitialParameters(config);

  // ---- Outer layout ----
  const root = document.createElement("div");
  root.className = "purelms-energyplus-task";
  root.style.cssText = `
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    align-items: flex-start;
    font-family: system-ui, -apple-system, sans-serif;
  `;

  const formPanel = document.createElement("div");
  formPanel.style.cssText = "flex: 1 1 360px; min-width: 320px;";

  const scenePanel = document.createElement("div");
  scenePanel.style.cssText = "flex: 0 0 360px; min-height: 280px;";

  root.append(formPanel, scenePanel);
  element.replaceChildren(root);

  // ---- Backend-availability gate ----
  // The LMS injects backendAvailable=false when the registration is
  // deactivated. Render a "no longer available" message instead of
  // the form.
  if (helpers.meta.backendAvailable === false) {
    formPanel.append(buildUnavailableNotice(helpers));
    return;
  }

  // ---- 3D scene (optional — gracefully degrades on no-WebGL) ----
  let sceneHandle: SceneHandle | null = null;
  sceneHandle = createScene(scenePanel);
  sceneHandle?.update(params);
  if (sceneHandle === null) {
    // No WebGL — collapse the scene panel to save horizontal space.
    scenePanel.style.display = "none";
  }

  // ---- Form ----
  const { formEl, valueDisplays, inputs, submitButton } = buildForm({
    config,
    params,
    onChange: (next) => {
      params.glazing_u_value = next.glazing_u_value;
      params.window_area = next.window_area;
      params.climate_zone = next.climate_zone;
      sceneHandle?.update(params);
      updateLiveValueDisplays(valueDisplays, next);
    },
  });

  const statusEl = document.createElement("div");
  statusEl.className = "purelms-task-status";
  statusEl.setAttribute("aria-live", "polite");
  statusEl.style.cssText = "margin-top: 12px; min-height: 1.5em; color: #6b7280;";

  const resultsEl = document.createElement("div");
  resultsEl.className = "purelms-task-results";
  resultsEl.style.cssText = "margin-top: 16px;";

  formPanel.append(formEl, statusEl, resultsEl);

  formEl.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const current = readParameters(inputs);
    void handleSubmit({
      parameters: current,
      submitButton,
      statusEl,
      resultsEl,
      helpers,
    });
  });
};

export default mount;

// ---------------------------------------------------------------------
// Form construction
// ---------------------------------------------------------------------

interface FormElements {
  formEl: HTMLFormElement;
  valueDisplays: {
    glazing_u_value: HTMLSpanElement;
    window_area: HTMLSpanElement;
  };
  inputs: {
    glazing_u_value: HTMLInputElement;
    window_area: HTMLInputElement;
    climate_zone: HTMLSelectElement;
  };
  submitButton: HTMLButtonElement;
}

interface BuildFormArgs {
  config: EnergyPlusConfig;
  params: ParameterState;
  onChange: (next: ParameterState) => void;
}

function buildForm({ config, params, onChange }: BuildFormArgs): FormElements {
  const formEl = document.createElement("form");
  formEl.noValidate = true;
  formEl.style.cssText = "display: flex; flex-direction: column; gap: 14px;";

  const uvalueBounds = effectiveNumberBounds(
    config.parameters?.glazing_u_value,
    GLAZING_U_VALUE_MIN,
    GLAZING_U_VALUE_MAX,
    GLAZING_U_VALUE_STEP,
  );
  const areaBounds = effectiveNumberBounds(
    config.parameters?.window_area,
    WINDOW_AREA_MIN,
    WINDOW_AREA_MAX,
    WINDOW_AREA_STEP,
  );

  // ---- U-value slider ----
  const uvalueVisible = config.parameters?.glazing_u_value?.visible !== false;
  const uvalueEnabled = config.parameters?.glazing_u_value?.enabled !== false;
  const uvalueRow = buildNumberSliderRow({
    name: "glazing_u_value",
    label: "Glazing U-value",
    unit: "W/m²K",
    helpText: "Lower is better-insulating. ~0.7=triple-pane, 2.5=double, 6.0=single.",
    value: params.glazing_u_value,
    bounds: uvalueBounds,
    visible: uvalueVisible,
    enabled: uvalueEnabled,
  });

  // ---- Window area slider ----
  const areaVisible = config.parameters?.window_area?.visible !== false;
  const areaEnabled = config.parameters?.window_area?.enabled !== false;
  const areaRow = buildNumberSliderRow({
    name: "window_area",
    label: "Window area",
    unit: "m²",
    helpText: "Total glazing in the zone.",
    value: params.window_area,
    bounds: areaBounds,
    visible: areaVisible,
    enabled: areaEnabled,
  });

  // ---- Climate zone dropdown ----
  const climateChoices = effectiveEnumChoices(
    config.parameters?.climate_zone?.choices,
  );
  const climateVisible = config.parameters?.climate_zone?.visible !== false;
  const climateEnabled = config.parameters?.climate_zone?.enabled !== false;
  const climateRow = buildEnumSelectRow({
    name: "climate_zone",
    label: "Climate zone",
    helpText: "ASHRAE climate zone — drives heating + cooling degree days.",
    value: params.climate_zone,
    choices: climateChoices,
    visible: climateVisible,
    enabled: climateEnabled,
  });

  // ---- Submit button ----
  const submitButton = document.createElement("button");
  submitButton.type = "submit";
  submitButton.textContent = "Run simulation";
  submitButton.style.cssText = `
    padding: 8px 16px;
    background: #2563eb;
    color: white;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
    align-self: flex-start;
  `;

  formEl.append(uvalueRow.rowEl, areaRow.rowEl, climateRow.rowEl, submitButton);

  // Wire up live-update on any input change.
  const inputs = {
    glazing_u_value: uvalueRow.inputEl,
    window_area: areaRow.inputEl,
    climate_zone: climateRow.selectEl,
  };

  const valueDisplays = {
    glazing_u_value: uvalueRow.valueEl,
    window_area: areaRow.valueEl,
  };

  const handleAnyChange = (): void => {
    onChange(readParameters(inputs));
  };
  inputs.glazing_u_value.addEventListener("input", handleAnyChange);
  inputs.window_area.addEventListener("input", handleAnyChange);
  inputs.climate_zone.addEventListener("change", handleAnyChange);

  return { formEl, valueDisplays, inputs, submitButton };
}

interface NumberSliderRowArgs {
  name: string;
  label: string;
  unit: string;
  helpText: string;
  value: number;
  bounds: { min: number; max: number; step: number };
  visible: boolean;
  enabled: boolean;
}

interface NumberSliderRowResult {
  rowEl: HTMLDivElement;
  inputEl: HTMLInputElement;
  valueEl: HTMLSpanElement;
}

function buildNumberSliderRow(args: NumberSliderRowArgs): NumberSliderRowResult {
  const rowEl = document.createElement("div");
  rowEl.style.cssText = "display: flex; flex-direction: column; gap: 4px;";
  if (!args.visible) {
    rowEl.style.display = "none";
  }

  const labelEl = document.createElement("label");
  labelEl.htmlFor = `purelms-${args.name}`;
  labelEl.style.cssText =
    "display: flex; justify-content: space-between; font-weight: 600; font-size: 14px;";

  const labelText = document.createElement("span");
  labelText.textContent = args.label;

  const valueEl = document.createElement("span");
  valueEl.textContent = formatNumber(args.value, args.unit);
  valueEl.style.cssText = "font-variant-numeric: tabular-nums; color: #374151;";

  labelEl.append(labelText, valueEl);

  const inputEl = document.createElement("input");
  inputEl.id = `purelms-${args.name}`;
  inputEl.type = "range";
  inputEl.min = String(args.bounds.min);
  inputEl.max = String(args.bounds.max);
  inputEl.step = String(args.bounds.step);
  inputEl.value = String(args.value);
  inputEl.disabled = !args.enabled;
  inputEl.style.width = "100%";

  // Keep the value display in sync as the slider moves.
  inputEl.addEventListener("input", () => {
    valueEl.textContent = formatNumber(parseFloat(inputEl.value), args.unit);
  });

  const helpEl = document.createElement("small");
  helpEl.textContent = args.helpText;
  helpEl.style.cssText = "color: #6b7280; font-size: 12px;";

  rowEl.append(labelEl, inputEl, helpEl);
  return { rowEl, inputEl, valueEl };
}

interface EnumSelectRowArgs {
  name: string;
  label: string;
  helpText: string;
  value: string;
  choices: EnumChoice[];
  visible: boolean;
  enabled: boolean;
}

interface EnumSelectRowResult {
  rowEl: HTMLDivElement;
  selectEl: HTMLSelectElement;
}

function buildEnumSelectRow(args: EnumSelectRowArgs): EnumSelectRowResult {
  const rowEl = document.createElement("div");
  rowEl.style.cssText = "display: flex; flex-direction: column; gap: 4px;";
  if (!args.visible) {
    rowEl.style.display = "none";
  }

  const labelEl = document.createElement("label");
  labelEl.htmlFor = `purelms-${args.name}`;
  labelEl.textContent = args.label;
  labelEl.style.cssText = "font-weight: 600; font-size: 14px;";

  const selectEl = document.createElement("select");
  selectEl.id = `purelms-${args.name}`;
  selectEl.disabled = !args.enabled;
  selectEl.style.cssText = "padding: 6px; font-size: 14px;";

  for (const choice of args.choices) {
    const opt = document.createElement("option");
    opt.value = choice.value;
    opt.textContent = choice.label;
    selectEl.append(opt);
  }
  // Setting ``selectEl.value`` AFTER appending all options is the
  // reliable cross-DOM way to pre-select. Setting ``option.selected``
  // before append doesn't propagate consistently in some DOM
  // implementations (happy-dom in particular). If the requested
  // value isn't in the choices (e.g. L2 restricted choices + a
  // stale manifest default), the browser falls back to the first
  // option, which is the safest behavior.
  selectEl.value = args.value;

  const helpEl = document.createElement("small");
  helpEl.textContent = args.helpText;
  helpEl.style.cssText = "color: #6b7280; font-size: 12px;";

  rowEl.append(labelEl, selectEl, helpEl);
  return { rowEl, selectEl };
}

function formatNumber(value: number, unit: string): string {
  // Two decimals for U-value (often e.g. 2.55), one for area.
  const digits = unit.startsWith("m²") ? 1 : 2;
  return `${value.toFixed(digits)} ${unit}`;
}

function readParameters(inputs: FormElements["inputs"]): ParameterState {
  return {
    glazing_u_value: parseFloat(inputs.glazing_u_value.value),
    window_area: parseFloat(inputs.window_area.value),
    climate_zone: inputs.climate_zone.value,
  };
}

function updateLiveValueDisplays(
  displays: FormElements["valueDisplays"],
  state: ParameterState,
): void {
  displays.glazing_u_value.textContent = formatNumber(
    state.glazing_u_value,
    "W/m²K",
  );
  displays.window_area.textContent = formatNumber(state.window_area, "m²");
}

// ---------------------------------------------------------------------
// Submit → poll → render results
// ---------------------------------------------------------------------

interface HandleSubmitArgs {
  parameters: ParameterState;
  submitButton: HTMLButtonElement;
  statusEl: HTMLElement;
  resultsEl: HTMLElement;
  helpers: MountHelpers;
}

async function handleSubmit({
  parameters,
  submitButton,
  statusEl,
  resultsEl,
  helpers,
}: HandleSubmitArgs): Promise<void> {
  submitButton.disabled = true;
  resultsEl.replaceChildren();
  statusEl.textContent = "Submitting…";

  let outcome;
  try {
    outcome = await helpers.api.submit({
      glazing_u_value: parameters.glazing_u_value,
      window_area: parameters.window_area,
      climate_zone: parameters.climate_zone,
    });
  } catch (err) {
    statusEl.textContent = `Submission failed: ${humanizeError(err)}`;
    submitButton.disabled = false;
    return;
  }

  if (outcome.is_complete || outcome.run === null) {
    // Synchronous backend (e.g. DockerCompose locally): the outcome
    // carries the terminal state already. But the API surface
    // doesn't always include the outputs on the outcome — most
    // commonly we still need to fetch the status to get the
    // populated outputs dict. The poll iterator handles both.
    if (outcome.run !== null) {
      for await (const status of helpers.api.pollStatus(outcome.run.id, {
        intervalSeconds: 1,
        maxAttempts: 2,
      })) {
        if (status.is_terminal) {
          renderTerminalResult(status, statusEl, resultsEl, helpers);
          break;
        }
      }
    } else {
      statusEl.textContent = "Done (sync, no run id).";
    }
    submitButton.disabled = false;
    return;
  }

  // Async path: poll until terminal.
  const run = outcome.run;
  statusEl.textContent = `Run ${run.id} dispatched; polling…`;

  try {
    for await (const status of helpers.api.pollStatus(run.id, {
      intervalSeconds: run.poll_interval_seconds || 2,
    })) {
      if (status.is_terminal) {
        renderTerminalResult(status, statusEl, resultsEl, helpers);
        break;
      }
      statusEl.textContent = formatProgressLine(status);
    }
  } catch (err) {
    statusEl.textContent = `Polling failed: ${humanizeError(err)}`;
  } finally {
    submitButton.disabled = false;
  }
}

function formatProgressLine(status: SimulationRunStatusResponse): string {
  const stepPart = status.progress_step ? ` — ${status.progress_step}` : "";
  return `Running: ${status.progress_pct}%${stepPart}`;
}

function renderTerminalResult(
  status: SimulationRunStatusResponse,
  statusEl: HTMLElement,
  resultsEl: HTMLElement,
  helpers: MountHelpers,
): void {
  if (status.status === "success" && status.outputs) {
    statusEl.textContent = `Run complete (${(status.runtime_seconds ?? 0).toFixed(2)}s).`;
    statusEl.style.color = "#16a34a";
    resultsEl.replaceChildren(
      buildResultCards(status.outputs as Partial<EnergyPlusOutputs>, helpers),
    );
    return;
  }
  // Non-success terminal state — render the error message(s).
  statusEl.textContent = `Run failed: ${status.status}.`;
  statusEl.style.color = "#dc2626";
  const messages = status.messages ?? [];
  const errorBox = document.createElement("div");
  errorBox.style.cssText = `
    background: #fef2f2;
    border: 1px solid #fca5a5;
    padding: 8px 12px;
    border-radius: 6px;
    color: #991b1b;
  `;
  for (const msg of messages) {
    if (msg.level !== "error") continue;
    const p = document.createElement("p");
    p.style.margin = "4px 0";
    p.textContent = `${msg.code}: ${msg.text}`;
    errorBox.append(p);
  }
  resultsEl.replaceChildren(errorBox);
}

function buildResultCards(
  outputs: Partial<EnergyPlusOutputs>,
  helpers: MountHelpers,
): HTMLDivElement {
  const grid = document.createElement("div");
  grid.style.cssText = `
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
  `;

  grid.append(
    buildValueCard("Annual heating", outputs.annual_heating_kWh, "kWh"),
    buildValueCard("Annual cooling", outputs.annual_cooling_kWh, "kWh"),
    buildValueCard("Peak heating load", outputs.peak_heating_kW, "kW"),
  );

  if (outputs.notes) {
    const notesCard = document.createElement("div");
    notesCard.style.cssText = `
      grid-column: 1 / -1;
      background: #f3f4f6;
      padding: 10px 12px;
      border-left: 3px solid #2563eb;
      border-radius: 4px;
      font-size: 14px;
      color: #374151;
    `;
    // Use textContent (never innerHTML) for safety, per ADR-0014's
    // helpers.escape rationale. helpers.escape isn't strictly needed
    // for textContent (the DOM API itself escapes), but we keep the
    // habit explicit so a future ``innerHTML`` use isn't a footgun.
    notesCard.textContent = helpers.escape(outputs.notes);
    grid.append(notesCard);
  }

  return grid;
}

function buildValueCard(
  label: string,
  value: number | undefined,
  unit: string,
): HTMLDivElement {
  const card = document.createElement("div");
  card.style.cssText = `
    background: white;
    border: 1px solid #e5e7eb;
    padding: 10px 12px;
    border-radius: 6px;
  `;

  const labelEl = document.createElement("div");
  labelEl.textContent = label;
  labelEl.style.cssText = "font-size: 12px; color: #6b7280; font-weight: 500;";

  const valueEl = document.createElement("div");
  valueEl.style.cssText =
    "font-size: 20px; font-weight: 700; color: #111827; font-variant-numeric: tabular-nums;";
  if (typeof value === "number") {
    valueEl.textContent = `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${unit}`;
  } else {
    valueEl.textContent = "—";
  }

  card.append(labelEl, valueEl);
  return card;
}

function buildUnavailableNotice(helpers: MountHelpers): HTMLDivElement {
  const box = document.createElement("div");
  box.style.cssText = `
    background: #fef3c7;
    border: 1px solid #fbbf24;
    padding: 12px 16px;
    border-radius: 6px;
    color: #92400e;
  `;
  box.textContent = helpers.escape(
    "This InteractiveTask is no longer available. The instructor may have deactivated it; check back later or contact your instructor.",
  );
  return box;
}

function humanizeError(err: unknown): string {
  if (err instanceof Error) return err.message;
  if (typeof err === "object" && err !== null && "detail" in err) {
    return String((err as { detail: unknown }).detail);
  }
  return String(err);
}
