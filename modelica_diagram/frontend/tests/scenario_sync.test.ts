/**
 * Drift guard: the frontend vendors copies of ``scenario.json`` and the
 * ``purelms.diagram.v1`` schema so the bundle is self-contained. These tests
 * fail if a vendored copy diverges from its source — forcing a re-vendor
 * rather than letting the canvas grade against a stale palette/schema.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readJson(relativePath: string): unknown {
  return JSON.parse(readFileSync(new URL(relativePath, import.meta.url), "utf8"));
}

describe("vendored data stays in sync with its source", () => {
  it("scenario.json matches the backend's single source", () => {
    const vendored = readJson("../src/vendor/hydronic_loop.scenario.json");
    const source = readJson("../../backend/scenarios/hydronic_loop/scenario.json");
    expect(vendored).toEqual(source);
  });

  it("diagram.v1 schema matches purelms-shared's canonical copy", () => {
    const vendored = readJson("../src/vendor/diagram.v1.schema.json");
    const source = readJson(
      "../../../../purelms-shared/purelms_shared/schemas/diagram.v1.schema.json",
    );
    expect(vendored).toEqual(source);
  });
});
