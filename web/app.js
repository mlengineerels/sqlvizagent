const ui = (() => {
  const elements = {
    question: document.getElementById("question"),
    send: document.getElementById("send"),
    status: document.getElementById("status"),
    chatList: document.getElementById("chat-list"),
    newChat: document.getElementById("new-chat"),
    chatSearch: document.getElementById("chat-search"),
    chatHistory: document.getElementById("chat-history"),
    historyDetails: document.getElementById("history-details"),
    historyCount: document.getElementById("history-count"),
    sqlDetails: document.getElementById("sql-details"),
    sql: document.getElementById("sql"),
    rowsDetails: document.getElementById("rows-details"),
    rows: document.getElementById("rows"),
    figureDetails: document.getElementById("figure-details"),
    chart: document.getElementById("chart"),
    copySql: document.getElementById("copy-sql"),
    copyRows: document.getElementById("copy-rows"),
    clear: document.getElementById("clear"),
    planOnly: document.getElementById("plan-only"),
    pagePrev: document.getElementById("page-prev"),
    pageNext: document.getElementById("page-next"),
    pageInfo: document.getElementById("page-info"),
    tablesDetails: document.getElementById("tables-details"),
    tablesInfo: document.getElementById("tables-info"),
    tablesChips: document.getElementById("tables-chips"),
    applyTables: document.getElementById("apply-tables"),
    traceDetails: document.getElementById("trace-details"),
    trace: document.getElementById("trace"),
  };

  const state = {
    sql: "",
    rows: [],
    figure: null,
    sortBy: null,
    sortDir: "asc",
    page: 1,
    pageSize: 20,
    lastQuestion: "",
    lastTablesUsed: [],
    suggestedTables: [],
    selectedTables: new Set(),
    sessionId: null,
    trace: [],
    plan: [],
    durationMs: null,
    chats: [],
    activeChatId: null,
    messages: [],
  };
  const toast = document.getElementById("toast");
  let toastTimer = null;

  const setStatus = (text) => {
    elements.status.textContent = text || "";
  };

  const showToast = (message, variant = "success") => {
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast ${variant}`;
    requestAnimationFrame(() => {
      toast.classList.add("show");
    });
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      toast.classList.remove("show");
    }, 2000);
  };

  const setSQL = (sql, { store = true } = {}) => {
    if (store) state.sql = sql || "";
    elements.sql.textContent = sql || "—";
    elements.sqlDetails.open = false;
  };

  const clearRows = () => {
    elements.rows.textContent = "—";
    elements.rowsDetails.open = false;
    state.rows = [];
    updatePagination(0);
  };

  const clearFigure = (message = "No figure yet.") => {
    elements.chart.innerHTML = message;
    elements.figureDetails.open = false;
    state.figure = null;
  };

  const renderTable = (rows) => {
    const data = rows || [];
    if (!data.length) {
      elements.rows.innerHTML = '<div class="muted">No rows returned.</div>';
      updatePagination(0);
      return;
    }

    const columns = Array.from(
      data.reduce((set, row) => {
        Object.keys(row || {}).forEach((k) => set.add(k));
        return set;
      }, new Set())
    );

    const totalPages = Math.max(1, Math.ceil(data.length / state.pageSize));
    if (state.page > totalPages) state.page = totalPages;
    const startIdx = (state.page - 1) * state.pageSize;
    const endIdx = startIdx + state.pageSize;
    const sorted = sortRows(data);
    const pageRows = sorted.slice(startIdx, endIdx);

    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      const isSorted = state.sortBy === col;
      const arrow = isSorted ? (state.sortDir === "asc" ? " ↑" : " ↓") : "";
      th.textContent = col + arrow;
      th.style.cursor = "pointer";
      th.addEventListener("click", () => handleSort(col));
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    pageRows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        const value = row[col];
        td.textContent = value === null || value === undefined ? "—" : value;
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    wrap.appendChild(table);

    elements.rows.innerHTML = "";
    elements.rows.appendChild(wrap);
    updatePagination(totalPages);
  };

  const renderRows = (rows, intent, { resetSort = true } = {}) => {
    if (intent === "visualization") {
      elements.rows.textContent = "Visualization shown below.";
      elements.rowsDetails.open = false;
      state.rows = [];
      if (resetSort) {
        state.sortBy = null;
        state.sortDir = "asc";
        state.page = 1;
      }
      return;
    }
    state.rows = rows || [];
    if (resetSort) {
      state.sortBy = null;
      state.sortDir = "asc";
      state.page = 1;
    }
    renderTable(state.rows);
  };

  const renderFigure = (figure, intent) => {
    if (!figure) {
      clearFigure("No figure returned.");
      return;
    }

    const layout = Object.assign(
      {
        autosize: true,
        margin: { l: 50, r: 30, t: 50, b: 50 },
        paper_bgcolor: intent === "visualization" ? "white" : "rgba(0,0,0,0)",
        plot_bgcolor: intent === "visualization" ? "white" : "rgba(0,0,0,0)",
      },
      figure.layout || {}
    );

    elements.chart.innerHTML = "";
    const wrapper = document.createElement("div");
    wrapper.className = "chart-wrapper";
    elements.chart.appendChild(wrapper);
    Plotly.newPlot(wrapper, figure.data || [], layout, { displaylogo: false, responsive: true });
    state.figure = figure;
  };

  const renderTrace = (trace) => {
    const list = trace || [];
    state.trace = list;
    if (!elements.trace) return;
    if (!list.length) {
      elements.trace.textContent = "—";
      if (elements.traceDetails) elements.traceDetails.open = false;
      return;
    }
    const planned = (state.plan || [])
      .map((p) => p.tool || p.description || "")
      .filter(Boolean)
      .join(" → ");
    const ul = document.createElement("ul");
    ul.className = "trace-list";
    list.forEach((t) => {
      const li = document.createElement("li");
      li.className = `trace-item status-${t.status || "unknown"}`;
      const status = t.status || "";
      const label = `${t.step || ""} · ${t.tool || ""} · ${status}`;
      const detail = t.detail || "";
      const strong = document.createElement("strong");
      strong.textContent = label;
      li.appendChild(strong);
      if (detail) {
        const span = document.createElement("span");
        span.textContent = ` — ${detail}`;
        li.appendChild(span);
      }
      ul.appendChild(li);
    });
    elements.trace.innerHTML = "";
    if (planned) {
      const planDiv = document.createElement("div");
      planDiv.className = "trace-plan muted";
      planDiv.textContent = `Plan: ${planned}`;
      elements.trace.appendChild(planDiv);
    }
    elements.trace.appendChild(ul);
    if (elements.traceDetails) elements.traceDetails.open = true;
  };

  const setLoading = (isLoading) => {
    elements.send.classList.toggle("loading", isLoading);
    elements.send.disabled = isLoading;
    elements.clear.disabled = isLoading;
    elements.question.disabled = isLoading;
    if (elements.newChat) elements.newChat.disabled = isLoading;
    if (elements.chatSearch) elements.chatSearch.disabled = isLoading;
  };

  const reset = (statusText = "Thinking…", { clearHistory = false } = {}) => {
    state.sql = "";
    state.rows = [];
    state.figure = null;
    state.sortBy = null;
    state.sortDir = "asc";
    state.page = 1;
    state.trace = [];
    state.plan = [];
    state.durationMs = null;
    setStatus(statusText);
    setSQL("—", { store: false });
    clearRows();
    clearFigure();
    renderTrace([]);
    renderTableSuggestions([], { reason: "No table suggestions yet." });
    if (clearHistory) {
      renderChatHistory([]);
    }
  };

  return {
    elements,
    state,
    setStatus,
    setSQL,
    renderRows,
    renderFigure,
    renderTrace,
    reset,
    setLoading,
    showToast,
  };
})();

const SESSION_KEY = "nl2sql-session-id";
const HISTORY_OPEN_KEY = "nl2sql-history-open";
const ensureSessionId = () => {
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
};
ui.state.sessionId = ensureSessionId();

function apiRequest(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  return fetch(path, { ...options, headers }).then(async (resp) => {
    let data = null;
    try {
      data = await resp.json();
    } catch (_err) {
      data = null;
    }
    if (!resp.ok) {
      const message = (data && data.detail) || "Request failed";
      throw new Error(message);
    }
    return data;
  });
}

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

function closeChatMenus() {
  document.querySelectorAll(".chat-menu.open").forEach((menu) => menu.classList.remove("open"));
}

const sortRows = (rows) => {
  const { sortBy, sortDir } = ui.state;
  if (!sortBy) return rows;
  const sorted = [...rows];
  sorted.sort((a, b) => {
    const va = a[sortBy];
    const vb = b[sortBy];
    if (va === vb) return 0;
    if (va === undefined || va === null) return 1;
    if (vb === undefined || vb === null) return -1;

    const aNum = typeof va === "number" || (!isNaN(parseFloat(va)) && isFinite(va));
    const bNum = typeof vb === "number" || (!isNaN(parseFloat(vb)) && isFinite(vb));

    if (aNum && bNum) {
      const diff = parseFloat(va) - parseFloat(vb);
      return sortDir === "asc" ? diff : -diff;
    }
    const comp = String(va).localeCompare(String(vb));
    return sortDir === "asc" ? comp : -comp;
  });
  return sorted;
};

const handleSort = (col) => {
  if (ui.state.sortBy === col) {
    ui.state.sortDir = ui.state.sortDir === "asc" ? "desc" : "asc";
  } else {
    ui.state.sortBy = col;
    ui.state.sortDir = "asc";
  }
  ui.state.page = 1;
  ui.renderRows(ui.state.rows, "retrieval", { resetSort: false });
};

const updatePagination = (totalPages) => {
  const el = ui.elements;
  if (totalPages === 0) {
    el.pageInfo.textContent = "Page 0 / 0";
    el.pagePrev.disabled = true;
    el.pageNext.disabled = true;
    return;
  }
  el.pageInfo.textContent = `Page ${ui.state.page} / ${totalPages}`;
  el.pagePrev.disabled = ui.state.page <= 1;
  el.pageNext.disabled = ui.state.page >= totalPages;
};

function renderTableSuggestions(suggested, { reason = "" } = {}) {
  const infoEl = ui.elements.tablesInfo;
  const chipsEl = ui.elements.tablesChips;
  const applyBtn = ui.elements.applyTables;

  const unique = Array.from(new Set(suggested || [])).filter(Boolean);
  ui.state.suggestedTables = unique;
  ui.state.selectedTables = new Set(unique);

  chipsEl.innerHTML = "";
  applyBtn.disabled = !unique.length;

  if (!unique.length) {
    infoEl.textContent = reason || "No table suggestions yet.";
    return;
  }

  if (unique.length > 1 && ui.elements.tablesDetails) {
    ui.elements.tablesDetails.open = true;
  }

  infoEl.textContent =
    unique.length > 1
      ? reason || "Multiple relevant tables found. Select the ones to use and click apply."
      : reason || `Using table: ${unique[0]}`;

  unique.forEach((name) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip active";
    chip.textContent = name;
    chip.addEventListener("click", () => {
      if (chip.classList.contains("active")) {
        chip.classList.remove("active");
        ui.state.selectedTables.delete(name);
      } else {
        chip.classList.add("active");
        ui.state.selectedTables.add(name);
      }
      applyBtn.disabled = ui.state.selectedTables.size === 0;
    });
    chipsEl.appendChild(chip);
  });
}

function renderChatHistory(messages) {
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
      const rowCount = Array.isArray(msg.metadata.rows) ? msg.metadata.rows.length : 0;
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
      bubble.appendChild(actions);
    }

    container.appendChild(bubble);
  });
  requestAnimationFrame(() => {
    container.scrollTop = container.scrollHeight;
  });
}

function renderChatList(chats) {
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

async function loadChats({ selectIfMissing = true } = {}) {
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

async function selectChat(chatId) {
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

async function ensureActiveChat(question) {
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

function updateActiveChatTitle(question) {
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

async function fetchQuery(question, { tables = [], planOnly = false, chatId = null } = {}) {
  return apiRequest("/api/query", {
    method: "POST",
    body: JSON.stringify({
      question,
      execute: !planOnly,
      plan_only: planOnly,
      tables,
      session_id: ui.state.sessionId,
      chat_id: chatId,
    }),
  });
}

function applyResponse(data, intent, tablesUsed, planOnly) {
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

function buildAssistantMetadata(data, intent, planOnly) {
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

function loadStoredResult(metadata) {
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

function addLocalMessage(message) {
  ui.state.messages = [...(ui.state.messages || []), message];
  renderChatHistory(ui.state.messages);
}

async function runQuery(question, tablesOverride = [], planOnly = false) {
  const chatId = await ensureActiveChat(question);
  updateActiveChatTitle(question);
  ui.state.lastQuestion = question;
  ui.state.lastTablesUsed = tablesOverride;

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
  ui.reset();
  renderTableSuggestions([], { reason: "Finding relevant tables..." });

  try {
    const data = await fetchQuery(question, { tables: tablesOverride, planOnly, chatId });
    const intent = data.intent || "";
    applyResponse(data, intent, tablesOverride, planOnly);

    addLocalMessage({
      role: "assistant",
      content: data.sql || "Result",
      created_at: new Date().toISOString(),
      metadata: buildAssistantMetadata(data, intent, planOnly),
    });

    await loadChats({ selectIfMissing: false });
  } catch (err) {
    ui.setStatus(err.message || "Something went wrong.");
  } finally {
    ui.setLoading(false);
  }
}

async function handleSend(tablesOverride = []) {
  const question = ui.elements.question.value.trim();
  if (!question) return;
  const planOnly = !!(ui.elements.planOnly && ui.elements.planOnly.checked);
  ui.elements.question.value = "";
  await runQuery(question, tablesOverride, planOnly);
}

async function handleApplyTables() {
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

async function handleNewChat() {
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

function handleClear() {
  ui.setLoading(false);
  ui.reset("");
  ui.elements.question.value = "";
  ui.elements.question.focus();
  ui.setStatus("Cleared");
}

async function copyContent(text, label) {
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
