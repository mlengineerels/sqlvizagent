import { fetchQuery } from "./api.js";
import { ui, renderTableSuggestions } from "./ui.js";
import { applyResponse, buildAssistantMetadata } from "./results.js";
import { addLocalMessage, ensureActiveChat, loadChats, selectChat, updateActiveChatTitle } from "./chat.js";

function findLastResultMessage() {
  return [...(ui.state.messages || [])]
    .reverse()
    .find((msg) => msg.role === "assistant" && msg.metadata && msg.metadata.result_snapshot);
}

export async function runQuery(
  question,
  tablesOverride = [],
  planOnly = false,
  options = {}
) {
  const { forceNew = false } = options || {};
  const chatId = await ensureActiveChat(question);
  updateActiveChatTitle(question);
  ui.state.lastQuestion = question;
  ui.state.lastTablesUsed = tablesOverride;
  const lastResult = findLastResultMessage();
  const hasPinnedContext = !!ui.state.activeContextMessageId;
  const hasContext = (Boolean(chatId && lastResult) || hasPinnedContext) && !forceNew;

  addLocalMessage({
    role: "user",
    content: question,
    created_at: new Date().toISOString(),
    metadata: {
      execute: !planOnly,
      plan_only: planOnly,
      tables_override: tablesOverride,
    },
  });

  ui.setLoading(true);
  if (hasContext) {
    ui.setStatus("Working on your follow-up...");
  } else {
    ui.reset();
    renderTableSuggestions([], { reason: "Finding relevant tables..." });
  }

  try {
    const data = await fetchQuery(question, {
      tables: tablesOverride,
      planOnly,
      chatId,
      forceNew,
      contextMessageId: ui.state.activeContextMessageId,
    });
    if (!data) throw new Error("No result returned.");
    if (data.action === "interpret") {
      addLocalMessage({
        role: "assistant",
        content: data.assistant_text || "Explanation",
        created_at: new Date().toISOString(),
        metadata: {
          kind: "interpretation",
          source_message_id: lastResult?.id || null,
        },
      });
      await loadChats({ selectIfMissing: false });
      await selectChat(chatId);
      return;
    }

    if (
      data.action === "refine_failed" ||
      data.action === "interpret_failed" ||
      data.action === "context_missing"
    ) {
      if (data.action === "context_missing") {
        ui.clearActiveContext();
      }
      addLocalMessage({
        role: "assistant",
        content: data.assistant_text || "Follow-up failed.",
        created_at: new Date().toISOString(),
        metadata: {
          kind: "followup_failed",
          fallback_question: data.fallback_question || question,
          action: data.action,
        },
      });
      await loadChats({ selectIfMissing: false });
      await selectChat(chatId);
      return;
    }

    if (hasContext) ui.reset();
    const intent = data.intent || "";
    applyResponse(data, intent, tablesOverride, planOnly);

    addLocalMessage({
      role: "assistant",
      content: data.sql || "Result",
      created_at: new Date().toISOString(),
      metadata: buildAssistantMetadata(data, intent, planOnly),
    });

    await loadChats({ selectIfMissing: false });
    await selectChat(chatId);
  } catch (err) {
    ui.setStatus(err.message || "Something went wrong.");
  } finally {
    ui.setLoading(false);
  }
}

export async function handleSend(tablesOverride = []) {
  const question = ui.elements.question.value.trim();
  if (!question) return;
  const planOnly = !!(ui.elements.planOnly && ui.elements.planOnly.checked);
  ui.elements.question.value = "";
  await runQuery(question, tablesOverride, planOnly);
}

export async function handleApplyTables() {
  if (!ui.state.lastQuestion) {
    ui.showToast("Ask a question first.", "error");
    return;
  }
  const selected = Array.from(ui.state.selectedTables || []);
  if (!selected.length) {
    ui.showToast("Select at least one table.", "error");
    return;
  }
  const planOnly = !!(ui.elements.planOnly && ui.elements.planOnly.checked);
  ui.reset("Re-running with selected tables…");
  renderTableSuggestions(selected, { reason: `Using your selected tables: ${selected.join(", ")}` });
  await runQuery(ui.state.lastQuestion, selected, planOnly);
}

export function handleClear() {
  ui.setLoading(false);
  ui.reset("");
  ui.elements.question.value = "";
  ui.elements.question.focus();
  ui.setStatus("Cleared");
}

export async function copyContent(text, label) {
  if (!text || !text.trim()) {
    ui.showToast(`No ${label} to copy.`, "error");
    return;
  }
  if (!navigator.clipboard) {
    ui.showToast("Clipboard not available in this browser.", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    ui.showToast(`${label} copied!`, "success");
  } catch (err) {
    ui.showToast(`Failed to copy ${label}.`, "error");
  }
}

ui.actions = ui.actions || {};
ui.actions.runQuery = runQuery;
