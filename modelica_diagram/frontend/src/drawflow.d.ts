/**
 * Ambient declaration for ``drawflow@0.0.60`` — the package ships no types.
 * Only the surface the canvas uses is declared; ``export()`` is ``unknown``
 * (we cast it to ``DrawflowExport`` at the call site, then serialise).
 */
declare module "drawflow" {
  export default class Drawflow {
    constructor(container: HTMLElement, render?: unknown, parent?: unknown);
    reflow: string;
    editor_mode: string;
    reroute: boolean;
    reroute_fix_curvature: boolean;
    reroute_curvature: number;
    start(): void;
    addNode(
      name: string,
      inputs: number,
      outputs: number,
      posX: number,
      posY: number,
      className: string,
      data: object,
      html: string,
      typenode?: boolean,
    ): number;
    export(): unknown;
    clear(): void;
    removeNodeId(id: string): void;
    on(event: string, callback: (...args: unknown[]) => void): void;
    getNodeFromId(
      id: string | number,
    ): { data?: { type?: string } & Record<string, unknown> } | undefined;
    addConnection(
      idOutput: string | number,
      idInput: string | number,
      outputClass: string,
      inputClass: string,
    ): void;
    import(data: unknown): void;
  }
}
