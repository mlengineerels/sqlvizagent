import { handleApplyTables, handleClear, handleSend, handleNewQuery, copyContent } from "./actions.js";
import { closeChatMenus, handleNewChat, loadChats, renderChatList } from "./chat.js";
import { ui, updatePagination } from "./ui.js";

const SESSION_KEY = "nl2sql-session-id";
const HISTORY_OPEN_KEY = "nl2sql-history-open";

function ensureSessionId() {
  try {
    const existing = localStorage.getItem(SESSION_KEY);
    if (existing) return existing;
    const generated = crypto.randomUUID
      ? crypto.randomUUID()
      : `sess_${Math.random().toString(16).slice(2)}${Date.now()}`;
    localStorage.setItem(SESSION_KEY, generated);
    return generated;
  } catch (_err) {
    return `sess_${Date.now()}`;
  }
}

ui.state.sessionId = ensureSessionId();

ui.elements.copySql.addEventListener("click", () => {
  copyContent(ui.state.sql, "SQL");
});

ui.elements.copyRows.addEventListener("click", () => {
  const rowsText = ui.state.rows && ui.state.rows.length
    ? JSON.stringify(ui.state.rows, null, 2)
    : "";
  copyContent(rowsText, "rows");
});

ui.elements.pagePrev.addEventListener("click", () => {
  if (ui.state.page > 1) {
    ui.state.page -= 1;
    ui.renderRows(ui.state.rows, "retrieval", { resetSort: false });
  }
});

ui.elements.pageNext.addEventListener("click", () => {
  const totalPages = Math.ceil((ui.state.rows.length || 0) / ui.state.pageSize);
  if (totalPages === 0) return;
  if (ui.state.page < totalPages) {
    ui.state.page += 1;
    ui.renderRows(ui.state.rows, "retrieval", { resetSort: false });
  }
});

ui.elements.clear.addEventListener("click", handleClear);
ui.elements.send.addEventListener("click", () => handleSend([]));
ui.elements.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !ui.elements.send.disabled) handleSend([]);
});
if (ui.elements.newQuery) ui.elements.newQuery.addEventListener("click", () => handleNewQuery([]));
ui.elements.applyTables.addEventListener("click", handleApplyTables);
if (ui.elements.newChat) ui.elements.newChat.addEventListener("click", handleNewChat);
if (ui.elements.chatSearch) {
  ui.elements.chatSearch.addEventListener("input", () => renderChatList(ui.state.chats));
}

document.addEventListener("click", closeChatMenus);

updatePagination(0);
loadChats();
if (ui.elements.historyDetails) {
  try {
    const saved = localStorage.getItem(HISTORY_OPEN_KEY);
    if (saved === "false") ui.elements.historyDetails.open = false;
  } catch (_err) {
    // ignore storage failures
  }
  ui.elements.historyDetails.addEventListener("toggle", () => {
    try {
      localStorage.setItem(HISTORY_OPEN_KEY, String(ui.elements.historyDetails.open));
    } catch (_err) {
      // ignore storage failures
    }
  });
}
