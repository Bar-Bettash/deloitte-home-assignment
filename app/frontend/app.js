"use strict";

// Presets omit `year` so the server applies its accepted default period
// (bundle annual-2025-r1: CY2024 baseline -> CY2025 comparison).
const demos = {
  "new-england": { action: "rank", region: "new_england", metric: "screen_score" },
  "lax-sna": { action: "compare", airports: ["LAX", "SNA"], metric: "congestion" },
  "anc-long-haul": { action: "metric", airports: ["ANC"], metric: "long_haul_share", threshold_miles: 3000 },
  "sfo-pressure": { action: "metric", airports: ["SFO"], metric: "sfo_pressure" },
  "sfo-trend": { action: "metric", airports: ["SFO"], metric: "sfo_enplaned_trend" },
  "bos-pvd": { action: "compare", airports: ["BOS", "PVD"], metric: "passenger_growth" },
  "growth": { action: "rank", region: "new_england", metric: "passenger_growth" },
};
// The server default period. Selecting it in the scope form omits `year`.
const DEFAULT_YEAR = 2025;
const SUPPORTED_YEARS = [2023, 2024, 2025];
const COVERAGE_SUMMARY = "Supported coverage: CY2024→CY2025 accepted bundle; 2023–2024 historical traffic and selected operational measures.";
const $ = (selector) => document.querySelector(selector);
const feedback = $("#feedback");
let feedbackTarget = $("#analyze");
const resultPanel = $("#result");
// Persistent controls are held by reference and never placed inside #result,
// because every render replaces that container's children.
const askTrigger = $("#ask-trigger");
const resultActions = $("#result-actions");
const connectionFailureMessage = "The backend is unavailable or returned an invalid response. The previous result is retained. Check the local server and retry explicitly.";
let latestSuccessfulResult = null;
let contextResultId = null;
let requestGeneration = 0;
let busy = false;
let resultIsPrevious = false;
let rendererReady = false;
let explanation = null;
const metricRenderers = Object.create(null);

function dispatchGlobeEvent(type, detail) {
  window.dispatchEvent(new CustomEvent(type, { detail, bubbles: false }));
}
function currentDraftAirports() {
  return $("#airports").value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);
}
function publishDraftState() {
  if (!rendererReady) return;
  dispatchGlobeEvent("airportdraftchange", { airports: currentDraftAirports() });
}
function publishResultState() {
  if (!rendererReady) return;
  const mappedAirports = new Set(["ANC", "LAX", "SNA", "SFO", "BOS", "PVD"]);
  const returnedAirports = latestSuccessfulResult ? latestSuccessfulResult.rows.map(row => row.airport).filter(airport => mappedAirports.has(airport)) : [];
  const regionalRanking = latestSuccessfulResult?.scope.airports.length > 6 && latestSuccessfulResult.rows.some(row => Number.isInteger(row.rank));
  dispatchGlobeEvent("analysisresultchange", {
    airports: returnedAirports, focusAirports: regionalRanking ? ["BOS", "PVD"] : returnedAirports,
    previous: resultIsPrevious,
  });
}
function publishGlobeState() { publishDraftState(); publishResultState(); }

function changedDraft(renderPrevious = true) {
  if (busy) setLoading(false);
  requestGeneration += 1;
  renderDraftSummary();
  if (latestSuccessfulResult && renderPrevious && $("#hero-layout").classList.contains("has-result")) {
    resultIsPrevious = true;
    renderResult(latestSuccessfulResult, true);
  }
  publishDraftState();
  if (!renderPrevious) publishResultState();
}
function addAirportToDraft(code) {
  $("#setup-controls").open = true;
  $("#scope-panel").open = true;
  positionFeedback($("#scope-form"));
  if (!supportedAirports.has(code)) return false;
  const action = $("#action").value;
  const airports = currentDraftAirports();
  if (action === "metric") {
    if (airports.length === 1 && airports[0] === code) return false;
    airports.splice(0, airports.length, code);
  } else if (action === "compare") {
    if (airports.includes(code)) return false;
    if (airports.length < 2) airports.push(code);
    else airports[1] = code;
  } else if (action === "rank") {
    if (!newEnglandAirports.has(code)) {
      showFeedback("This rank draft accepts New England airports only. It was left unchanged.", true);
      return false;
    }
    if (airports.includes(code)) return false;
    airports.push(code);
  } else return false;
  $("#airports").value = airports.join(", ");
  clearScopeError("airports");
  changedDraft();
  showFeedback(`${code} added to the visible draft. Run the scope to request an analysis.`);
  return true;
}

function setLoading(value) {
  busy = value;
  $("#controls").setAttribute("aria-busy", String(value));
  for (const button of document.querySelectorAll("button[data-send]")) button.disabled = value;
  $("#explain").disabled = value || !contextResultId;
  $("#question").disabled = value;
  $("#chat-form button").disabled = value;
}

function positionFeedback(target = null) {
  if (target) feedbackTarget = target;
  if (feedbackTarget?.after) feedbackTarget.after(feedback);
}

function showFeedback(message, error = false, target = null) {
  positionFeedback(target);
  feedback.replaceChildren();
  feedback.textContent = message;
  feedback.setAttribute("role", error ? "alert" : "status");
  feedback.setAttribute("data-complete", "false");
  feedback.hidden = false;
}

function showRequestError(detail, status) {
  showFeedback(`${detail.message} ${errorRecovery(detail, status)}`, true);
  const diagnostics = document.createElement("details");
  diagnostics.className = "technical-details";
  heading(diagnostics, "summary", "Technical request details");
  paragraph(diagnostics, `Request ID: ${detail.requestId}`);
  feedback.append(diagnostics);
}

function showResultReady(message) {
  showFeedback(message, false, $("#result-title"));
  feedback.setAttribute("data-complete", "true");
}

const supportedAirports = new Set("BDL HVN PWM BGR PQI RKD BHB AUG BOS ACK ORH MVY HYA PVC MHT PSM LEB PVD WST BID BTV RUT EWB ANC LAX SNA SFO".split(" "));
// EWB joins the New England cohort in the accepted 2025 bundle.
const newEnglandAirports = new Set("BDL HVN PWM BGR PQI RKD BHB AUG BOS ACK ORH MVY HYA PVC MHT PSM LEB PVD WST BID BTV RUT EWB".split(" "));
const operationalMetrics = new Set(["congestion", "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"]);
const t100Metrics = new Set(["passengers", "seats", "departures", "passenger_growth", "seat_occupancy", "long_haul_share"]);
const rankMetrics = new Set(["screen_score", "passengers", "passenger_growth", "seat_occupancy"]);
const errorCodes = new Set(["invalid_json", "unsupported_media_type", "request_too_large", "invalid_request", "unsupported_scope", "clarification_required", "insufficient_data", "busy", "session_expired", "result_mismatch", "ai_unavailable", "data_unavailable", "query_timeout", "internal_error"]);

function parseErrorResponse(payload) {
  if (!isRecord(payload) || payload.success !== false || !isRecord(payload.error)) return null;
  const { code, message, request_id: requestId } = payload.error;
  if (!errorCodes.has(code) || typeof message !== "string" || !message || message.length > 500
      || typeof requestId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) return null;
  return { code, message, requestId };
}

function errorRecovery(detail, status) {
  if (detail.code === "busy") return "Wait for the current analysis to finish, then retry explicitly.";
  if (["session_expired", "result_mismatch"].includes(detail.code)) return "Use Start a new analysis, then choose a complete request.";
  if (detail.code === "ai_unavailable") return "Presets and Adjust scope still work without AI interpretation.";
  return "Review the scope or try again explicitly.";
}

function validateAnalysisScope(analysis) {
  const errors = [];
  const airports = analysis.airports || [];
  if (airports.some((code) => !supportedAirports.has(code)) || new Set(airports).size !== airports.length) {
    errors.push({ field: "airports", message: "Use unique supported three-letter airport codes." });
  }
  // Mirrors contracts.AnalysisRequest: an omitted year means the server default (2025 bundle).
  const year = analysis.year ?? DEFAULT_YEAR;
  if (!SUPPORTED_YEARS.includes(year)) errors.push({ field: "year", message: "Choose a supported year." });
  if (["passenger_growth", "screen_score"].includes(analysis.metric) && year === 2023) {
    errors.push({ field: "year", message: "Passenger growth and screening score need a comparison period; choose 2025 or 2024." });
  }
  if (analysis.metric === "long_haul_share" && !(analysis.threshold_miles > 0 && analysis.threshold_miles <= 12000)) {
    errors.push({ field: "threshold", message: "Enter a long-haul threshold greater than 0 and no more than 12,000 miles." });
  }
  if (analysis.action === "rank") {
    if (!rankMetrics.has(analysis.metric)) errors.push({ field: "metric", message: "Rank supports screening score, passengers, passenger growth, or seat occupancy." });
    if (analysis.region !== "new_england" && (!airports.length || airports.some((code) => !newEnglandAirports.has(code)))) {
      errors.push({ field: "airports", message: "Rank needs a New England airport subset or a blank field for the full cohort." });
    }
  } else if (analysis.action === "compare") {
    if (airports.length !== 2) errors.push({ field: "airports", message: "Compare needs exactly two airport codes." });
    if (operationalMetrics.has(analysis.metric)) {
      if (year === 2023) errors.push({ field: "year", message: "Operational comparisons support 2025 and 2024 only." });
      if (airports.some((code) => !["LAX", "SNA", "SFO"].includes(code))) errors.push({ field: "airports", message: "Operational comparisons support LAX, SNA, and SFO." });
    } else if (!t100Metrics.has(analysis.metric)) errors.push({ field: "metric", message: "This metric is not available for comparisons." });
  } else if (analysis.action === "metric") {
    if (airports.length !== 1) errors.push({ field: "airports", message: "Single-airport metric needs exactly one airport code." });
    if (operationalMetrics.has(analysis.metric)) {
      if (year === 2023) errors.push({ field: "year", message: "Operational metrics support 2025 and 2024 only." });
      if (airports.length === 1 && !["LAX", "SNA", "SFO"].includes(airports[0])) errors.push({ field: "airports", message: "Operational metrics support LAX, SNA, and SFO." });
    } else if (["sfo_enplaned_trend", "sfo_pressure"].includes(analysis.metric)) {
      if (year === 2023) errors.push({ field: "year", message: "SFO metrics support 2025 and 2024 only." });
      if (airports.length === 1 && airports[0] !== "SFO") errors.push({ field: "airports", message: "This metric supports SFO only." });
    } else if (!t100Metrics.has(analysis.metric)) errors.push({ field: "metric", message: "This metric is not available for a single airport." });
  }
  return errors;
}

function clearScopeError(field) {
  const control = $(`#${field}`);
  const message = $(`#${field}-error`);
  if (!control || !message) return;
  control.removeAttribute?.("aria-invalid");
  const describedBy = (control.getAttribute?.("aria-describedby") || "").split(" ").filter((id) => id && id !== message.id);
  if (describedBy.length) control.setAttribute("aria-describedby", describedBy.join(" "));
  else control.removeAttribute?.("aria-describedby");
  message.textContent = "";
  message.hidden = true;
}

function clearQuestionError() {
  const question = $("#question");
  const message = $("#question-error");
  question.removeAttribute?.("aria-invalid");
  message.textContent = "";
  message.hidden = true;
}

function showQuestionError(text) {
  const question = $("#question");
  const message = $("#question-error");
  message.textContent = text;
  message.hidden = false;
  question.setAttribute("aria-invalid", "true");
}

// A failed chat request is reported next to the composer, which stays open with
// the question kept for editing; the previous result is left untouched.
function showChatError(text) {
  feedback.hidden = true;
  showQuestionError(text);
  $("#question-error").scrollIntoView?.({ block: "nearest", behavior: "smooth" });
}

function chatErrorMessage(detail) {
  if (!detail) return connectionFailureMessage;
  if (detail.code === "ai_unavailable") return "Couldn’t interpret that question. Try again or use one of the preset analyses.";
  return detail.message;
}

function showScopeErrors(errors) {
  for (const field of ["action", "airports", "metric", "year", "threshold"]) clearScopeError(field);
  const shown = new Set();
  for (const error of errors) {
    if (shown.has(error.field)) continue;
    const control = $(`#${error.field}`);
    const message = $(`#${error.field}-error`);
    if (!control || !message) continue;
    message.textContent = error.message;
    message.hidden = false;
    control.setAttribute("aria-invalid", "true");
    const describedBy = (control.getAttribute?.("aria-describedby") || "").split(" ").filter(Boolean);
    control.setAttribute("aria-describedby", [...new Set([...describedBy, message.id])].join(" "));
    shown.add(error.field);
  }
  const first = errors.find((error) => shown.has(error.field));
  if (first) $(`#${first.field}`).focus?.();
}

async function submitScope(analysis) {
  positionFeedback($("#scope-form"));
  const errors = validateAnalysisScope(analysis);
  if (errors.length) {
    showScopeErrors(errors);
    showFeedback(errors[0].message, true);
    return false;
  }
  showScopeErrors([]);
  await submitRequest({ analysis });
  return true;
}

function requestKind(request) {
  if (request.analysis?.action === "explain") return "explain";
  return request.message != null ? "followup" : "analysis";
}

async function submitRequest(request) {
  if (busy) return;
  const kind = requestKind(request);
  const generation = ++requestGeneration;
  const controller = new AbortController();
  let timeout = null;
  setLoading(true);
  try {
    // Follow-ups and explanations keep the current result labeled as current:
    // nothing replaces it unless a new result is admitted.
    if (kind !== "analysis" && latestSuccessfulResult) positionFeedback(resultActions);
    showFeedback(kind === "explain" ? "Loading explanation…"
      : latestSuccessfulResult
        ? "Loading analysis. The previous result remains available; this can take up to 30 seconds."
        : "Loading analysis. This can take up to 30 seconds.");
    if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
    timeout = setTimeout(() => controller.abort(), 35000);
    const response = await fetch("/api/query", {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const payload = await response.json();
    if (generation !== requestGeneration) return;
    if (!response.ok) {
      const detail = parseErrorResponse(payload);
      if (kind === "followup") { showChatError(chatErrorMessage(detail)); return; }
      if (detail) showRequestError(detail, response.status);
      else showFeedback(connectionFailureMessage, true);
      if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
      return;
    }
    const result = validateResult(payload);
    if (kind === "explain") {
      // Explain recomputes the referenced result; it never replaces it.
      if (!latestSuccessfulResult || result.result_id !== latestSuccessfulResult.result_id) throw new Error("Explanation does not match the displayed result");
      explanation = { resultId: result.result_id, text: result.summary || "No explanation was returned." };
      renderResult(latestSuccessfulResult, resultIsPrevious);
      showFeedback("Explanation received.", false, resultActions);
      feedback.setAttribute("data-complete", "true");
      return;
    }
    latestSuccessfulResult = result;
    contextResultId = result.result_id;
    resultIsPrevious = false;
    explanation = null;
    if (kind === "followup") $("#question").value = "";
    renderResult(result, false);
    showResultReady(result.status === "partial" ? "Partial result received. Review unavailable metrics, exclusions, and limitations." : "Analysis received.");
  } catch (error) {
    if (generation !== requestGeneration) return;
    const failure = error.name === "AbortError"
      ? "Request timed out. The previous result is retained. Retry explicitly; no retry was sent."
      : connectionFailureMessage;
    if (kind === "followup") { showChatError(failure); return; }
    showFeedback(failure, true);
    if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
  } finally {
    clearTimeout(timeout);
    if (generation === requestGeneration) setLoading(false);
  }
}

function enableEvidenceLink() {
  const link = $("#evidence-link");
  if (!link) return;
  link.href = "#analysis-evidence";
  link.removeAttribute("aria-disabled");
  link.removeAttribute("tabindex");
  const skip = $("#result-skip");
  if (skip) {
    skip.href = "#result-title";
    skip.removeAttribute("aria-disabled");
    skip.removeAttribute("tabindex");
  }
}

function openSetupAndScope() {
  $("#setup-controls").open = true;
  $("#scope-panel").open = true;
  $("#action").focus();
}

function disclosureContainsFocus(disclosure) {
  const focused = document.activeElement;
  return Boolean(focused && (focused === disclosure || disclosure.contains?.(focused)));
}

function renderResult(result, previous) {
  resultIsPrevious = previous;
  $("#hero-layout").classList.add("has-result");
  if (!previous) {
    const setup = $("#setup-controls");
    const scope = $("#scope-panel");
    if (!disclosureContainsFocus(setup)) setup.open = false;
    $("#setup-toggle").textContent = "Try another analysis";
    if (!disclosureContainsFocus(scope)) scope.open = false;
    $("#fresh").hidden = true;
    $("#back-to-analysis").hidden = false;
    $("#setup-toggle").textContent = "Choose another question";
  }
  enableEvidenceLink();
  resultPanel.replaceChildren();
  resultPanel.hidden = false;
  $("#result-panel").hidden = false;
  const title = $("#result-title");
  title.textContent = previous ? "Previous result" : result.status === "partial" ? "Partial result" : "Current result";
  title.hidden = false;
  const another = $("#fresh");
  another.textContent = "Choose another question";
  $("#view-evidence").hidden = true;
  const composer = $("#conversation-composer");
  const composerOpen = !composer.hidden && !composer.inert;
  const explain = $("#explain");
  explain.hidden = !contextResultId || contextResultId !== result.result_id;
  explain.disabled = busy || explain.hidden;
  // The existing follow-up button leads; Explain sits next to it.
  resultActions.insertBefore(askTrigger, resultActions.firstChild);
  askTrigger.textContent = "Ask a follow-up →";
  askTrigger.hidden = composerOpen;
  askTrigger.setAttribute("aria-expanded", String(composerOpen));
  const scopeHeading = document.createElement("div");
  scopeHeading.className = "result-scope-heading";
  const airportTitle = heading(scopeHeading, "h2", result.scope.metric === "screen_score" ? "New England screening" : result.scope.airports.length > 2 ? "Airports in scope" : result.scope.airports.join(" / "));
  airportTitle.className = "result-airports";
  const measureName = result.scope.metric === "congestion" ? `${result.scope.airports.join(" / ")} operational comparison` : humanScopeMetricLabel(result.scope.metric);
  const scopeDetails = paragraph(scopeHeading, `${measureName} · ${result.scope.year}`);
  scopeDetails.className = "result-measure";
  paragraph(scopeHeading, describePeriod(result.scope)).className = "result-period";
  resultPanel.append(scopeHeading);
  const kpis = renderKpiRow(result);
  if (kpis) resultPanel.append(kpis);
  const dashboard = document.createElement("div");
  dashboard.className = "dashboard-grid";
  dashboard.classList.toggle("congestion-dashboard", result.scope.metric === "congestion");
  dashboard.classList.toggle("screening-dashboard", result.scope.metric === "screen_score");
  resultPanel.append(dashboard);
  const metricView = document.createElement("div");
  metricView.className = "metric-view";
  metricView.id = "metric-view";
  dashboard.append(metricView);
  renderMetricView(result, metricView);
  const insights = document.createElement("section");
  insights.className = "key-insights";
  heading(insights, "h3", "Key insights");
  paragraph(insights, result.summary || "No summary was returned.");
  if (result.limitations.length) paragraph(insights, `Limitation · ${result.limitations[0]}`);
  if (explanation && explanation.resultId === result.result_id) paragraph(insights, `Explanation · ${explanation.text}`).id = "result-explanation";
  dashboard.classList.toggle("fullwidth-insights", ["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric));
  dashboard.append(insights);
  if (result.series.length || ["sfo_enplaned_trend", "sfo_pressure"].includes(result.scope.metric)) {
    const seriesPanel = document.createElement("section");
    seriesPanel.className = "series-panel";
    const headingText = ["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric) ? "DataSF monthly ENPLANED passengers" : "Monthly series";
    if (["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric)) seriesPanel.classList.add("feature-chart");
    heading(seriesPanel, "h3", headingText);
    if (result.series.length) {
      const units = [...new Set(result.series.map((point) => point.unit))];
      const unitCaption = units.length === 1 ? `Unit: ${units[0]}` : `Units vary: ${units.join(", ")}`;
      paragraph(seriesPanel, `${unitCaption} · ${result.series[0].period.slice(0, 4)}-${result.series[0].period.slice(4)} to ${result.series.at(-1).period.slice(0, 4)}-${result.series.at(-1).period.slice(4)} · returned values only`);
    }
    if (result.series.length) {
      const table = makeTable("Monthly series — complete returned values and units", ["Month", "Value"]);
      for (const point of result.series) {
        const tr = document.createElement("tr");
        cell(tr, `${point.period.slice(0, 4)}-${point.period.slice(4)}`);
        cell(tr, point.status === "unavailable" ? `Unavailable: Value unavailable · ${point.unit}` : formatMetric(point));
        table.body.append(tr);
      }
      const chart = renderSeriesChart(result.series);
      if (chart) seriesPanel.append(chart);
      else paragraph(seriesPanel, "Series not plotted: no usable returned count values.");
      const exact = document.createElement("details");
      heading(exact, "summary", "Exact monthly values");
      exact.append(table.region);
      seriesPanel.append(exact);
    } else paragraph(seriesPanel, "No monthly series was returned.");
    if (["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric)) resultPanel.insertBefore(seriesPanel, dashboard);
    else dashboard.append(seriesPanel);
  }
  const evidenceGroup = document.createElement("details");
  evidenceGroup.className = "evidence-source-group";
  heading(evidenceGroup, "summary", "Evidence and sources");
  const evidenceHeading = heading(evidenceGroup, "h3", "Evidence and counterevidence");
  evidenceHeading.id = "analysis-evidence";
  evidenceHeading.tabIndex = -1;
  if (!result.evidence.length) paragraph(evidenceGroup, "No reviewed evidence was returned for this result; traffic alone does not establish an investment case.");
  for (const evidence of result.evidence) {
    const details = document.createElement("details");
    details.open = false;
    const source = result.sources.find((item) => item.id === evidence.source_id);
    heading(details, "summary", `${source?.name || "Reviewed source"} · ${evidence.date}`);
    paragraph(details, `Evidence: ${evidence.claim}`);
    paragraph(details, `Locator: ${evidence.locator}`);
    paragraph(details, `Limitation / counterevidence: ${evidence.limitation}`);
    if (!source) paragraph(details, `Source ID: ${evidence.source_id}`);
    evidenceGroup.append(details);
  }
  if (result.scope.metric === "sfo_pressure") paragraph(evidenceGroup, "These signals do not quantify or establish unmet demand.");
  if (result.scope.metric === "sfo_pressure") {
    const row = result.rows.find(item => item.airport === "SFO");
    const primary = new Set(["passenger_growth", "seat_occupancy", "sfo_pressure"]);
    const supporting = row?.metrics.filter(metric => !primary.has(metric.key)) || [];
    if (supporting.length) {
      const details = document.createElement("details");
      heading(details, "summary", "Supporting indicators");
      for (const metric of supporting) paragraph(details, `${humanMetricLabel(metric.key)} · ${metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric)}`);
      evidenceGroup.append(details);
    }
  }
  if (result.exclusions.length || result.limitations.length) {
    const limits = document.createElement("details");
    limits.className = "limits-detail";
    limits.open = false;
    heading(limits, "summary", "Limitations and coverage");
    listSection(limits, "Exclusions", result.exclusions);
    listSection(limits, "Limitations", result.limitations);
    evidenceGroup.append(limits);
  }
  const sourcesPanel = document.createElement("details");
  heading(sourcesPanel, "summary", "Values and sources");
  paragraph(sourcesPanel, `Population: ${result.scope.population}`);
  if (result.scope.threshold_miles != null) paragraph(sourcesPanel, `Long-haul threshold: ${formatNumber(result.scope.threshold_miles)} miles.`);
  for (const row of result.rows) for (const metric of row.metrics) {
    const parts = [`${row.airport} · ${humanMetricLabel(metric.key)}`, metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : `Value: ${formatMetric(metric)}`];
    if (metric.numerator != null && metric.denominator != null) parts.push(`Numerator / denominator: ${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`);
    if (metric.eligible_count != null) parts.push(`Eligible observations: ${formatNumber(metric.eligible_count)}`);
    if (metric.comparison_direction) parts.push(`Direction: ${humanDirection(metric.comparison_direction)}`);
    parts.push(`Source IDs: ${metric.source_ids.join(", ") || "None returned"}`);
    paragraph(sourcesPanel, parts.join(" · "));
  }
  heading(sourcesPanel, "h3", "Sources");
  if (!result.sources.length) {
    paragraph(sourcesPanel, "No source references were returned. Treat the result as incomplete and review the scope or run another supported example.");
    const action = document.createElement("button");
    action.type = "button";
    action.textContent = "Review scope";
    action.addEventListener("click", openSetupAndScope);
    sourcesPanel.append(action);
  }
  for (const source of result.sources) {
    const details = document.createElement("details");
    heading(details, "summary", source.name);
    paragraph(details, `Source ID: ${source.id}`);
    paragraph(details, `Snapshot: ${source.snapshot_id}`);
    paragraph(details, `Observation period: ${source.period}`);
    paragraph(details, `Retrieved: ${source.retrieved_at || "Not supplied"}`);
    if (isSafeHttpUrl(source.url)) {
      const link = document.createElement("a");
      link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "Open cited source (new tab)"; details.append(link);
    }
    sourcesPanel.append(details);
  }
  evidenceGroup.append(sourcesPanel);
  const diagnostics = document.createElement("details");
  diagnostics.className = "technical-details";
  heading(diagnostics, "summary", "Technical request details");
  paragraph(diagnostics, `Request ID: ${result.request_id}`);
  evidenceGroup.append(diagnostics);
  resultPanel.append(evidenceGroup);
  $("#view-evidence").onclick = () => { evidenceGroup.open = true; evidenceHeading.focus(); };
  updateContextStrip(result, previous);
  publishResultState();
}

function renderKpiRow(result) {
  if (["congestion", "screen_score", "long_haul_share"].includes(result.scope.metric)) return null;
  const metricsByKey = new Map();
  for (const row of result.rows) for (const metric of row.metrics) {
    if (!metricsByKey.has(metric.key)) metricsByKey.set(metric.key, []);
    metricsByKey.get(metric.key).push({ airport: row.airport, rank: row.rank, metric });
  }
  // SFO pressure keeps its three primary indicators (the rest are listed as
  // supporting evidence). Every other metric leads with the requested key when
  // it is returned, then shows whichever other metrics the rows actually carry.
  const candidates = result.scope.metric === "sfo_pressure"
    ? ["passenger_growth", "sfo_pressure", "seat_occupancy"]
    : [result.scope.metric, ...metricsByKey.keys()];
  const keys = [...new Set(candidates)].filter((key) => metricsByKey.has(key)).slice(0, 4);
  if (!keys.length) return null;
  const group = document.createElement("section");
  group.className = "kpi-row";
  group.setAttribute("aria-label", "Returned key metrics");
  for (const key of keys) {
    const card = document.createElement("article");
    card.className = "kpi-card";
    heading(card, "h3", humanMetricLabel(key));
    const values = metricsByKey.get(key);
    const selectedValues = result.rows.some((row) => row.rank != null) ? values.slice(0, 1) : values.slice(0, 4);
    for (const item of selectedValues) {
      const line = document.createElement("div");
      line.className = "kpi-value-line";
      const airport = document.createElement("span");
      airport.className = "kpi-airport";
      airport.textContent = item.airport;
      const value = document.createElement("strong");
      value.className = item.metric.status === "unavailable" ? "is-unavailable" : "";
      value.textContent = item.metric.status === "unavailable" ? "—" : formatMetric(item.metric);
      if (item.metric.unit === "percentage_points" && item.metric.status === "ok") value.setAttribute("aria-label", `${formatNumber(item.metric.value)} percentage points`);
      line.append(airport, value);
      card.append(line);
      if (item.metric.status === "unavailable") paragraph(card, item.metric.reason).className = "kpi-reason";
      else if (key === "long_haul_share" && item.metric.numerator != null && item.metric.denominator != null) paragraph(card, `Qualifying / eligible departures · ${formatNumber(item.metric.numerator)} / ${formatNumber(item.metric.denominator)}`).className = "kpi-note";
      if (result.scope.metric === "screen_score" && item.rank != null) paragraph(card, `Backend rank ${item.rank}`).className = "kpi-note";
    }
    group.append(card);
  }
  if (result.scope.threshold_miles != null) {
    const note = document.createElement("p");
    note.className = "kpi-threshold";
    note.textContent = `Threshold · ${formatNumber(result.scope.threshold_miles)} miles`;
    group.append(note);
  }
  return group;
}

function focusQuestionChoices() {
  closeQuestionComposer(false);
  if (contextResultId) startNewAnalysis();
  $("#setup-controls").open = true;
  document.querySelector('.prompt[data-preset="new-england"]')?.focus();
}

function openQuestionComposer(takeFocus = true) {
  const composer = $("#conversation-composer");
  clearTimeout(composerCloseTimer);
  composer.hidden = false;
  document.body?.classList?.add("composer-open");
  composer.inert = false;
  composer.setAttribute("aria-hidden", "false");
  askTrigger.setAttribute("aria-expanded", "true");
  askTrigger.hidden = $("#hero-layout").classList.contains("has-result");
  const animateOpen = () => composer.classList.add("is-open");
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(animateOpen);
  else setTimeout(animateOpen, 0);
  if (takeFocus) $("#question").focus();
}

let composerCloseTimer = null;
function closeQuestionComposer(returnFocus = true) {
  const composer = $("#conversation-composer");
  if (composer.hidden) return;
  composer.classList.remove("is-open");
  document.body?.classList?.remove("composer-open");
  composer.setAttribute("aria-hidden", "true");
  composer.inert = true;
  askTrigger.setAttribute("aria-expanded", "false");
  askTrigger.hidden = false;
  clearTimeout(composerCloseTimer);
  composerCloseTimer = setTimeout(() => { composer.hidden = true; }, window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 210);
  if (returnFocus) askTrigger.focus();
}

function describePeriod(scope) {
  if (scope.bundle_id == null) return `Period · ${scope.year} historical data`;
  return `Period · CY${scope.baseline_year} → CY${scope.comparison_year} · showing ${scope.year} · Bundle ${scope.bundle_id}`;
}

function updateContextStrip(result, previous) {
  const base = `${result.scope.airports.join(" / ")} · ${humanScopeMetricLabel(result.scope.metric)} · ${result.scope.year}${result.scope.bundle_id == null ? "" : ` (bundle ${result.scope.bundle_id})`}`;
  const candidates = result.rows.flatMap(row => row.metrics.map(metric => ({ airport: row.airport, metric })));
  const preferredKey = result.scope.metric === "congestion" ? "cancellation_rate" : result.scope.metric;
  let displayed = candidates.filter(item => item.metric.key === preferredKey).slice(0, 2);
  if (!displayed.length) displayed = candidates.slice(0, 2);
  const values = displayed.map(({ airport, metric }) => `${airport} ${humanMetricLabel(metric.key)} · ${metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric)}`);
  $("#context-summary").textContent = [base, ...values, `${result.sources.length} source${result.sources.length === 1 ? "" : "s"}`].join(" · ");
}

function renderMetricView(result, target) {
  if (!result.rows.length) { renderGenericMetricTable(result, target); return; }
  const renderer = metricRenderers[result.scope.metric];
  if (typeof renderer === "function" && renderer(result, target) !== false) {
    if (!new Set(["congestion", "screen_score", "sfo_pressure"]).has(result.scope.metric)) renderSupplementalMetricDetails(result, target);
    return;
  }
  renderGenericMetricTable(result, target);
}
function renderSupplementalMetricDetails(result, target) {
  const details = document.createElement("details");
  details.className = "methodology-detail";
  heading(details, "summary", "Values and sources");
  for (const row of result.rows) for (const metric of row.metrics) {
    const parts = [`${row.airport} · ${label(metric.key)}`];
    parts.push(metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : `Value: ${formatMetric(metric)}`);
    if (metric.numerator != null && metric.denominator != null) parts.push(`Numerator / denominator: ${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`);
    if (metric.eligible_count != null) parts.push(`Eligible observations: ${formatNumber(metric.eligible_count)}`);
    if (metric.comparison_direction) parts.push(`Direction: ${label(metric.comparison_direction)}`);
    parts.push(`Source IDs: ${metric.source_ids.join(", ") || "None returned"}`);
    paragraph(details, parts.join(" · "));
  }
  target.append(details);
}
function renderSeriesChart(series) {
  const usable = series.filter(point => point.status === "ok" && Number.isFinite(point.value));
  if (!usable.length || usable.some(point => point.unit !== "count")) return null;
  const points = series.map((point, index) => ({ point, index, month: Number(point.period.slice(4)), year: Number(point.period.slice(0, 4)) }))
    .filter(({ point }) => point.status === "ok" && Number.isFinite(point.value));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const width = 960, height = 260, left = 82, right = 18, top = 20, bottom = 40;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  const titleId = `series-chart-title-${series[0].period}`;
  const descId = `series-chart-description-${series[0].period}`;
  svg.setAttribute("aria-labelledby", `${titleId} ${descId}`);
  const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
  title.id = titleId; title.textContent = "Returned monthly values";
  const description = document.createElementNS("http://www.w3.org/2000/svg", "desc");
  description.id = descId; description.textContent = "Monthly passengers in counts. The vertical axis includes zero; unavailable months and gaps break the line.";
  svg.append(title, description);
  const values = points.map(({ point }) => point.value);
  const min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = index => left + (series.length <= 1 ? plotWidth / 2 : index * plotWidth / (series.length - 1));
  const y = value => top + (max - value) / span * plotHeight;
  const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
  axis.setAttribute("x1", String(left)); axis.setAttribute("x2", String(width - right));
  axis.setAttribute("y1", String(y(0))); axis.setAttribute("y2", String(y(0))); axis.setAttribute("class", "series-zero-axis"); svg.append(axis);
  for (const value of [...new Set([max, 0, min])]) {
    const position = y(value);
    const tick = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tick.setAttribute("x", String(left - 8)); tick.setAttribute("y", String(position + 4)); tick.setAttribute("text-anchor", "end");
    tick.setAttribute("class", "series-axis-label"); tick.textContent = formatNumber(value); svg.append(tick);
  }
  const yearStarts = series.map((point, index) => point.period.endsWith("01") ? index : -1).filter(index => index >= 0);
  const tickIndices = new Set([0, ...yearStarts, series.length - 1]);
  for (const index of [...tickIndices]) {
    const point = series[index]; if (!point) continue;
    const tick = document.createElementNS("http://www.w3.org/2000/svg", "text");
    tick.setAttribute("x", String(x(index))); tick.setAttribute("y", String(height - 8)); tick.setAttribute("text-anchor", index === 0 ? "start" : index === series.length - 1 ? "end" : "middle");
    tick.setAttribute("class", "series-axis-label");
    tick.textContent = point.period.endsWith("01") ? `Jan ${point.period.slice(0, 4)}` : `${point.period.slice(0, 4)}-${point.period.slice(4)}`;
    svg.append(tick);
  }
  let segment = [];
  const flush = () => {
    if (segment.length > 1) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", segment.map(item => `${x(item.index)},${y(item.point.value)}`).join(" "));
      line.setAttribute("fill", "none"); line.setAttribute("class", "series-line"); svg.append(line);
    } else if (segment.length === 1) {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", String(x(segment[0].index))); dot.setAttribute("cy", String(y(segment[0].point.value)));
      dot.setAttribute("r", "3"); dot.setAttribute("class", "series-point"); svg.append(dot);
    }
    segment = [];
  };
  for (const current of points) {
    const last = segment[segment.length - 1];
    if (last && (current.index !== last.index + 1 || !isNextMonth(last, current))) flush();
    segment.push(current);
  }
  flush();
  return svg;
}
function isNextMonth(previous, current) {
  return (current.year === previous.year && current.month === previous.month + 1)
    || (current.year === previous.year + 1 && previous.month === 12 && current.month === 1);
}
metricRenderers.congestion = renderCongestionView;
metricRenderers.screen_score = renderScreeningView;
metricRenderers.long_haul_share = renderLongHaulView;
metricRenderers.sfo_pressure = renderSfoPressureView;
function renderSfoPressureView(result, target) {
  // The returned growth and occupancy values already appear in the KPI row.
  if (result.scope.metric === "sfo_pressure") return true;
  const row = result.rows.find((item) => item.airport === "SFO");
  if (!row) return false;
  target.className += " priority-metric-grid";
  heading(target, "h3", "Priority indicators");
  const priority = [
    ["passenger_growth", "Passenger growth"],
    ["seat_occupancy", "Seat occupancy"],
    ["sfo_pressure", "Passenger growth gap"],
  ];
  let displayed = 0;
  for (const [key, title] of priority) {
    const metric = row.metrics.find((item) => item.key === key);
    if (!metric) continue;
    const block = document.createElement("div");
    block.className = "priority-metric";
    block.setAttribute("data-metric-key", key);
    const labelNode = paragraph(block, title);
    labelNode.className = "metric-label";
    const ppValue = metric.key === "sfo_pressure" && metric.status === "ok" ? `${formatNumber(metric.value)} pp` : null;
    const valueNode = paragraph(block, metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : ppValue || formatMetric(metric));
    valueNode.className = "metric-value";
    if (metric.key === "sfo_pressure") valueNode.setAttribute("aria-label", metric.status === "ok" ? `${formatNumber(metric.value)} percentage points` : `Unavailable: ${metric.reason}`);
    if (metric.key === "sfo_pressure" && metric.status === "ok") {
      const spokenUnit = document.createElement("span"); spokenUnit.className = "visually-hidden"; spokenUnit.textContent = " percentage points"; valueNode.append(spokenUnit);
    }
    target.append(block);
    displayed += 1;
  }
  if (!displayed) paragraph(target, "No priority indicators were returned.");
  return true;
}
function renderLongHaulView(result, target) {
  for (const airport of result.scope.airports) {
    const block = document.createElement("div");
    block.className = "long-haul-result";
    heading(block, "h3", airport);
    const metric = result.rows.find((row) => row.airport === airport)?.metrics.find((item) => item.key === "long_haul_share");
    if (!metric) paragraph(block, "Not returned.");
    else if (metric.status === "unavailable") {
      paragraph(block, `Unavailable: ${metric.reason}`);
      if (metric.numerator != null && metric.denominator != null) {
        paragraph(block, `Long-haul performed departures: ${formatNumber(metric.numerator)}`);
        paragraph(block, `Eligible performed departures: ${formatNumber(metric.denominator)}`);
      }
    } else {
      const share = paragraph(block, "Long-haul share");
      share.className = "long-haul-share-label";
      const track = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      track.setAttribute("class", "share-ring");
      track.setAttribute("viewBox", "0 0 100 100");
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", `${airport} returned long-haul share ${formatMetric(metric)}`);
      const circumference = 2 * Math.PI * 36;
      const ringTrack = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ringTrack.setAttribute("cx", "50"); ringTrack.setAttribute("cy", "50"); ringTrack.setAttribute("r", "36"); ringTrack.setAttribute("class", "share-ring-track");
      const fill = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      fill.setAttribute("cx", "50"); fill.setAttribute("cy", "50"); fill.setAttribute("r", "36"); fill.setAttribute("class", "share-ring-value");
      fill.setAttribute("stroke-dasharray", `${circumference * Math.max(0, Math.min(100, metric.value)) / 100} ${circumference}`);
      fill.setAttribute("transform", "rotate(-90 50 50)");
      const centerValue = document.createElementNS("http://www.w3.org/2000/svg", "text");
      centerValue.setAttribute("class", "share-ring-center");
      centerValue.setAttribute("x", "50"); centerValue.setAttribute("y", "50");
      centerValue.setAttribute("text-anchor", "middle"); centerValue.setAttribute("dominant-baseline", "middle");
      centerValue.setAttribute("fill", "currentColor"); centerValue.setAttribute("font-size", "10"); centerValue.setAttribute("font-weight", "600");
      centerValue.textContent = formatMetric(metric);
      track.append(ringTrack, fill, centerValue);
      block.append(track);
      const threshold = result.scope.threshold_miles == null ? "threshold not supplied" : `at least ${formatNumber(result.scope.threshold_miles)} miles`;
      paragraph(block, `Long-haul performed departures (${threshold}): ${formatNumber(metric.numerator)}`);
      paragraph(block, `Eligible performed departures: ${formatNumber(metric.denominator)}`);
    }
    target.append(block);
  }
  return true;
}
function renderScreeningView(result, target) {
  const ranked = result.rows.filter(row => Number.isInteger(row.rank) && row.metrics.some(metric => metric.key === "screen_score" && metric.status === "ok"));
  const preview = document.createElement("div");
  heading(target, "h3", `Top 5 of ${result.rows.length} returned · screening score / 100`);
  preview.className = "ranking-list ranking-preview";
  preview.setAttribute("role", "list");
  preview.setAttribute("aria-label", `Top five of ${result.rows.length} returned screening scores, in backend order`);
  const appendRow = (list, row, showScoreBar = true, compact = false) => {
    const item = document.createElement("div");
    item.className = "ranking-row";
    item.setAttribute("role", "listitem");
    const rank = paragraph(item, row.rank == null ? "—" : compact ? String(row.rank) : `Backend rank ${row.rank}`);
    rank.className = "backend-rank";
    heading(item, "h3", row.airport);
    const score = row.metrics.find(value => value.key === "screen_score");
    if (score?.status !== "ok") paragraph(item, `Screening score · ${score ? `Unavailable: ${score.reason}` : "Not returned"}`).className = "ranking-detail score-value";
    if (showScoreBar && Number.isInteger(row.rank) && score?.status === "ok") {
      const scoreBar = document.createElement("div"); scoreBar.className = "score-track";
      scoreBar.setAttribute("role", "img"); scoreBar.setAttribute("aria-label", `${row.airport}, backend rank ${row.rank}, screening score ${formatMetric(score)}`);
      const fill = document.createElement("span"); fill.className = "score-fill";
      fill.setAttribute("style", `width:${Math.max(0, Math.min(100, score.value))}%`); scoreBar.append(fill); item.append(scoreBar);
      paragraph(item, compact ? formatNumber(score.value) : `Screening score · ${formatMetric(score)}`).className = "ranking-detail score-value";
    } else if (score?.status === "ok") paragraph(item, compact ? formatNumber(score.value) : `Screening score · ${formatMetric(score)}`).className = "ranking-detail score-value";
    if (compact) { list.append(item); return; }
    const support = document.createElement("details");
    heading(support, "summary", "Supporting values and sources");
    for (const key of ["passenger_growth", "passengers", "seat_occupancy"]) {
      const metric = row.metrics.find((value) => value.key === key);
      const detail = paragraph(support, `${humanMetricLabel(key)} · ${!metric ? "Not returned" : metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric)}`);
      detail.className = "ranking-detail";
    }
    for (const metric of row.metrics) if (!new Set(["screen_score", "passenger_growth", "passengers", "seat_occupancy"]).has(metric.key)) {
      paragraph(support, `${humanMetricLabel(metric.key)} · ${metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric)}`);
    }
    if (row.metrics.some(metric => metric.source_ids.length)) paragraph(support, `Source IDs: ${[...new Set(row.metrics.flatMap(metric => metric.source_ids))].join(", ")}`);
    item.append(support); list.append(item);
  };
  for (const row of ranked.slice(0, 5)) appendRow(preview, row, true, true);
  target.append(preview);
  const full = document.createElement("details");
  heading(full, "summary", `All ranked airports (${ranked.length})`);
  const list = document.createElement("div"); list.className = "ranking-list full-ranking"; list.setAttribute("role", "list");
  list.setAttribute("aria-label", "All returned airports in backend order");
  for (const row of result.rows) appendRow(list, row, true);
  full.append(list); target.append(full);
  return true;
}
function renderCongestionView(result, target) {
  if (result.scope.airports.length !== 2) return false;
  heading(target, "h3", "Operational comparison");
  const keys = ["cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"];
  const titles = ["Cancellation rate", "Diversion rate", "Departure delay", "Taxi out"];
  const bars = document.createElement("div");
  bars.className = "congestion-bars";
  bars.setAttribute("role", "list");
  const table = makeTable("Exact operational values by airport", ["Measure", ...result.scope.airports]);
  table.region.className += " congestion-table-region";
  table.region.setAttribute("aria-label", "Operational comparison; measures by airport");
  table.region.children[0].className = "congestion-table";
  keys.forEach((key, index) => {
    const metrics = result.scope.airports.map(airport => result.rows.find(item => item.airport === airport)?.metrics.find(item => item.key === key));
    const valid = metrics.filter(metric => metric?.status === "ok" && Number.isFinite(metric.value));
    const minimum = Math.min(0, ...valid.map(metric => metric.value));
    const maximum = Math.max(0, ...valid.map(metric => metric.value));
    const span = maximum - minimum || 1;
    const zero = maximum === minimum ? 0 : -minimum / span * 100;
    const measure = document.createElement("div"); measure.className = "congestion-measure"; measure.setAttribute("role", "listitem");
    const labelNode = heading(measure, "h3", titles[index]); labelNode.className = "congestion-measure-title";
    const unit = valid[0]?.unit || "count";
    paragraph(measure, `Scale ${formatMetric({ unit, value: minimum })} to ${formatMetric({ unit, value: maximum })}`).className = "congestion-scale";
    const row = document.createElement("tr");
    cell(row, titles[index]);
      const tracks = document.createElement("div"); tracks.className = "comparison-tracks";
      const plotted = [];
    metrics.forEach((metric, airportIndex) => {
      const airport = result.scope.airports[airportIndex];
      let text = "Not returned", spoken = text;
      if (metric?.status === "unavailable") { text = "Unavailable"; spoken = `Unavailable: ${metric.reason}`; }
      else if (metric?.status === "ok") {
        text = formatMetric(metric); spoken = text;
        const cue = { higher: "↑", lower: "↓", tied: "↔" }[metric.comparison_direction];
        if (cue) { text += ` ${cue}`; spoken += `, ${label(metric.comparison_direction)}`; }
      }
      cell(row, text);
      row.children[row.children.length - 1].setAttribute("aria-label", spoken);
      const comparison = document.createElement("div"); comparison.className = "comparison-value";
      paragraph(comparison, `${airport} · ${metric?.comparison_direction ? humanDirection(metric.comparison_direction) : "Direction unavailable"}`).className = "comparison-label";
      paragraph(comparison, metric?.status === "ok" ? formatMetric(metric) : metric?.status === "unavailable" ? `Unavailable: ${metric.reason}` : "Not returned").className = "comparison-exact";
      if (metric?.status === "ok" && Number.isFinite(metric.value)) {
        plotted.push({ airport, metric });
      } else paragraph(comparison, "Not plotted").className = "not-plotted";
      tracks.append(comparison);
    });
    const plot = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    plot.setAttribute("class", "comparison-dumbbell");
    plot.setAttribute("viewBox", "0 0 1000 32");
    plot.setAttribute("role", "img");
    plot.setAttribute("aria-label", `${titles[index]} comparison on a zero-inclusive ${formatMetric({ unit, value: minimum })} to ${formatMetric({ unit, value: maximum })} scale: ${plotted.map(({ airport, metric }) => `${airport} ${formatMetric(metric)}, ${humanDirection(metric.comparison_direction)}`).join("; ") || "no returned values plotted"}`);
    const axis = document.createElementNS("http://www.w3.org/2000/svg", "line");
    axis.setAttribute("x1", "0"); axis.setAttribute("x2", "1000"); axis.setAttribute("y1", "16"); axis.setAttribute("y2", "16"); axis.setAttribute("class", "dumbbell-axis"); plot.append(axis);
    if (plotted.length === 2) {
      const positions = plotted.map(({ metric }) => maximum === minimum ? 500 : (metric.value - minimum) / span * 1000);
      const connector = document.createElementNS("http://www.w3.org/2000/svg", "line");
      connector.setAttribute("x1", String(positions[0])); connector.setAttribute("x2", String(positions[1])); connector.setAttribute("y1", "16"); connector.setAttribute("y2", "16"); connector.setAttribute("class", "dumbbell-connector"); plot.append(connector);
      plotted.forEach(({ airport }, pointIndex) => {
        const point = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        point.setAttribute("cx", String(positions[pointIndex])); point.setAttribute("cy", String(plotted[0].metric.value === plotted[1].metric.value ? (pointIndex ? 21 : 11) : 16)); point.setAttribute("r", "7");
        point.setAttribute("class", `dumbbell-point dumbbell-point-${pointIndex + 1}`); point.setAttribute("aria-label", `${airport} ${formatMetric(plotted[pointIndex].metric)}`); plot.append(point);
      });
    } else if (plotted.length === 1) {
      const point = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      point.setAttribute("cx", String(maximum === minimum ? 500 : (plotted[0].metric.value - minimum) / span * 1000)); point.setAttribute("cy", "16"); point.setAttribute("r", "7"); point.setAttribute("class", "dumbbell-point dumbbell-point-1"); plot.append(point);
    }
    tracks.append(plot);
    measure.append(tracks); bars.append(measure);
    table.body.append(row);
  });
  target.append(bars);
  const exact = document.createElement("details"); heading(exact, "summary", "Exact values and methodology"); exact.append(table.region); target.append(exact);
  const methodology = document.createElement("div");
  methodology.className = "methodology-detail";
  heading(methodology, "h3", "Denominators and sources");
  for (const key of keys) for (const airport of result.scope.airports) {
    const metric = result.rows.find((item) => item.airport === airport)?.metrics.find((item) => item.key === key);
    if (!metric) continue;
    const components = [`${airport} · ${titles[keys.indexOf(key)]}`];
    components.push(metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : `Value: ${formatMetric(metric)}`);
    if (metric.numerator != null && metric.denominator != null) components.push(`Numerator / denominator: ${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`);
    if (metric.eligible_count != null) components.push(`Eligible observations: ${formatNumber(metric.eligible_count)}`);
    if (metric.comparison_direction) components.push(`Direction: ${label(metric.comparison_direction)}`);
    components.push(`Source IDs: ${metric.source_ids.join(", ") || "None returned"}`);
    paragraph(methodology, components.join(" · "));
  }
  exact.append(methodology);
  return true;
}
function renderGenericMetricTable(result, target) {
  if (!result.rows.length) {
    paragraph(target, "No metric rows were returned. Review exclusions and limitations, then adjust the scope or run an example.");
    const action = document.createElement("button");
    action.type = "button";
    action.textContent = "Adjust scope";
    action.addEventListener("click", openSetupAndScope);
    target.append(action);
    return;
  }
  const table = makeTable("Airport metrics — values supplied by the backend", ["Rank", "Airport", "Metric", "Value", "Numerator / denominator", "Eligible observations", "Comparison", "Sources"]);
  for (const row of result.rows) for (const metric of row.metrics) {
    const tr = document.createElement("tr");
    if (metric.key === result.scope.metric) tr.className = "selected-metric-row";
    const direction = metric.comparison_direction ? label(metric.comparison_direction) : "—";
    const value = metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric);
    for (const text of [row.rank == null ? "—" : String(row.rank), row.airport, label(metric.key), value,
      metric.denominator == null ? "Not supplied" : `${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`,
      metric.eligible_count == null ? "Not supplied" : formatNumber(metric.eligible_count), direction, metric.source_ids.join(", ")]) cell(tr, text);
    table.body.append(tr);
  }
  target.append(table.region);
}

function validateResult(result) {
  const fail = () => { throw new Error("Invalid analysis response"); };
  const scopeMetrics = new Set(["passengers", "seats", "departures", "passenger_growth", "seat_occupancy", "long_haul_share", "screen_score", "congestion", "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes", "sfo_enplaned_trend", "sfo_pressure"]);
  const metricUnits = { passengers: "count", seats: "count", departures: "count", passenger_growth: "percent", seat_occupancy: "percent", long_haul_share: "percent", screen_score: "score", cancellation_rate: "percent", diversion_rate: "percent", departure_delay_minutes: "minutes", taxi_out_minutes: "minutes", sfo_enplaned_trend: "count", enplaned_growth: "percent", sfo_pressure: "percentage_points" };
  const ratioMetrics = new Set(["seat_occupancy", "long_haul_share", "cancellation_rate", "diversion_rate"]);
  if (!isRecord(result) || !["ok", "partial"].includes(result.status)
      || typeof result.result_id !== "string" || typeof result.request_id !== "string"
      || !isRecord(result.scope) || !Array.isArray(result.scope.airports)
      || result.scope.airports.length < 1 || result.scope.airports.length > 23
      || !result.scope.airports.every((airport) => typeof airport === "string" && supportedAirports.has(airport))
      || new Set(result.scope.airports).size !== result.scope.airports.length
      || !SUPPORTED_YEARS.includes(result.scope.year) || !validResolvedPeriod(result.scope) || !scopeMetrics.has(result.scope.metric)
      || typeof result.scope.population !== "string" || !(result.summary === null || typeof result.summary === "string")
      || !Array.isArray(result.rows) || result.rows.length > 23
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
    if (!isRecord(row) || !result.scope.airports.includes(row.airport) || !Array.isArray(row.metrics)
        || (row.rank != null && (!Number.isInteger(row.rank) || row.rank < 1 || row.rank > 23))) fail();
    for (const metric of row.metrics) {
      if (!validValue(metric) || typeof metric.key !== "string" || !metricUnits[metric.key] || metric.unit !== metricUnits[metric.key] || !stringArray(metric.source_ids)
          || !metric.source_ids.every((id) => ids.has(id))
          || (metric.status === "unavailable" && (typeof metric.reason !== "string" || !metric.reason))
          || ((metric.numerator == null) !== (metric.denominator == null))
          || (metric.numerator != null && (!Number.isFinite(metric.numerator) || !Number.isFinite(metric.denominator)))
          || (metric.status === "ok" && ratioMetrics.has(metric.key) && metric.numerator == null)
          || (metric.eligible_count != null && (!Number.isInteger(metric.eligible_count) || metric.eligible_count < 0))
          || (metric.comparison_direction != null && !["higher", "lower", "tied", "unavailable"].includes(metric.comparison_direction))) fail();
    }
  }
  for (const point of result.series) if (!validValue(point) || !/^202[345](0[1-9]|1[0-2])$/.test(point.period)) fail();
  for (const item of result.evidence) {
    if (!isRecord(item) || !["source_id", "locator", "date", "claim", "limitation"].every((key) => typeof item[key] === "string") || !item.source_id || item.source_id.length > 120) fail();
  }
  return result;
}
// Mirrors contracts.ResultScope: bundle_id, baseline_year and comparison_year are
// optional but appear together; a 2025 result must carry the resolved bundle.
function validResolvedPeriod(scope) {
  const fields = [scope.bundle_id, scope.baseline_year, scope.comparison_year];
  const present = fields.filter((value) => value != null).length;
  if (present === 0) return scope.year !== 2025;
  if (present !== 3) return false;
  if (typeof scope.bundle_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(scope.bundle_id)) return false;
  if (![scope.baseline_year, scope.comparison_year].every((year) => Number.isInteger(year) && SUPPORTED_YEARS.includes(year))) return false;
  return scope.baseline_year < scope.comparison_year && [scope.baseline_year, scope.comparison_year].includes(scope.year);
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
function humanDirection(value) { return ({ higher: "Higher", lower: "Lower", tied: "Tied", unavailable: "Unavailable" })[value] || "Direction unavailable"; }
function humanMetricLabel(key) {
  const names = { screen_score: "Screening score", passenger_growth: "Passenger growth", passengers: "Passengers", seats: "Seats", departures: "Departures", seat_occupancy: "Seat occupancy", long_haul_share: "Long-haul share", cancellation_rate: "Cancellation rate", diversion_rate: "Diversion rate", departure_delay_minutes: "Departure delay", taxi_out_minutes: "Taxi out", sfo_enplaned_trend: "SFO passenger trend", enplaned_growth: "Enplaned passenger growth", sfo_pressure: "Passenger growth gap" };
  return names[key] || label(key);
}
function humanScopeMetricLabel(key) { return key === "sfo_pressure" ? "SFO demand pressure" : humanMetricLabel(key); }
const draftSummary = document.createElement("div");
draftSummary.className = "actions draft-summary";
draftSummary.id = "draft-summary";
draftSummary.setAttribute("aria-label", "Current scope draft");
const draftFields = ["Action", "Airports", "Metric", "Year", "Threshold"];
const draftChips = draftFields.map((name) => {
  const chip = document.createElement("span");
  chip.className = "metric-note";
  chip.setAttribute("data-draft-field", name.toLowerCase());
  draftSummary.append(chip);
  return chip;
});
function renderDraftSummary() {
  if (!draftChips.length) return;
  const action = $("#action").value;
  const airports = currentDraftAirports();
  const airportLabel = action === "rank" && !airports.length ? "New England cohort" : airports.join(", ") || "Choose airport(s)";
const values = [label(action), airportLabel, humanScopeMetricLabel($("#metric").value), yearLabel($("#year").value),
    $("#metric").value === "long_haul_share" ? `${$("#threshold").value} miles` : ""];
  draftChips.forEach((chip, index) => {
    chip.hidden = index === 4 && !values[index];
    chip.textContent = `${draftFields[index]} · ${values[index]}`;
  });
}
$("#scope-panel").insertBefore(draftSummary, $("#scope-form"));
renderDraftSummary();
function yearLabel(value) { return value === String(DEFAULT_YEAR) ? `${DEFAULT_YEAR} (vs 2024)` : value; }
function formatNumber(value) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value); }
function formatMetric(metric) {
  const value = metric.unit === "count" ? formatNumber(metric.value) : metric.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (metric.unit === "percentage_points") return `${value} pp`;
  return `${value}${({ percent: "%", percentage_points: " percentage points", minutes: " min", score: " / 100", count: "" })[metric.unit]}`;
}
function paragraph(parent, text) { const node = document.createElement("p"); node.textContent = text; parent.append(node); return node; }
function heading(parent, tag, text) { const node = document.createElement(tag); node.textContent = text; parent.append(node); return node; }
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
  $("#metric").value = analysis.metric; $("#year").value = String(analysis.year ?? DEFAULT_YEAR);
  $("#threshold").value = analysis.threshold_miles || 3000;
  syncThresholdAvailability();
}
function syncThresholdAvailability() {
  $("#threshold").disabled = $("#metric").value !== "long_haul_share";
}
function runPreset(analysis) {
  if (busy) return Promise.resolve(false);
  $("#setup-controls").open = true;
  positionFeedback($("#analyze"));
  fillScope(analysis);
  changedDraft();
  showScopeErrors([]);
  return submitRequest({ analysis });
}
function startNewAnalysis() {
  closeQuestionComposer(false);
  $("#conversation-composer").hidden = true;
  // Feedback is relocated into the result heading while a result is visible,
  // so it is not a stable insertion reference inside the controls container.
  $("#analyze").after(askTrigger);
  askTrigger.hidden = false;
  askTrigger.textContent = "Ask your own question →";
  askTrigger.setAttribute("aria-expanded", "false");
  requestGeneration += 1;
  setLoading(false);
  latestSuccessfulResult = null;
  contextResultId = null;
  explanation = null;
  resultIsPrevious = false;
  $("#hero-layout").classList.remove("has-result");
  $("#result-panel").hidden = true;
  $("#result").hidden = true;
  $("#result-title").hidden = true;
  $("#back-to-analysis").hidden = true;
  $("#fresh").hidden = true;
  $("#view-evidence").hidden = true;
  $("#explain").hidden = true;
  $("#setup-controls").open = true;
  $("#setup-toggle").textContent = "Choose an analysis";
  positionFeedback($("#analyze"));
  $("#question").value = "";
  clearQuestionError();
  $("#context-summary").textContent = COVERAGE_SUMMARY;
  $("#explain").disabled = true;
  changedDraft(false);
  showScopeErrors([]);
  showFeedback("New analysis selected. Choose a preset or submit a complete scope.");
  document.querySelector('.prompt[data-preset="new-england"]')?.focus();
}
window.addEventListener("globerendererready", () => {
  rendererReady = true;
  publishGlobeState();
}, { once: true });
window.addEventListener("airportdraftselect", (event) => {
  const code = event.detail?.code;
  if (typeof code === "string") addAirportToDraft(code);
});
$("#add-airport").addEventListener("click", () => {
  const picker = $("#airport-picker");
  if (picker.value && addAirportToDraft(picker.value)) picker.value = "";
});
for (const button of document.querySelectorAll("[data-preset]")) {
  button.addEventListener("click", () => {
    void runPreset(demos[button.dataset.preset]);
  });
}
for (const input of document.querySelectorAll("input, select, textarea")) if (input.id !== "airport-picker") input.addEventListener("input", () => {
  // Typing a follow-up is not a scope change: the shown result stays current
  // and any in-flight request continues until a question is actually sent.
  if (input.id === "question") { clearQuestionError(); return; }
  changedDraft();
  if (["action", "airports", "metric", "year", "threshold"].includes(input.id)) clearScopeError(input.id);
});
$("#metric").addEventListener("input", syncThresholdAvailability);
$("#scope-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const action = $("#action").value;
  const airports = $("#airports").value.split(",").map((item) => item.trim().toUpperCase()).filter(Boolean);
  const analysis = { action, metric: $("#metric").value };
  // The default period is requested by omitting year, exactly like the presets.
  const year = Number($("#year").value || DEFAULT_YEAR);
  if (year !== DEFAULT_YEAR) analysis.year = year;
  if (action === "rank" && !airports.length) analysis.region = "new_england"; else analysis.airports = airports;
  if (analysis.metric === "long_haul_share") analysis.threshold_miles = Number($("#threshold").value);
  void submitScope(analysis);
});
$("#chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const request = { message: $("#question").value.trim() };
  clearQuestionError();
  if (!request.message) {
    showQuestionError("Enter a question before sending.");
    $("#question").focus();
    return;
  }
  if (contextResultId) request.context_result_id = contextResultId;
  void submitRequest(request);
});
askTrigger.addEventListener("click", openQuestionComposer);
$("#back-to-analysis").addEventListener("click", startNewAnalysis);
document.addEventListener?.("keydown", (event) => {
  if (event.key === "Escape" && !$("#conversation-composer").hidden) closeQuestionComposer();
});
$("#explain").addEventListener("click", () => {
  if (contextResultId) void submitRequest({ analysis: { action: "explain" }, context_result_id: contextResultId });
});
$("#evidence-link")?.addEventListener("click", (event) => {
  event.preventDefault();
  const target = $("#analysis-evidence");
  const group = $("#result .evidence-source-group");
  if (latestSuccessfulResult && target) { if (group) group.open = true; target.focus(); }
});
$("#analysis-skip").addEventListener("click", (event) => {
  event.preventDefault();
  $("#setup-controls").open = true;
  $("#analyst-rail").focus();
});
$("#result-skip").addEventListener("click", (event) => {
  event.preventDefault();
  const target = $("#result-title");
  if (latestSuccessfulResult && target) target.focus();
});
$("#fresh").addEventListener("click", focusQuestionChoices);
$("#setup-toggle").addEventListener("click", (event) => {
  if (!$("#setup-controls").open) {
    event.preventDefault();
    focusQuestionChoices();
  }
});
async function probeHealth() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" }, credentials: "same-origin", signal: controller.signal });
    const payload = await response.json();
    const ready = response.ok && payload.status === "ok";
    $("#health-status").textContent = ready ? "Connected" : "Connection unavailable";
    $("#health-status").dataset.status = ready ? "reachable" : "unavailable";
    $("#health-status").title = ready ? "Connected" : "Connection unavailable";
  } catch (_) {
    $("#health-status").textContent = "Connection unavailable";
    $("#health-status").dataset.status = "unavailable";
    $("#health-status").title = "Connection unavailable";
  }
  finally { clearTimeout(timeout); }
}
void probeHealth();
