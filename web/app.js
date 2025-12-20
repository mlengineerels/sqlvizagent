const ui = (() => {
  const elements = {
    question: document.getElementById("question"),
    send: document.getElementById("send"),
    status: document.getElementById("status"),
    sqlDetails: document.getElementById("sql-details"),
    sql: document.getElementById("sql"),
    rowsDetails: document.getElementById("rows-details"),
    rows: document.getElementById("rows"),
    figureDetails: document.getElementById("figure-details"),
    chart: document.getElementById("chart"),
    copySql: document.getElementById("copy-sql"),
    copyRows: document.getElementById("copy-rows"),
    clear: document.getElementById("clear"),
    pagePrev: document.getElementById("page-prev"),
    pageNext: document.getElementById("page-next"),
    pageInfo: document.getElementById("page-info"),
  };

  const state = { sql: "", rows: [], figure: null, sortBy: null, sortDir: "asc", page: 1, pageSize: 20 };
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

  const setLoading = (isLoading) => {
    elements.send.classList.toggle("loading", isLoading);
    elements.send.disabled = isLoading;
    elements.clear.disabled = isLoading;
    elements.question.disabled = isLoading;
  };

  const reset = (statusText = "Thinking…") => {
    state.sql = "";
    state.rows = [];
    state.figure = null;
    state.sortBy = null;
    state.sortDir = "asc";
    state.page = 1;
    setStatus(statusText);
    setSQL("—", { store: false });
    clearRows();
    clearFigure();
  };

  return { elements, state, setStatus, setSQL, renderRows, renderFigure, reset, setLoading, showToast };
})();

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

async function fetchQuery(question) {
  const resp = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, execute: true }),
  });

  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

async function handleSend() {
  const question = ui.elements.question.value.trim();
  if (!question) return;

  ui.setLoading(true);
  ui.reset();

  try {
    const data = await fetchQuery(question);
    const intent = data.intent || "";

    ui.setSQL(data.sql || "—");
    ui.renderRows(data.rows || [], intent);
    ui.renderFigure(data.figure, intent);
    ui.setStatus(`Intent: ${intent || "unknown"}`);
  } catch (err) {
    ui.setStatus(err.message || "Something went wrong.");
  } finally {
    ui.setLoading(false);
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
ui.elements.send.addEventListener("click", handleSend);
ui.elements.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !ui.elements.send.disabled) handleSend();
});

updatePagination(0);
