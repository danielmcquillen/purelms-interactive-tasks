/**
 * Frontend-side constants mirroring the manifest's enum + default
 * values.
 *
 * Why duplicate from the manifest: the LMS injects manifest
 * metadata via the ``config`` arg in v2, but in v1 the bundle is
 * responsible for knowing its own defaults + choices. Keep these
 * in sync with ``interactive_task.yaml`` manually — if the manifest
 * adds a climate zone, add the matching entry here too.
 */

/** Parameter defaults (mirrors manifest's ``parameters[].default``). */
export const DEFAULT_GLAZING_U_VALUE = 2.5;
export const DEFAULT_WINDOW_AREA = 5.0;
export const DEFAULT_CLIMATE_ZONE = "5A";

/** Numeric parameter bounds (mirrors manifest's ``parameters[].min/max/step``). */
export const GLAZING_U_VALUE_MIN = 0.5;
export const GLAZING_U_VALUE_MAX = 6.0;
export const GLAZING_U_VALUE_STEP = 0.1;
export const WINDOW_AREA_MIN = 1.0;
export const WINDOW_AREA_MAX = 20.0;
export const WINDOW_AREA_STEP = 0.5;

/** Climate zone metadata. Matches the manifest's ``choices`` entries
 * plus a frontend-only ``wallTintHex`` for the 3D scene. */
export interface ClimateZoneMeta {
  value: string;
  label: string;
  wallTintHex: number;
}

export const CLIMATE_DATA: Record<string, ClimateZoneMeta> = {
  "4A": {
    value: "4A",
    label: "4A Mixed-humid (e.g. New York City)",
    wallTintHex: 0xd6cfc4, // warmer / sandy
  },
  "5A": {
    value: "5A",
    label: "5A Cool-humid (e.g. Chicago)",
    wallTintHex: 0xc7cfd6, // neutral gray-blue
  },
  "6A": {
    value: "6A",
    label: "6A Cold-humid (e.g. Minneapolis)",
    wallTintHex: 0xb9c6d4, // cooler / cold blue-gray
  },
};

export const CLIMATE_CHOICES: ClimateZoneMeta[] = [
  CLIMATE_DATA["4A"]!,
  CLIMATE_DATA["5A"]!,
  CLIMATE_DATA["6A"]!,
];
