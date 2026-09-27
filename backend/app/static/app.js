"use strict";

const demos = {
  "new-england": { action: "rank", region: "new_england", metric: "screen_score", year: 2024 },
  "lax-sna": { action: "compare", airports: ["LAX", "SNA"], metric: "congestion", year: 2024 },
  "anc-long-haul": { action: "metric", airports: ["ANC"], metric: "long_haul_share", year: 2024, threshold_miles: 3000 },
  "sfo-pressure": { action: "metric", airports: ["SFO"], metric: "sfo_pressure", year: 2024 },
  "sfo-trend": { action: "metric", airports: ["SFO"], metric: "sfo_enplaned_trend", year: 2024 },
  "bos-pvd": { action: "compare", airports: ["BOS", "PVD"], metric: "passenger_growth", year: 2024 },
  "growth": { action: "rank", region: "new_england", metric: "passenger_growth", year: 2024 },
};
const $ = (selector) => document.querySelector(selector);
const feedback = $("#feedback");
const resultPanel = $("#result");
let latestSuccessfulResult = null;
let contextResultId = null;
let requestGeneration = 0;
let busy = false;

function changedDraft() {
  requestGeneration += 1;
  if (latestSuccessfulResult) renderResult(latestSuccessfulResult, true);
}

function setLoading(value) {
  busy = value;
  $("#controls").setAttribute("aria-busy", String(value));
  for (const button of document.querySelectorAll("button[data-send]")) button.disabled = value;
  $("#explain").disabled = value || !contextResultId;
}

function showFeedback(message, error = false) {
  feedback.textContent = message;
  feedback.setAttribute("role", error ? "alert" : "status");
  feedback.hidden = false;
}

async function submitRequest(request) {
  if (busy) return;
  const generation = ++requestGeneration;
  setLoading(true);
  showFeedback("Loading analysis. The previous result remains below; this can take up to 30 seconds.");
  if (latestSuccessfulResult) renderResult(latestSuccessfulResult, true);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35000);
  try {
    const response = await fetch("/api/query", {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const payload = await response.json();
    if (generation !== requestGeneration) return;
    if (!response.ok) {
      const detail = isRecord(payload) && isRecord(payload.error) ? payload.error : {};
      showFeedback(`${detail.message || `Request failed (HTTP ${response.status}).`} ${response.status === 409 ? "Use Start a new analysis, then choose a complete request." : "Review the scope or try again explicitly."}${detail.request_id ? ` Request ID: ${detail.request_id}` : ""}`, true);
      if (latestSuccessfulResult) renderResult(latestSuccessfulResult, true);
      return;
    }
    const result = validateResult(payload);
    latestSuccessfulResult = result;
    contextResultId = result.result_id;
    renderResult(result, false);
    showFeedback(result.status === "partial" ? "Partial result received. Review unavailable metrics, exclusions, and limitations." : "Analysis received.");
  } catch (error) {
    if (generation !== requestGeneration) return;
    showFeedback(error.name === "AbortError"
      ? "Request timed out. The previous result is retained. Retry explicitly; no retry was sent."
      : "The backend is unavailable or returned an invalid response. The previous result is retained. Check the local server and retry explicitly.", true);
    if (latestSuccessfulResult) renderResult(latestSuccessfulResult, true);
  } finally {
    clearTimeout(timeout);
    setLoading(false);
    if (generation !== requestGeneration) showFeedback("The draft changed while loading. Its response was not displayed. Submit the current scope to continue.");
  }
}

function renderResult(result, previous) {
  resultPanel.replaceChildren();
  resultPanel.hidden = false;
  heading(resultPanel, "h2", previous ? "Previous result" : result.status === "partial" ? "Partial result" : "Analysis result");
  paragraph(resultPanel, `${label(result.scope.metric)} · ${result.scope.airports.join(", ")} · ${result.scope.year}`);
  paragraph(resultPanel, result.scope.population);
  if (result.scope.threshold_miles != null) paragraph(resultPanel, `Long-haul threshold: ${formatNumber(result.scope.threshold_miles)} miles.`);
  if (result.summary) paragraph(resultPanel, result.summary);
  if (!result.rows.length) paragraph(resultPanel, "No metric rows returned. Review the exclusions and limitations below.");
  else {
    const table = makeTable("Airport metrics — ranks and values supplied by the backend", ["Rank", "Airport", "Metric", "Value", "Numerator / denominator", "Eligible observations", "Comparison", "Sources"]);
    for (const row of result.rows) for (const metric of row.metrics) {
      const tr = document.createElement("tr");
      const direction = metric.comparison_direction ? label(metric.comparison_direction) : "—";
      const value = metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric);
      for (const text of [row.rank == null ? "—" : String(row.rank), row.airport, label(metric.key), value,
        metric.denominator == null ? "Not supplied" : `${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`,
        metric.eligible_count == null ? "Not supplied" : formatNumber(metric.eligible_count), direction, metric.source_ids.join(", ")]) cell(tr, text);
      table.body.append(tr);
    }
    resultPanel.append(table.region);
  }
  if (result.series.length) {
    const table = makeTable("Monthly series", ["Month", "Value"]);
    for (const point of result.series) {
      const tr = document.createElement("tr");
      cell(tr, `${point.period.slice(0, 4)}-${point.period.slice(4)}`);
      cell(tr, point.status === "unavailable" ? "Unavailable" : formatMetric(point));
      table.body.append(tr);
    }
    resultPanel.append(table.region);
  }
  listSection(resultPanel, "Exclusions", result.exclusions);
  listSection(resultPanel, "Limitations", result.limitations);
  heading(resultPanel, "h3", "Evidence and counterevidence");
  if (!result.evidence.length) paragraph(resultPanel, "No reviewed evidence was returned for this result; traffic alone does not establish an investment case.");
  for (const evidence of result.evidence) {
    const details = document.createElement("details");
    details.open = true;
    heading(details, "summary", `${evidence.source_id} · ${evidence.date}`);
    paragraph(details, `Evidence: ${evidence.claim}`);
    paragraph(details, `Locator: ${evidence.locator}`);
    paragraph(details, `Limitation / counterevidence: ${evidence.limitation}`);
    resultPanel.append(details);
  }
  heading(resultPanel, "h3", "Sources");
  for (const source of result.sources) {
    const details = document.createElement("details");
    heading(details, "summary", `${source.id}: ${source.name}`);
    paragraph(details, `Snapshot: ${source.snapshot_id}`);
    paragraph(details, `Observation period: ${source.period}`);
    paragraph(details, `Retrieved: ${source.retrieved_at || "Not supplied"}`);
    if (isSafeHttpUrl(source.url)) {
      const link = document.createElement("a");
      link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "Open cited source (new tab)"; details.append(link);
    }
    resultPanel.append(details);
  }
  paragraph(resultPanel, `Request ID: ${result.request_id}`);
}

function validateResult(result) {
  const fail = () => { throw new Error("Invalid analysis response"); };
  if (!isRecord(result) || !["ok", "partial"].includes(result.status)
      || typeof result.result_id !== "string" || typeof result.request_id !== "string"
      || !isRecord(result.scope) || !Array.isArray(result.scope.airports)
      || !result.scope.airports.every((airport) => typeof airport === "string")
      || ![2023, 2024].includes(result.scope.year) || typeof result.scope.metric !== "string"
      || typeof result.scope.population !== "string" || !(result.summary === null || typeof result.summary === "string")
      || !Array.isArray(result.rows) || result.rows.length > 22
      || !Array.isArray(result.series) || result.series.length > 24
      || !Array.isArray(result.sources) || !Array.isArray(result.evidence)
      || !stringArray(result.limitations) || !stringArray(result.exclusions)) fail();
  const ids = new Set();
  for (const source of result.sources) {
    if (!isRecord(source) || !["id", "name", "snapshot_id", "period"].every((key) => typeof source[key] === "string")
        || !(source.url === null || isSafeHttpUrl(source.url)) || ids.has(source.id)) fail();
    ids.add(source.id);
  }
  for (const row of result.rows) {
    if (!isRecord(row) || !result.scope.airports.includes(row.airport) || !Array.isArray(row.metrics)) fail();
    for (const metric of row.metrics) {
      if (!validValue(metric) || typeof metric.key !== "string" || !stringArray(metric.source_ids)
          || !metric.source_ids.every((id) => ids.has(id))
          || (metric.status === "unavailable" && (typeof metric.reason !== "string" || !metric.reason))
          || (metric.denominator != null && (!Number.isFinite(metric.denominator) || !Number.isFinite(metric.numerator)))
          || (metric.eligible_count != null && (!Number.isInteger(metric.eligible_count) || metric.eligible_count < 0))
          || (metric.comparison_direction != null && !["higher", "lower", "tied", "unavailable"].includes(metric.comparison_direction))) fail();
    }
  }
  for (const point of result.series) if (!validValue(point) || !/^202[34](0[1-9]|1[0-2])$/.test(point.period)) fail();
  for (const item of result.evidence) {
    if (!isRecord(item) || !["source_id", "locator", "date", "claim", "limitation"].every((key) => typeof item[key] === "string") || !ids.has(item.source_id)) fail();
  }
  return result;
}
function validValue(value) {
  return isRecord(value) && ["count", "percent", "percentage_points", "minutes", "score"].includes(value.unit)
    && ((value.status === "ok" && Number.isFinite(value.value)) || (value.status === "unavailable" && value.value === null));
}
function isRecord(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function stringArray(value) { return Array.isArray(value) && value.every((item) => typeof item === "string"); }
function isSafeHttpUrl(value) {
  if (typeof value !== "string") return false;
  try { return ["http:", "https:"].includes(new URL(value).protocol); } catch (_) { return false; }
}
function label(value) { return value.replaceAll("_", " "); }
function formatNumber(value) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value); }
function formatMetric(metric) {
  const value = metric.unit === "count" ? formatNumber(metric.value) : metric.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value}${({ percent: "%", percentage_points: " percentage points", minutes: " min", score: " / 100", count: "" })[metric.unit]}`;
}
function paragraph(parent, text) { const node = document.createElement("p"); node.textContent = text; parent.append(node); }
function heading(parent, tag, text) { const node = document.createElement(tag); node.textContent = text; parent.append(node); }
function cell(parent, text) { const node = document.createElement("td"); node.textContent = text; parent.append(node); }
function listSection(parent, title, items) {
  if (!items.length) return;
  heading(parent, "h3", title);
  const list = document.createElement("ul");
  for (const item of items) { const node = document.createElement("li"); node.textContent = item; list.append(node); }
  parent.append(list);
}
function makeTable(title, columns) {
  const region = document.createElement("div"); region.className = "table-region";
  region.tabIndex = 0; region.setAttribute("role", "region"); region.setAttribute("aria-label", `${title}; scroll horizontally if needed`);
  const table = document.createElement("table"); heading(table, "caption", title);
  const head = document.createElement("thead"); const row = document.createElement("tr");
  for (const title of columns) { const th = document.createElement("th"); th.scope = "col"; th.textContent = title; row.append(th); }
  head.append(row); const body = document.createElement("tbody"); table.append(head, body); region.append(table);
  return { region, body };
}
function fillScope(analysis) {
  $("#action").value = analysis.action; $("#airports").value = (analysis.airports || []).join(", ");
  $("#metric").value = analysis.metric; $("#year").value = String(analysis.year);
  $("#threshold").value = analysis.threshold_miles || 3000;
}
for (const button of document.querySelectorAll("[data-preset]")) {
  button.addEventListener("click", () => {
    if (busy) return;
    changedDraft(); const analysis = demos[button.dataset.preset]; fillScope(analysis);
    void submitRequest({ analysis });
  });
}
for (const input of document.querySelectorAll("input, select, textarea")) input.addEventListener("input", changedDraft);
$("#scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const action = $("#action").value;
  const airports = $("#airports").value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);
  const analysis = { action, metric: $("#metric").value, year: Number($("#year").value) };
  if (action === "rank" && !airports.length) analysis.region = "new_england"; else analysis.airports = airports;
  if (analysis.metric === "long_haul_share") analysis.threshold_miles = Number($("#threshold").value);
  void submitRequest({ analysis });
});
$("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const request = { message: $("#question").value.trim() };
  if (!request.message) return;
  if (contextResultId) request.context_result_id = contextResultId;
  void submitRequest(request);
});
$("#explain").addEventListener("click", () => {
  if (contextResultId) void submitRequest({ analysis: { action: "explain" }, context_result_id: contextResultId });
});
$("#fresh").addEventListener("click", () => {
  changedDraft(); contextResultId = null; $("#question").value = "";
  $("#explain").disabled = true; showFeedback("New analysis selected. Choose a preset or submit a complete scope; the previous result remains below.");
});
async function probeHealth() {
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" }, credentials: "same-origin" });
    const payload = await response.json();
    $("#health-status").textContent = response.ok && payload.status === "ok" ? "Backend reachable (source readiness is checked per query)" : "Backend unavailable";
  } catch (_) { $("#health-status").textContent = "Backend unavailable. Start the local server; requests can be retried explicitly."; }
}
void probeHealth();
