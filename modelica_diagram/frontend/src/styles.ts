/**
 * Styles for the modelica_diagram canvas, injected once per document.
 *
 * ``DRAWFLOW_CSS`` is vendored verbatim from ``drawflow@0.0.60``
 * (``dist/drawflow.min.css``) so the bundle is self-contained — no CSS-loader
 * config, and it works identically under esbuild and vitest. ``TASK_CSS`` adds
 * the palette / panel / node / result styling on top.
 */

const DRAWFLOW_CSS = `.drawflow,.drawflow .parent-node{position:relative}.parent-drawflow{display:flex;overflow:hidden;touch-action:none;outline:0}.drawflow{width:100%;height:100%;user-select:none;perspective:0}.drawflow .drawflow-node{display:flex;align-items:center;position:absolute;background:#0ff;width:160px;min-height:40px;border-radius:4px;border:2px solid #000;color:#000;z-index:2;padding:15px}.drawflow .drawflow-node.selected{background:red}.drawflow .drawflow-node:hover{cursor:move}.drawflow .drawflow-node .inputs,.drawflow .drawflow-node .outputs{width:0}.drawflow .drawflow-node .drawflow_content_node{width:100%;display:block}.drawflow .drawflow-node .input,.drawflow .drawflow-node .output{position:relative;width:20px;height:20px;background:#fff;border-radius:50%;border:2px solid #000;cursor:crosshair;z-index:1;margin-bottom:5px}.drawflow .drawflow-node .input{left:-27px;top:2px;background:#ff0}.drawflow .drawflow-node .output{right:-3px;top:2px}.drawflow svg{z-index:0;position:absolute;overflow:visible!important}.drawflow .connection{position:absolute;pointer-events:none;aspect-ratio:1/1}.drawflow .connection .main-path{fill:none;stroke-width:5px;stroke:#4682b4;pointer-events:all}.drawflow .connection .main-path:hover{stroke:#1266ab;cursor:pointer}.drawflow .connection .main-path.selected{stroke:#43b993}.drawflow .connection .point{cursor:move;stroke:#000;stroke-width:2;fill:#fff;pointer-events:all}.drawflow .connection .point.selected,.drawflow .connection .point:hover{fill:#1266ab}.drawflow .main-path{fill:none;stroke-width:5px;stroke:#4682b4}.drawflow-delete{position:absolute;display:block;width:30px;height:30px;background:#000;color:#fff;z-index:4;border:2px solid #fff;line-height:30px;font-weight:700;text-align:center;border-radius:50%;font-family:monospace;cursor:pointer}.drawflow>.drawflow-delete{margin-left:-15px;margin-top:15px}.parent-node .drawflow-delete{right:-15px;top:-15px}`;

const TASK_CSS = `
.mdl-task{display:flex;flex-wrap:wrap;gap:16px;align-items:stretch;font-family:system-ui,-apple-system,sans-serif}
.mdl-sidebar{flex:1 1 280px;min-width:260px;display:flex;flex-direction:column;gap:14px}
.mdl-stage{flex:2 1 420px;min-width:320px;display:flex;flex-direction:column;gap:8px}
.mdl-palette{display:flex;flex-wrap:wrap;gap:6px}
.mdl-palette button{padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;cursor:pointer;font-size:13px;font-weight:600;color:#1e293b}
.mdl-palette button:hover{background:#eef2ff;border-color:#6366f1}
.mdl-canvas{position:relative;height:440px;border:1px solid #e2e8f0;border-radius:8px;background:#f8fafc;background-image:radial-gradient(#e2e8f0 1px,transparent 1px);background-size:18px 18px}
.mdl-toolbar{display:flex;gap:8px;align-items:center}
.mdl-hint{font-size:12px;color:#64748b}
.mdl-field{display:flex;flex-direction:column;gap:4px}
.mdl-field label{display:flex;justify-content:space-between;font-weight:600;font-size:14px;color:#334155}
.mdl-field input[type=range]{width:100%}
.mdl-field .mdl-val{font-variant-numeric:tabular-nums;color:#475569}
.mdl-run{padding:9px 16px;background:#2563eb;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer;align-self:flex-start}
.mdl-run:disabled{opacity:.6;cursor:default}
.mdl-secondary{padding:6px 10px;background:#fff;color:#334155;border:1px solid #cbd5e1;border-radius:6px;cursor:pointer;font-size:13px}
.mdl-status{min-height:1.4em;font-size:13px;color:#64748b}
.mdl-results{display:flex;flex-direction:column;gap:10px}
.mdl-verdict{padding:8px 12px;border-radius:6px;font-weight:600}
.mdl-verdict.ok{background:#dcfce7;color:#166534;border:1px solid #86efac}
.mdl-verdict.no{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}
.mdl-msgs{margin:0;padding-left:18px;font-size:13px;color:#475569}
.mdl-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px}
.mdl-card{background:#fff;border:1px solid #e5e7eb;border-radius:6px;padding:8px 10px}
.mdl-card .k{font-size:11px;color:#6b7280;font-weight:500}
.mdl-card .v{font-size:18px;font-weight:700;color:#111827;font-variant-numeric:tabular-nums}
.mdl-chart{width:100%;height:120px;border:1px solid #e5e7eb;border-radius:6px;background:#fff}
.mdl-notice{background:#fef3c7;border:1px solid #fbbf24;padding:12px 16px;border-radius:6px;color:#92400e}
.drawflow .drawflow-node.mdl-node{background:#fff;border:1px solid #94a3b8;width:150px;min-height:34px;padding:10px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.drawflow .drawflow-node.mdl-node.selected{border-color:#2563eb;background:#eff6ff}
.mdl-node-title{font-weight:600;font-size:13px;color:#0f172a}
.mdl-node-ports{font-size:10px;color:#64748b;margin-top:3px;line-height:1.3}
`;

export const STYLES = DRAWFLOW_CSS + TASK_CSS;

/** Inject the stylesheet once (idempotent per document). */
export function injectStyles(doc: Document, id = "modelica-diagram-styles"): void {
  if (doc.getElementById(id)) {
    return;
  }
  const style = doc.createElement("style");
  style.id = id;
  style.textContent = STYLES;
  doc.head.append(style);
}
