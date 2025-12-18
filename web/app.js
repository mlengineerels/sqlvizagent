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
  };

  const state = { sql: "", rows: [], figure: null };
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
  };

  const clearFigure = (message = "No figure yet.") => {
    elements.chart.innerHTML = message;
    elements.figureDetails.open = false;
    state.figure = null;
  };

  const renderTable = (rows) => {
    if (!rows || rows.length === 0) {
      elements.rows.innerHTML = '<div class="muted">No rows returned.</div>';
      return;
    }

    const columns = Array.from(
      rows.reduce((set, row) => {
        Object.keys(row || {}).forEach((k) => set.add(k));
        return set;
      }, new Set())
    );

    const table = document.createElement("table");
    const thead = document.createElement("thead");
    const headerRow = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col;
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
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
  };

  const renderRows = (rows, intent) => {
    if (intent === "visualization") {
      elements.rows.textContent = "Visualization shown below.";
      elements.rowsDetails.open = false;
      state.rows = [];
      return;
    }
    state.rows = rows || [];
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
    setStatus(statusText);
    setSQL("—", { store: false });
    clearRows();
    clearFigure();
  };

  return { elements, state, setStatus, setSQL, renderRows, renderFigure, reset, setLoading, showToast };
})();

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

ui.elements.clear.addEventListener("click", handleClear);
ui.elements.send.addEventListener("click", handleSend);
ui.elements.question.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !ui.elements.send.disabled) handleSend();
});
