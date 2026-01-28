import { ui, renderTableSuggestions } from "./ui.js";

export function applyResponse(data, intent, tablesUsed, planOnly) {
  ui.setSQL(data.sql || "—");
  ui.renderRows(data.rows || [], intent);
  ui.renderFigure(data.figure, intent);

  ui.state.plan = data.plan || [];
  ui.renderTrace(data.trace || []);
  if (planOnly) {
    ui.state.rows = [];
    if (ui.elements.rows) {
      ui.elements.rows.textContent = "Plan only mode: execution skipped.";
    }
    if (ui.elements.chart) {
      ui.elements.chart.innerHTML = "Plan only mode: no figure.";
    }
  }

  ui.state.plan = data.plan || [];
  ui.state.durationMs = data.duration_ms || null;
  const duration = ui.state.durationMs ? ` • ${ui.state.durationMs} ms` : "";
  const mode = planOnly ? " • plan only" : "";
  const notes = (data.notes || []).join("; ");
  const notesText = notes ? ` • ${notes}` : "";
  ui.setStatus(`Intent: ${intent || "unknown"}${mode}${duration}${notesText}`);
  renderTableSuggestions(data.suggested_tables || [], {
    reason:
      tablesUsed && tablesUsed.length
        ? `Using your selected tables: ${tablesUsed.join(", ")}`
        : "",
  });
}

export function buildAssistantMetadata(data, intent, planOnly) {
  return {
    sql: data.sql || "",
    rows: data.rows || [],
    figure: data.figure || null,
    intent: intent || "",
    suggested_tables: data.suggested_tables || [],
    trace: data.trace || [],
    plan: data.plan || [],
    duration_ms: data.duration_ms || null,
    notes: data.notes || [],
    plan_only: !!planOnly,
  };
}

export function loadStoredResult(metadata) {
  const intent = metadata.intent || "retrieval";
  ui.setSQL(metadata.sql || "—");
  ui.renderRows(metadata.rows || [], intent);
  ui.renderFigure(metadata.figure, intent);
  ui.state.plan = metadata.plan || [];
  ui.renderTrace(metadata.trace || []);
  ui.state.durationMs = metadata.duration_ms || null;
  const duration = ui.state.durationMs ? ` • ${ui.state.durationMs} ms` : "";
  const notes = (metadata.notes || []).join("; ");
  const notesText = notes ? ` • ${notes}` : "";
  ui.setStatus(`Intent: ${intent}${duration}${notesText}`);
  renderTableSuggestions(metadata.suggested_tables || [], { reason: "Loaded from history." });
}
