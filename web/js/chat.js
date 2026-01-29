import { apiRequest, sendFeedback, fetchReplay } from "./api.js";
import { ui } from "./ui.js";
import { loadStoredResult } from "./results.js";

function formatChatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function buildChatTitle(text) {
  const words = String(text || "").trim().split(/\s+/).slice(0, 6);
  return words.join(" ") || "New chat";
}

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function closeChatMenus() {
  document.querySelectorAll(".chat-menu.open").forEach((menu) => menu.classList.remove("open"));
}

export function renderChatHistory(messages) {
  const container = ui.elements.chatHistory;
  if (!container) return;
  const list = messages || [];
  ui.state.messages = list;
  container.innerHTML = "";
  if (ui.elements.historyCount) {
    const count = list.length;
    ui.elements.historyCount.textContent = count ? `• ${count} message${count > 1 ? "s" : ""}` : "";
  }

  if (!list.length) {
    container.innerHTML = '<div class="muted">No messages yet.</div>';
    return;
  }

  list.forEach((msg) => {
    const bubble = document.createElement("div");
    const role = msg.role === "user" ? "user" : "assistant";
    bubble.className = `message message-${role}`;

    const header = document.createElement("div");
    header.className = "message-header";
    const who = document.createElement("span");
    who.textContent = role === "user" ? "You" : "Assistant";
    const when = document.createElement("span");
    when.textContent = formatChatTime(msg.created_at);
    header.appendChild(who);
    header.appendChild(when);

    const body = document.createElement("div");
    body.className = "message-content";
    body.textContent = msg.content || "";

    bubble.appendChild(header);
    bubble.appendChild(body);

    if (role === "assistant" && msg.metadata) {
      const meta = document.createElement("div");
      meta.className = "message-meta";
      const kind = msg.metadata.kind || "";
      if (kind === "interpretation") {
        meta.textContent = "Explanation of the last result";
        bubble.appendChild(meta);
      } else if (kind === "followup_failed") {
        meta.textContent = "Follow-up could not be applied.";
        bubble.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "message-actions";
        const retryQuestion = msg.metadata.fallback_question;
        if (retryQuestion && ui.actions && typeof ui.actions.runQuery === "function") {
          const retryBtn = document.createElement("button");
          retryBtn.className = "ghost small";
          retryBtn.textContent = "Run as new query";
          retryBtn.addEventListener("click", async (event) => {
            event.stopPropagation();
            ui.elements.question.value = retryQuestion;
            await ui.actions.runQuery(retryQuestion, [], false, { forceNew: true });
          });
          actions.appendChild(retryBtn);
        }
        bubble.appendChild(actions);
      } else {
        const snapshotCount = msg.metadata.result_snapshot?.row_count;
        const rowCount = Number.isFinite(snapshotCount)
          ? snapshotCount
          : Array.isArray(msg.metadata.rows)
            ? msg.metadata.rows.length
            : 0;
        const intent = msg.metadata.intent || "unknown";
        meta.textContent = `Intent: ${intent} • Rows: ${rowCount}`;
        bubble.appendChild(meta);

        const actions = document.createElement("div");
        actions.className = "message-actions";
        const loadBtn = document.createElement("button");
        loadBtn.className = "ghost small";
        loadBtn.textContent = "Load result";
        loadBtn.addEventListener("click", (event) => {
          event.stopPropagation();
          loadStoredResult(msg.metadata);
          ui.setStatus("Loaded result from history.");
        });
        actions.appendChild(loadBtn);

        if (msg.id && ui.state.activeChatId) {
          const upBtn = document.createElement("button");
          upBtn.className = "ghost small";
          upBtn.textContent = "+1";
          upBtn.addEventListener("click", async (event) => {
            event.stopPropagation();
            try {
              await sendFeedback(ui.state.activeChatId, msg.id, 1);
              ui.showToast("Thanks for the feedback!");
            } catch (err) {
              ui.showToast(err.message || "Feedback failed", "error");
            }
          });
          actions.appendChild(upBtn);

          const downBtn = document.createElement("button");
          downBtn.className = "ghost small";
          downBtn.textContent = "-1";
          downBtn.addEventListener("click", async (event) => {
            event.stopPropagation();
            try {
              await sendFeedback(ui.state.activeChatId, msg.id, -1);
              ui.showToast("Thanks for the feedback!");
            } catch (err) {
              ui.showToast(err.message || "Feedback failed", "error");
            }
          });
          actions.appendChild(downBtn);
        }
        bubble.appendChild(actions);
      }
    }

    container.appendChild(bubble);
  });
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
  });
}

export function renderChatList(chats) {
  const listEl = ui.elements.chatList;
  if (!listEl) return;
  const query = (ui.elements.chatSearch && ui.elements.chatSearch.value || "").toLowerCase();
  const items = (chats || []).filter((chat) =>
    !query || String(chat.title || "").toLowerCase().includes(query)
  );

  listEl.innerHTML = "";
  if (!items.length) {
    listEl.innerHTML = '<div class="muted small">No chats found.</div>';
    return;
  }

  items.forEach((chat) => {
    const item = document.createElement("div");
    item.className = `chat-item${chat.id === ui.state.activeChatId ? " active" : ""}`;

    const meta = document.createElement("div");
    meta.className = "chat-meta";
    const title = document.createElement("div");
    title.className = "chat-title";
    title.textContent = chat.title || "Untitled";
    const time = document.createElement("div");
    time.className = "chat-time";
    time.textContent = formatChatTime(chat.updated_at);
    meta.appendChild(title);
    meta.appendChild(time);

    const actions = document.createElement("div");
    actions.className = "chat-actions";
    const menuButton = document.createElement("button");
    menuButton.className = "chat-menu-button";
    menuButton.textContent = "⋯";

    const menu = document.createElement("div");
    menu.className = "chat-menu";
    const renameBtn = document.createElement("button");
    renameBtn.textContent = "Rename";
    renameBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      closeChatMenus();
      const nextTitle = window.prompt("Rename chat", chat.title || "");
      if (!nextTitle) return;
      try {
        await apiRequest(`/api/chats/${chat.id}`, {
          method: "PATCH",
          body: JSON.stringify({ title: nextTitle }),
        });
        await loadChats({ selectIfMissing: false });
      } catch (err) {
        ui.showToast(err.message || "Rename failed", "error");
      }
    });
    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      closeChatMenus();
      const confirmed = window.confirm("Delete this chat?");
      if (!confirmed) return;
      try {
        await apiRequest(`/api/chats/${chat.id}`, { method: "DELETE" });
        if (chat.id === ui.state.activeChatId) {
          ui.state.activeChatId = null;
          ui.state.messages = [];
          renderChatHistory([]);
          ui.reset("Chat deleted.", { clearHistory: false });
        }
        await loadChats({ selectIfMissing: true });
      } catch (err) {
        ui.showToast(err.message || "Delete failed", "error");
      }
    });
    menu.appendChild(renameBtn);
    menu.appendChild(deleteBtn);
    // Replay JSON export is temporarily disabled.
    // const replayBtn = document.createElement("button");
    // replayBtn.textContent = "Replay JSON";
    // replayBtn.addEventListener("click", async (event) => {
    //   event.stopPropagation();
    //   closeChatMenus();
    //   try {
    //     const data = await fetchReplay(chat.id);
    //     downloadJson(data, `chat_${chat.id}_replay.json`);
    //     ui.showToast("Replay downloaded.");
    //   } catch (err) {
    //     ui.showToast(err.message || "Replay failed", "error");
    //   }
    // });
    // menu.appendChild(replayBtn);

    menuButton.addEventListener("click", (event) => {
      event.stopPropagation();
      closeChatMenus();
      menu.classList.toggle("open");
    });

    actions.appendChild(menuButton);
    actions.appendChild(menu);

    item.appendChild(meta);
    item.appendChild(actions);
    item.addEventListener("click", () => {
      selectChat(chat.id);
    });

    listEl.appendChild(item);
  });
}

export async function loadChats({ selectIfMissing = true } = {}) {
  try {
    const chats = await apiRequest("/api/chats");
    ui.state.chats = chats || [];
    renderChatList(ui.state.chats);
    const activeExists = ui.state.activeChatId &&
      ui.state.chats.some((chat) => chat.id === ui.state.activeChatId);
    if (!activeExists) {
      ui.state.activeChatId = null;
    }
    if (selectIfMissing && !ui.state.activeChatId && ui.state.chats.length) {
      await selectChat(ui.state.chats[0].id);
    }
    if (!ui.state.chats.length) {
      renderChatHistory([]);
      ui.setStatus("No chats yet. Ask a question to start one.");
    }
  } catch (err) {
    ui.showToast(err.message || "Failed to load chats", "error");
  }
}

export async function selectChat(chatId) {
  try {
    const data = await apiRequest(`/api/chats/${chatId}`);
    ui.state.activeChatId = chatId;
    ui.state.messages = data.messages || [];
    renderChatList(ui.state.chats);
    renderChatHistory(ui.state.messages);
    const last = [...ui.state.messages].reverse().find((m) => m.role === "assistant" && m.metadata);
    if (last && last.metadata) {
      loadStoredResult(last.metadata);
    } else {
      ui.reset("Chat loaded.", { clearHistory: false });
    }
  } catch (err) {
    ui.showToast(err.message || "Failed to load chat", "error");
  }
}

export async function ensureActiveChat(question) {
  if (ui.state.activeChatId) return ui.state.activeChatId;
  const title = buildChatTitle(question);
  const chat = await apiRequest("/api/chats", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
  ui.state.activeChatId = chat.id;
  ui.state.chats = [chat, ...(ui.state.chats || [])];
  renderChatList(ui.state.chats);
  renderChatHistory([]);
  return chat.id;
}

export function updateActiveChatTitle(question) {
  if (!ui.state.activeChatId) return;
  const nextTitle = buildChatTitle(question);
  if (!nextTitle) return;
  const updated = (ui.state.chats || []).map((chat) => {
    if (chat.id !== ui.state.activeChatId) return chat;
    if (String(chat.title || "").trim().toLowerCase() !== "new chat") return chat;
    return { ...chat, title: nextTitle };
  });
  ui.state.chats = updated;
  renderChatList(ui.state.chats);
}

export function addLocalMessage(message) {
  ui.state.messages = [...(ui.state.messages || []), message];
  renderChatHistory(ui.state.messages);
}

export async function handleNewChat() {
  try {
    const chat = await apiRequest("/api/chats", {
      method: "POST",
      body: JSON.stringify({ title: "New chat" }),
    });
    ui.state.activeChatId = chat.id;
    ui.state.messages = [];
    ui.state.lastQuestion = "";
    ui.state.lastTablesUsed = [];
    renderChatHistory([]);
    ui.reset("New chat created.", { clearHistory: false });
    await loadChats({ selectIfMissing: false });
  } catch (err) {
    ui.showToast(err.message || "Failed to create chat", "error");
  }
}
