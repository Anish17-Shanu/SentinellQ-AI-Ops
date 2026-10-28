const API_BASE = (window.SENTINELIQ_API_BASE || "").replace(/\/$/, "");
const PAGE_SIZE = 20;
const POLL_MS = 15000;
const KEY_STORAGE = "sentineliq.apiKey";

const state = {
  offset: 0,
  total: 0,
  pageCount: 0,
  filters: {},
  selectedId: null,
  loading: false,
  timer: null,
};

const nodes = {
  summary: {
    total: document.getElementById("stat-total"),
    open: document.getElementById("stat-open"),
    p1: document.getElementById("stat-p1"),
    avg: document.getElementById("stat-avg"),
    mtta: document.getElementById("stat-mtta"),
    mttr: document.getElementById("stat-mttr"),
  },
  list: document.getElementById("incident-list"),
  status: document.getElementById("status"),
  conn: document.getElementById("conn-state"),
  resultCount: document.getElementById("result-count"),
  pageState: document.getElementById("page-state"),
  prev: document.getElementById("prev-page"),
  next: document.getElementById("next-page"),
  detail: document.getElementById("detail-panel"),
  detailBody: document.getElementById("detail-body"),
  form: document.getElementById("incident-form"),
  filterForm: document.getElementById("filter-form"),
  keyInput: document.getElementById("api-key"),
  keyState: document.getElementById("key-state"),
};

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function getKey() {
  return sessionStorage.getItem(KEY_STORAGE) || "";
}

function authHeaders() {
  const key = getKey();
  return key ? { "X-API-Key": key } : {};
}

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    const error = new Error(payload?.error?.message || `Request failed (${response.status})`);
    error.status = response.status;
    error.field = payload?.error?.field;
    throw error;
  }
  return payload;
}

function setStatus(message, kind = "info") {
  nodes.status.textContent = message;
  nodes.status.dataset.kind = kind;
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
  const total = Math.round(Number(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function priorityClass(priority) {
  return `p${String(priority || "P4").slice(-1)}`;
}

function renderSummary(summary) {
  nodes.summary.total.textContent = summary.total_incidents ?? 0;
  nodes.summary.open.textContent = summary.open_incidents ?? 0;
  nodes.summary.p1.textContent = summary.p1_open ?? 0;
  nodes.summary.avg.textContent = summary.average_score_100 ?? summary.average_score ?? 0;
  nodes.summary.mtta.textContent = formatDuration(summary.mtta_seconds);
  nodes.summary.mttr.textContent = formatDuration(summary.mttr_seconds);
  nodes.summary.mtta.title = summary.mtta_seconds ? `${summary.mtta_seconds}s mean time to acknowledge` : "No acknowledged incidents yet";
  nodes.summary.mttr.title = summary.mttr_seconds ? `${summary.mttr_seconds}s mean time to resolve` : "No resolved incidents yet";
}

function renderEmptyState() {
  nodes.list.replaceChildren(
    el(
      "div",
      { class: "empty" },
      el("p", { class: "empty-title", text: "No incidents match these filters." }),
      el("p", { class: "hint", text: "Clear the filters or create an incident to get started." })
    )
  );
}

function renderCards(items) {
  if (!items.length) {
    renderEmptyState();
    return;
  }
  const cards = items.map((incident) => {
    const openButton = el("button", {
      type: "button",
      class: "ghost",
      text: "View",
      "aria-label": `View ${incident.service} incident`,
      onclick: () => showDetail(incident.id),
    });
    return el(
      "article",
      { class: `incident-card ${priorityClass(incident.priority)}`, dataset: { id: incident.id } },
      el(
        "div",
        { class: "card-top" },
        el("p", { class: `pill ${priorityClass(incident.priority)}`, text: `${incident.priority} · ${incident.risk_band}` }),
        el("p", { class: "state-pill", text: incident.state })
      ),
      el("h3", { text: incident.service }),
      el("p", { class: "card-explanation", text: incident.explanation }),
      el(
        "dl",
        { class: "card-meta" },
        el("div", null, el("dt", { text: "Owner" }), el("dd", { text: incident.owner })),
        el("div", null, el("dt", { text: "Env" }), el("dd", { text: incident.environment })),
        el("div", null, el("dt", { text: "Score" }), el("dd", { text: `${incident.score_100} / 100` })),
        el("div", null, el("dt", { text: "Alerts" }), el("dd", { text: String(incident.alert_count ?? 1) }))
      ),
      el(
        "div",
        { class: "card-bottom" },
        el("div", { class: "tags" }, el("span", { class: "tag", text: incident.category }), el("span", { class: "tag", text: incident.criticality })),
        el("div", { class: "card-actions" }, el("time", { class: "hint", datetime: incident.created_at, text: formatTime(incident.created_at) }), openButton)
      )
    );
  });
  nodes.list.replaceChildren(...cards);
}

function currentQuery() {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state.filters)) {
    if (value && key !== "sort") params.set(key, value);
  }
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(state.offset));
  const [sort, order] = (state.filters.sort || "created_at:desc").split(":");
  params.set("sort", sort);
  params.set("order", order === "asc" ? "asc" : "desc");
  return params.toString();
}

function renderPager(count) {
  nodes.resultCount.textContent = `${state.total} total`;
  const page = Math.floor(state.offset / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
  nodes.pageState.textContent = `Page ${page} of ${pages}`;
  nodes.prev.disabled = state.offset === 0 || state.loading;
  nodes.next.disabled = state.offset + count >= state.total || state.loading;
}

async function loadSummary() {
  const summary = await request("/v1/summary");
  renderSummary(summary);
}

async function loadIncidents() {
  state.loading = true;
  renderPager(state.pageCount);
  try {
    const data = await request(`/v1/incidents?${currentQuery()}`);
    state.total = data.total;
    state.pageCount = data.count;
    renderCards(data.items);
    renderPager(data.count);
    if (state.selectedId && !data.items.some((item) => item.id === state.selectedId)) {
      closeDetail();
    }
    setStatus(`Loaded ${data.count} of ${data.total} incidents.`);
  } finally {
    state.loading = false;
    renderPager(state.pageCount);
  }
}

async function refresh(options = {}) {
  try {
    await Promise.all([loadSummary(), loadIncidents()]);
    if (state.selectedId && options.detail !== false) {
      await loadDetail(state.selectedId, { quiet: true });
    }
    nodes.conn.textContent = "Live · polling every 15s";
    nodes.conn.dataset.kind = "ok";
  } catch (error) {
    handleError(error);
  }
}

function handleError(error) {
  nodes.conn.textContent = "Disconnected";
  nodes.conn.dataset.kind = "error";
  if (error.status === 401) {
    setStatus("Authentication required. Add an API key under Connection settings.", "error");
    return;
  }
  if (error.status === 403) {
    setStatus("This API key lacks permission for that action.", "error");
    return;
  }
  setStatus(error.message || "Request failed.", "error");
}

function detailRow(label, value) {
  return el("div", null, el("dt", { text: label }), el("dd", { text: value ?? "—" }));
}

const TRANSITIONS = {
  open: ["acknowledged"],
  acknowledged: ["mitigated", "resolved"],
  mitigated: ["resolved"],
  resolved: [],
};

const TRANSITION_LABELS = {
  acknowledged: "Acknowledge",
  mitigated: "Mark mitigated",
  resolved: "Resolve",
};

function renderDetail(payload) {
  const incident = payload.incident;
  const timeline = payload.timeline || [];
  const actions = payload.playbook || [];
  const noteInput = el("input", {
    type: "text",
    id: "note-input",
    placeholder: "Optional note for the audit trail",
    maxlength: "500",
    "aria-label": "Transition note",
  });
  const buttons = (TRANSITIONS[incident.state] || []).map((target) =>
    el("button", {
      type: "button",
      class: target === "resolved" ? "" : "ghost",
      text: TRANSITION_LABELS[target],
      onclick: () => transition(incident.id, target, noteInput.value),
    })
  );

  nodes.detailBody.replaceChildren(
    el("p", { class: `pill ${priorityClass(incident.priority)}`, text: `${incident.priority} · ${incident.risk_band}` }),
    el("h3", { text: incident.service }),
    el("p", { class: "card-explanation", text: incident.explanation }),
    el("h4", { text: "Signals" }),
    el(
      "dl",
      { class: "detail-grid" },
      detailRow("Owner", incident.owner),
      detailRow("Environment", incident.environment),
      detailRow("Category", incident.category),
      detailRow("Criticality", incident.criticality),
      detailRow("Impacted users", incident.impacted_users.toLocaleString()),
      detailRow("Error rate", `${incident.error_rate}%`),
      detailRow("Latency", `${incident.latency_ms} ms`),
      detailRow("Alerts", String(incident.alerts)),
      detailRow("Score", `${incident.score_100} / 100 (raw ${incident.score})`),
      detailRow("Confidence", `${Math.round(incident.confidence * 100)}%`),
      detailRow("Created", formatTime(incident.created_at)),
      detailRow("Updated", formatTime(incident.updated_at))
    ),
    el("h4", { text: "Response playbook" }),
    el("ol", { class: "playbook" }, ...actions.map((action) => el("li", { text: action }))),
    el("h4", { text: "Timeline" }),
    el(
      "ol",
      { class: "timeline" },
      ...timeline
        .slice()
        .reverse()
        .map((event) =>
          el(
            "li",
            null,
            el("p", { class: "timeline-type", text: event.type.replaceAll("_", " ") }),
            el("p", { class: "hint", text: `${formatTime(event.at)} · ${event.actor || "system"}` }),
            event.detail?.note ? el("p", { text: event.detail.note }) : null
          )
        )
    ),
    el("h4", { text: "Update state" }),
    el("div", { class: "transition-controls" }, noteInput, ...buttons)
  );
}

async function loadDetail(id, options = {}) {
  const payload = await request(`/v1/incidents/${encodeURIComponent(id)}`);
  renderDetail(payload);
  if (!options.quiet) setStatus(`Viewing ${payload.incident.service}.`);
}

async function showDetail(id) {
  state.selectedId = id;
  nodes.detail.hidden = false;
  nodes.detailBody.replaceChildren(el("p", { class: "hint", text: "Loading incident…" }));
  try {
    await loadDetail(id);
    nodes.detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
    document.getElementById("close-detail").focus({ preventScroll: true });
  } catch (error) {
    handleError(error);
  }
}

function closeDetail() {
  state.selectedId = null;
  nodes.detail.hidden = true;
  nodes.detailBody.replaceChildren();
}

async function transition(id, target, note) {
  const buttons = nodes.detailBody.querySelectorAll("button");
  buttons.forEach((button) => (button.disabled = true));
  try {
    await request(`/v1/incidents/${encodeURIComponent(id)}/transitions`, {
      method: "POST",
      body: JSON.stringify({ state: target, note: note || "" }),
    });
    setStatus(`Incident ${target}.`);
    await loadDetail(id, { quiet: true });
    await refresh({ detail: false });
  } catch (error) {
    handleError(error);
    buttons.forEach((button) => (button.disabled = false));
  }
}

function collectFilters() {
  const data = new FormData(nodes.filterForm);
  const filters = {};
  for (const [key, value] of data.entries()) {
    filters[key] = String(value).trim();
  }
  return filters;
}

function resetOffset() {
  state.offset = 0;
}

function startPolling() {
  if (state.timer) clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (document.hidden) return;
    refresh({ detail: Boolean(state.selectedId) });
  }, POLL_MS);
}

let searchTimer = null;
nodes.filterForm.addEventListener("input", (event) => {
  state.filters = collectFilters();
  resetOffset();
  if (event.target.name === "search") {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadIncidents().catch(handleError), 300);
  } else {
    loadIncidents().catch(handleError);
  }
});

document.getElementById("clear-filters").addEventListener("click", () => {
  nodes.filterForm.reset();
  state.filters = {};
  resetOffset();
  loadIncidents().catch(handleError);
});

nodes.prev.addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - PAGE_SIZE);
  loadIncidents().catch(handleError);
});

nodes.next.addEventListener("click", () => {
  if (state.offset + PAGE_SIZE < state.total) {
    state.offset += PAGE_SIZE;
    loadIncidents().catch(handleError);
  }
});

document.getElementById("refresh-button").addEventListener("click", () => refresh());
document.getElementById("close-detail").addEventListener("click", closeDetail);

nodes.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = nodes.form.querySelector("button[type=submit]");
  const data = new FormData(nodes.form);
  const payload = Object.fromEntries(data.entries());
  for (const field of ["impacted_users", "alerts"]) payload[field] = Number(payload[field]);
  for (const field of ["error_rate", "latency_ms"]) payload[field] = Number(payload[field]);
  if (!payload.priority_hint) delete payload.priority_hint;
  if (!payload.description) delete payload.description;

  submit.disabled = true;
  try {
    const record = await request("/v1/incidents", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify(payload),
    });
    setStatus(`Stored ${record.service} as ${record.priority} (${record.score_100}/100).`);
    nodes.form.reset();
    resetOffset();
    await refresh({ detail: false });
  } catch (error) {
    handleError(error);
  } finally {
    submit.disabled = false;
  }
});

function updateKeyState() {
  const hasKey = Boolean(getKey());
  nodes.keyState.textContent = hasKey ? "Key set for this tab." : "No key set. Read-only demo access.";
  nodes.keyInput.placeholder = hasKey ? "Replace stored key" : "viewer/responder/admin key";
}

document.getElementById("save-key").addEventListener("click", () => {
  const value = nodes.keyInput.value.trim();
  if (value) sessionStorage.setItem(KEY_STORAGE, value);
  else sessionStorage.removeItem(KEY_STORAGE);
  nodes.keyInput.value = "";
  updateKeyState();
  refresh();
});

async function boot() {
  updateKeyState();
  try {
    const health = await request("/health");
    nodes.conn.textContent = `Connected · v${health.version}`;
    nodes.conn.dataset.kind = "ok";
  } catch (error) {
    handleError(error);
    return;
  }
  await refresh();
  startPolling();
}

boot();
