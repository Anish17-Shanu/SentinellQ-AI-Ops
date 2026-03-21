const summaryGrid = document.getElementById("summary-grid");
const incidentList = document.getElementById("incident-list");
const statusNode = document.getElementById("status");

async function request(url, options) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}

function renderSummary(summary) {
  summaryGrid.innerHTML = [
    ["Total Incidents", summary.total_incidents],
    ["P1", summary.by_priority.P1],
    ["P2", summary.by_priority.P2],
    ["Average Score", summary.average_score],
  ].map(([label, value]) => `<article><p class="eyebrow">${label}</p><h2>${value}</h2></article>`).join("");
}

function renderIncidents(items) {
  incidentList.innerHTML = items.map(({ input, assessment }) => `
    <article class="incident-card">
      <p class="eyebrow">${assessment.priority} · ${assessment.risk_band}</p>
      <h3>${input.service}</h3>
      <p>${assessment.explanation}</p>
      <p class="meta">Owner: ${input.owner} | Env: ${input.environment} | Score: ${assessment.score}</p>
      <div>
        <span class="tag">${input.category}</span>
        <span class="tag">${input.criticality}</span>
      </div>
    </article>
  `).join("");
}

async function refresh() {
  const [summary, incidents] = await Promise.all([request("/summary"), request("/incidents")]);
  renderSummary(summary);
  renderIncidents(incidents.items);
}

document.getElementById("incident-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  payload.impacted_users = Number(payload.impacted_users);
  payload.error_rate = Number(payload.error_rate);
  payload.latency_ms = Number(payload.latency_ms);
  payload.alerts = Number(payload.alerts);
  await request("/incidents", { method: "POST", body: JSON.stringify(payload) });
  statusNode.textContent = "Incident stored successfully.";
  event.currentTarget.reset();
  await refresh();
});

refresh().catch((error) => {
  statusNode.textContent = error.message;
});
