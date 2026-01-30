export const ui = (() => {
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
    newQuery: document.getElementById("new-query"),
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
    activeContextMessageId: null,
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
    if (elements.newQuery) elements.newQuery.disabled = isLoading;
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
      // clearHistory is intentionally ignored here; history rendering lives in chat module.
    }
  };

  const setActiveContext = (messageId) => {
    state.activeContextMessageId = messageId || null;
  };

  const clearActiveContext = () => {
    state.activeContextMessageId = null;
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
    setActiveContext,
    clearActiveContext,
  };
})();

export function sortRows(rows) {
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
}

export function handleSort(col) {
  if (ui.state.sortBy === col) {
    ui.state.sortDir = ui.state.sortDir === "asc" ? "desc" : "asc";
  } else {
    ui.state.sortBy = col;
    ui.state.sortDir = "asc";
  }
  ui.state.page = 1;
  ui.renderRows(ui.state.rows, "retrieval", { resetSort: false });
}

export function updatePagination(totalPages) {
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
}

export function renderTableSuggestions(suggested, { reason = "" } = {}) {
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
