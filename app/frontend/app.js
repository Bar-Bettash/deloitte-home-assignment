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
const connectionFailureMessage = "The analysis service is unavailable or returned an invalid response. The previous result is kept; try again in a moment.";
let latestSuccessfulResult = null;
let contextResultId = null;
let requestGeneration = 0;
let busy = false;
let resultIsPrevious = false;
let rendererReady = false;
let explanation = null;
let methodologyExtras = [];
const metricRenderers = Object.create(null);
// The chat transcript lives only in this page's memory: a refresh clears it,
// while closing the card, presets, Back and new analyses keep it. It is never
// persisted and never sent — a follow-up still carries only the question, the
// signed context_result_id and, after a "which measure?" reply, its two airports.
let conversation = [];
// The two airports of an unanswered "which measure?" clarification. Sent once,
// with the next typed message, then cleared; presets, a new analysis and a reload
// clear it too. It holds two supported codes and nothing else.
let pendingComparison = null;
let composerOwner = null; // { generation, message } of the send that last emptied the question field
const presetLabels = {
  "new-england": "New England expansion", "lax-sna": "LAX vs SNA congestion", "anc-long-haul": "ANC long-haul share",
  "sfo-pressure": "SFO demand pressure", "sfo-trend": "SFO passenger trend", "bos-pvd": "BOS vs PVD growth", "growth": "New England growth ranking",
};

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
  cancelResultFade();
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

// Loading copy never names a deadline; a longer hint appears only after a pause.
const LOADING_HINT_DELAY_MS = 4000;
let loadingHintTimer = null;
function setLoading(value) {
  busy = value;
  $("#controls").setAttribute("aria-busy", String(value));
  // The composer is never `disabled` while a request is in flight: that would drop
  // focus to <body> and block drafting the next question. Send is aria-disabled and
  // the synchronous `busy` guard keeps exactly one request in flight.
  for (const button of document.querySelectorAll("button[data-send]:not(.composer-send)")) button.disabled = value;
  $("#explain").disabled = value || !contextResultId;
  $("#chat-form .composer-send").setAttribute("aria-disabled", String(value));
  document.body?.classList?.toggle("is-analyzing", value);
  $("#result-panel").classList.toggle("is-loading", value);
  if (!value) {
    clearTimeout(loadingHintTimer);
    loadingHintTimer = null;
    feedback.removeAttribute("data-loading");
  }
}
function showLoadingFeedback(label, generation) {
  showFeedback(`${label}…`);
  feedback.setAttribute("data-loading", "true");
  clearTimeout(loadingHintTimer);
  loadingHintTimer = setTimeout(() => {
    loadingHintTimer = null;
    if (busy && generation === requestGeneration && feedback.getAttribute("data-loading") === "true") {
      feedback.textContent = `${label}… This may take a few more seconds.`;
    }
  }, LOADING_HINT_DELAY_MS);
  loadingHintTimer?.unref?.();
}

// Result swap: the shown result dims briefly, the DOM is replaced, then the new
// content settles in. Reduced motion (or no matchMedia) swaps immediately.
const RESULT_OUT_MS = 120;
function prefersReducedMotion() {
  return typeof window.matchMedia !== "function" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
function fadeOutResult() {
  const panel = $("#result-panel");
  if (panel.hidden || prefersReducedMotion()) return Promise.resolve();
  panel.classList.add("is-leaving");
  return new Promise((resolve) => setTimeout(resolve, RESULT_OUT_MS));
}
// A superseded request (draft edit, Back) must never leave the shown result dimmed.
function cancelResultFade() {
  $("#result-panel").classList.remove("is-leaving");
}
function fadeInResult(wasHidden) {
  const panel = $("#result-panel");
  panel.classList.remove("is-leaving");
  if (prefersReducedMotion()) return;
  const start = wasHidden ? "is-entering-fresh" : "is-entering";
  panel.classList.add(start);
  const settle = () => panel.classList.remove(start);
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => requestAnimationFrame(settle));
  else setTimeout(settle, 16);
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
// A follow-up rejected for these reasons gets a written answer; any other failure
// (model, service, timeout, connection) is a failed turn with an inline Retry.
const chatReplyCodes = new Set(["unsupported_scope", "clarification_required", "insufficient_data", "invalid_request", "request_too_large", "session_expired", "result_mismatch"]);
const editToFixCodes = new Set(["invalid_request", "request_too_large"]);
const errorCodes = new Set(["invalid_json", "unsupported_media_type", "request_too_large", "invalid_request", "unsupported_scope", "clarification_required", "insufficient_data", "busy", "session_expired", "result_mismatch", "ai_unavailable", "data_unavailable", "query_timeout", "internal_error", "access_required"]);

function parseErrorResponse(payload) {
  if (!isRecord(payload) || payload.success !== false || !isRecord(payload.error)) return null;
  const { code, message, request_id: requestId, pending_comparison: pair } = payload.error;
  if (!errorCodes.has(code) || typeof message !== "string" || !message || message.length > 500
      || typeof requestId !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) return null;
  const validPair = code === "clarification_required" && Array.isArray(pair) && pair.length === 2 && pair[0] !== pair[1]
    && pair.every((airport) => supportedAirports.has(airport));
  return { code, message, requestId, pendingComparison: validPair ? [...pair] : null };
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
  message.removeAttribute?.("data-mirrored");
  message.textContent = "";
  message.hidden = true;
}

function showQuestionError(text) {
  const question = $("#question");
  const message = $("#question-error");
  message.removeAttribute?.("data-mirrored");
  message.textContent = text;
  message.hidden = false;
  question.setAttribute("aria-invalid", "true");
}

// A failed chat request is reported next to the composer, which stays open with
// the question kept for editing; the previous result is left untouched. When the
// same text is already the assistant's reply in the open card, the line above the
// input is marked as mirrored so the card shows it once (it stays the field's alert).
function showChatError(text, mirrored = false) {
  feedback.hidden = true;
  showQuestionError(text);
  if (mirrored) $("#question-error").setAttribute("data-mirrored", "true");
  $("#question-error").scrollIntoView?.({ block: "nearest", behavior: "smooth" });
}

function chatErrorMessage(detail) {
  if (!detail) return connectionFailureMessage;
  if (detail.code === "ai_unavailable") return "Natural-language analysis is temporarily unavailable. Preset analyses still work.";
  return detail.message;
}

// A new result replaces the old one in place, so the reader is returned to its
// title and headline figures instead of the old scroll position.
function scrollResultIntoView() {
  const behavior = prefersReducedMotion() ? "auto" : "smooth";
  const pane = $("#result-panel");
  const scrollable = pane && typeof getComputedStyle === "function" && /(auto|scroll)/.test(getComputedStyle(pane).overflowY || "")
    && pane.scrollHeight > pane.clientHeight;
  if (scrollable) { pane.scrollTo?.({ top: 0, behavior }); return; }
  const top = pane?.getBoundingClientRect?.().top;
  if (typeof top === "number" && top < 0) window.scrollTo?.({ top: Math.max(0, window.scrollY + top - 12), behavior });
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

// `chat.presetLabel` only annotates the transcript; it never enters the request body.
// `chat.chatText` makes a structured request a chat turn: the text is what the
// transcript shows as the analyst's message (a chosen measure); it is never sent.
async function submitRequest(request, chat = {}) {
  if (busy) return;
  const kind = requestKind(request);
  const conversational = kind === "followup" || Boolean(chat.chatText);
  // A preset or scoped analysis is a new question: no clarification is pending. A
  // chosen measure answers the pending question, so the pair stays until it succeeds.
  if (kind === "analysis" && !chat.chatText) pendingComparison = null;
  const generation = ++requestGeneration;
  // Any new send ends an older send's claim on the field, so only the latest can give text back.
  composerOwner = null;
  const controller = new AbortController();
  let timeout = null;
  // A follow-up shows the question at once, then an "Analyzing…" reply in the slot
  // the answer will land in. A retry reuses its failed turn instead of adding one.
  const turn = conversational ? startChatTurn(chat.chatText || request.message, chat.retryOf || null) : null;
  if (turn && chat.chatText) turn.request = request;
  retireChatChoices();
  // The sent question leaves the composer at once, typed or dictated; a failure that
  // keeps the turn for Retry puts it back for editing.
  if (kind === "followup") clearSentQuestion(request.message, generation);
  setLoading(true);
  try {
    // Follow-ups and explanations keep the current result labeled as current:
    // nothing replaces it unless a new result is admitted.
    if (kind !== "analysis" && latestSuccessfulResult) positionFeedback(resultActions);
    showLoadingFeedback(kind === "explain" ? "Preparing explanation" : "Analyzing", generation);
    if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
    timeout = setTimeout(() => controller.abort(), 35000);
    const response = await fetch("/api/query", {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const payload = await response.json();
    if (generation !== requestGeneration) return;
    if (response.status === 401 && parseErrorResponse(payload)?.code === "access_required") {
      // The shared demo access session ended (8-hour limit or sign-out elsewhere): sign in again.
      window.location.assign("/login");
      return;
    }
    if (!response.ok) {
      const detail = parseErrorResponse(payload);
      if (conversational) {
        const message = chatErrorMessage(detail);
        // An unsupported or unclear question is an answer, not a failure; a model,
        // service or timeout failure stays on the question with an inline Retry.
        // An answer uses up the pending pair; only a new "which measure?" sets one.
        if (detail && chatReplyCodes.has(detail.code)) {
          pendingComparison = detail.pendingComparison;
          settleChatReply(turn, message, "note");
          if (pendingComparison) offerChatChoices(turn.pending, pendingComparison);
          // "Send a shorter question" asks for an edit, so the text comes back for it.
          if (editToFixCodes.has(detail.code)) restoreFailedQuestion(generation);
        } else {
          failChatTurn(turn, message);
          restoreFailedQuestion(generation);
        }
        showChatError(message, chatExpanded());
        return;
      }
      if (detail) showRequestError(detail, response.status);
      else showFeedback(connectionFailureMessage, true);
      if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
      return;
    }
    const result = validateResult(payload);
    if (kind !== "explain") pendingComparison = null;
    // A typed "why?" can come back as an explanation: the same result, recomputed,
    // with an explanatory summary. It is answered like Explain, never shown as new.
    const explained = kind === "explain" || (kind === "followup" && result.result_id === latestSuccessfulResult?.result_id);
    if (explained) {
      // Explain recomputes the referenced result; it never replaces it.
      if (!latestSuccessfulResult || result.result_id !== latestSuccessfulResult.result_id) throw new Error("Explanation does not match the displayed result");
      explanation = { resultId: result.result_id, text: result.summary || "No explanation was returned." };
      renderResult(latestSuccessfulResult, resultIsPrevious);
      if (kind === "followup") {
        settleChatReply(turn, explanation.text, "done", result.result_id);
      } else {
        showFeedback("Explanation received.", false, resultActions);
        feedback.setAttribute("data-complete", "true");
      }
      return;
    }
    const panelWasHidden = $("#result-panel").hidden;
    await fadeOutResult();
    if (generation !== requestGeneration) { cancelResultFade(); return; }
    latestSuccessfulResult = result;
    contextResultId = result.result_id;
    resultIsPrevious = false;
    explanation = null;
    renderResult(result, false);
    fadeInResult(panelWasHidden);
    scrollResultIntoView();
    showResultReady(result.status === "partial" ? "Analysis received. Some airports or values were unavailable; see the coverage note." : "Analysis received.");
    if (conversational) settleChatReply(turn, compactReply(result), "done", result.result_id);
    else if (chat.presetLabel) addChatMessage("assistant", compactReply(result), { resultId: result.result_id, context: `Preset · ${chat.presetLabel}` });
  } catch (error) {
    if (generation !== requestGeneration) return;
    const failure = error.name === "AbortError"
      ? "Request timed out. The previous result is retained. Retry explicitly; no retry was sent."
      : connectionFailureMessage;
    if (conversational) {
      failChatTurn(turn, failure);
      restoreFailedQuestion(generation);
      showChatError(failure, chatExpanded());
      return;
    }
    showFeedback(failure, true);
    if (latestSuccessfulResult && kind === "analysis") renderResult(latestSuccessfulResult, true);
  } finally {
    clearTimeout(timeout);
    // A turn is never left saying "Analyzing…": a superseded request (Back, a
    // scope edit) or a failure while showing the answer still settles it.
    if (turn?.pending.state === "pending") {
      failChatTurn(turn, generation === requestGeneration
        ? "This answer could not be shown. The previous result is kept."
        : "Stopped: the analysis changed before this answer arrived.");
      restoreFailedQuestion(generation);
    }
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
    skip.hidden = false;
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
  // Partial coverage is stated in the result's coverage note, not as the headline.
  title.textContent = previous ? "Previous result" : "Current result";
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
  const airportTitle = heading(scopeHeading, "h2", resultHeadline(result));
  airportTitle.className = "result-airports";
  const measureName = result.scope.metric === "congestion" ? "Airport operations, four indicators"
    : result.scope.metric === "overview" ? (result.scope.airports.length === 2 ? "Overall comparison, key measures" : "Airport overview, key measures")
      : humanScopeMetricLabel(result.scope.metric);
  const scopeDetails = paragraph(scopeHeading, `${measureName} · ${result.scope.year}`);
  scopeDetails.className = "result-measure";
  paragraph(scopeHeading, describePeriod(result.scope)).className = "result-period";
  resultPanel.append(scopeHeading);
  const kpis = renderKpiRow(result);
  if (kpis) resultPanel.append(kpis);
  // The plain-language answer sits directly under the figures, before any chart.
  const takeaway = renderTakeaway(result);
  if (takeaway) resultPanel.append(takeaway);
  const dashboard = document.createElement("div");
  dashboard.className = "dashboard-grid";
  dashboard.classList.toggle("congestion-dashboard", result.scope.metric === "congestion");
  dashboard.classList.toggle("overview-dashboard", result.scope.metric === "overview");
  dashboard.classList.toggle("screening-dashboard", result.scope.metric === "screen_score");
  dashboard.classList.toggle("long-haul-dashboard", result.scope.metric === "long_haul_share");
  resultPanel.append(dashboard);
  const metricView = document.createElement("div");
  metricView.className = "metric-view";
  metricView.id = "metric-view";
  dashboard.append(metricView);
  // Renderers contribute exact tables here; they are shown inside the single
  // collapsed "Methodology & limitations" disclosure, never above the fold.
  methodologyExtras = [];
  renderMetricView(result, metricView);
  const insights = document.createElement("section");
  insights.className = "key-insights";
  heading(insights, "h3", "Key insight");
  renderInsightSummary(insights, result.summary || "No summary was returned.");
  if (result.limitations.length) paragraph(insights, `Limitation · ${result.limitations[0]}`).className = "insight-limitation";
  const coverage = coverageNote(result);
  if (coverage) paragraph(insights, coverage).className = "insight-coverage";
  dashboard.classList.toggle("fullwidth-insights", ["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric));
  // An operations comparison or overview states its conclusion before the detail.
  if (["congestion", "overview"].includes(result.scope.metric)) dashboard.insertBefore(insights, metricView);
  else dashboard.append(insights);
  // Explain output gets its own short section; it never joins the Key insight card.
  if (explanation && explanation.resultId === result.result_id) {
    const explained = document.createElement("section");
    explained.className = "result-explanation";
    heading(explained, "h3", "Explanation");
    paragraph(explained, explanation.text).id = "result-explanation";
    dashboard.append(explained);
  }
  if (result.series.length || ["sfo_enplaned_trend", "sfo_pressure"].includes(result.scope.metric)) {
    const seriesPanel = document.createElement("section");
    seriesPanel.className = "series-panel";
    const sfoSeries = ["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric);
    const headingText = sfoSeries ? "Monthly passengers boarding at SFO" : "Monthly series";
    if (sfoSeries) seriesPanel.classList.add("feature-chart");
    heading(seriesPanel, "h3", headingText);
    if (result.series.length) {
      const units = [...new Set(result.series.map((point) => point.unit))];
      const unitCaption = units.length === 1 ? `Unit: ${units[0]}` : `Units vary: ${units.join(", ")}`;
      const range = `${monthLabel(result.series[0].period)} – ${monthLabel(result.series.at(-1).period)}`;
      paragraph(seriesPanel, sfoSeries && units.length === 1 && units[0] === "count"
        ? `DataSF enplanements, domestic and international combined · ${range}`
        : `${unitCaption} · ${range} · returned values only`).className = "series-caption";
      const table = makeTable("Monthly series — complete returned values and units", ["Month", "Value"]);
      for (const point of result.series) {
        const tr = document.createElement("tr");
        cell(tr, `${point.period.slice(0, 4)}-${point.period.slice(4)}`);
        cell(tr, point.status === "unavailable" ? `Unavailable: Value unavailable · ${point.unit}` : formatMetric(point));
        table.body.append(tr);
      }
      const chart = renderSeriesChart(result.series, sfoSeries ? { title: headingText, noun: "passengers", axis: "Passengers per month" } : {});
      if (chart) seriesPanel.append(chart);
      else paragraph(seriesPanel, "Series not plotted: no usable returned count values.");
      methodologyExtras.push(["Exact monthly values", [table.region]]);
    } else paragraph(seriesPanel, "No monthly series was returned.");
    if (["sfo_pressure", "sfo_enplaned_trend"].includes(result.scope.metric)) resultPanel.insertBefore(seriesPanel, dashboard);
    else dashboard.append(seriesPanel);
  }
  const { group: evidenceGroup, heading: evidenceHeading } = renderEvidencePanel(result);
  resultPanel.append(evidenceGroup, renderMethodologyPanel(result), renderTechnicalPanel(result));
  $("#view-evidence").onclick = () => { evidenceGroup.open = true; evidenceHeading.focus(); };
  updateContextStrip(result, previous);
  publishResultState();
  refreshChatViewLinks();
}

// Exclusions are reported as coverage ("22 of 23 airports assessed"), calmly and
// without hiding them; the exact reasons stay under Methodology & limitations.
function coverageNote(result) {
  if (!result.exclusions.length) return null;
  const codes = result.exclusions.map((item) => /^([A-Z]{3})\b/.exec(item.trim())?.[1]).filter(Boolean);
  const why = result.exclusions.every((item) => /incomplete/i.test(item)) ? "required data was incomplete" : "required data was unavailable";
  const named = codes.length ? codes.join(", ") : `${result.exclusions.length} airport${result.exclusions.length === 1 ? "" : "s"}`;
  const verb = codes.length > 1 || (!codes.length && result.exclusions.length > 1) ? "were" : "was";
  const assessed = result.rows.length < result.scope.airports.length ? `${result.rows.length} of ${result.scope.airports.length} airports assessed. ` : "";
  return `Coverage · ${assessed}${named} ${verb} excluded because ${why}.`;
}

// A leading "Label:" in the returned summary is emphasised; the text itself is
// rendered verbatim (split, never rewritten).
function renderInsightSummary(parent, text) {
  const node = document.createElement("p");
  node.className = "insight-summary";
  const match = /^([^:.]{3,40}:)\s(.+)$/s.exec(text);
  if (match) {
    const lead = document.createElement("strong");
    lead.textContent = match[1];
    node.append(lead);
    const rest = document.createElement("span");
    rest.textContent = ` ${match[2]}`;
    node.append(rest);
  } else node.textContent = text;
  parent.append(node);
  return node;
}

function disclosure(className, title) {
  const group = document.createElement("details");
  group.className = className;
  group.open = false;
  heading(group, "summary", title);
  return group;
}

function renderEvidencePanel(result) {
  const group = disclosure("evidence-source-group", "Evidence & sources");
  const evidenceHeading = heading(group, "h3", "Evidence and counterevidence");
  evidenceHeading.id = "analysis-evidence";
  evidenceHeading.tabIndex = -1;
  if (!result.evidence.length) paragraph(group, "No reviewed evidence was returned for this result; traffic alone does not establish an investment case.");
  for (const evidence of result.evidence) {
    const item = document.createElement("div");
    item.className = "evidence-item";
    const source = result.sources.find((entry) => entry.id === evidence.source_id);
    paragraph(item, `${source?.name || "Reviewed source"} · ${evidence.date}`).className = "evidence-source";
    paragraph(item, `Evidence: ${evidence.claim}`);
    paragraph(item, `Locator: ${evidence.locator}`);
    paragraph(item, `Limitation / counterevidence: ${evidence.limitation}`);
    if (!source) paragraph(item, `Source ID: ${evidence.source_id}`);
    group.append(item);
  }
  if (result.scope.metric === "sfo_pressure") paragraph(group, "These signals do not quantify or establish unmet demand.");
  heading(group, "h3", "Sources");
  if (!result.sources.length) {
    paragraph(group, "No source references were returned. Treat the result as incomplete and review the scope or run another supported example.");
    const action = document.createElement("button");
    action.type = "button";
    action.textContent = "Review scope";
    action.addEventListener("click", openSetupAndScope);
    group.append(action);
  }
  for (const source of result.sources) {
    const item = document.createElement("div");
    item.className = "source-item";
    paragraph(item, source.name).className = "evidence-source";
    paragraph(item, `Observation period: ${source.period} · Retrieved: ${source.retrieved_at || "Not supplied"}`);
    if (isSafeHttpUrl(source.url)) {
      const link = document.createElement("a");
      link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.textContent = "Open cited source (new tab)"; item.append(link);
    }
    group.append(item);
  }
  return { group, heading: evidenceHeading };
}

function renderMethodologyPanel(result) {
  const group = disclosure("methodology-group", "Methodology & limitations");
  paragraph(group, `Population: ${result.scope.population}`);
  if (result.scope.threshold_miles != null) paragraph(group, `Long-haul threshold: ${formatNumber(result.scope.threshold_miles)} miles.`);
  for (const [title, nodes] of methodologyExtras) {
    heading(group, "h3", title);
    group.append(...nodes);
  }
  heading(group, "h3", "Values, denominators and directions");
  const values = document.createElement("div");
  values.className = "methodology-detail";
  for (const row of result.rows) for (const metric of row.metrics) {
    const parts = [`${row.airport} · ${humanMetricLabel(metric.key)}`, metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : `Value: ${formatMetric(metric)}`];
    if (Number.isInteger(row.rank)) parts.push(`Rank ${row.rank}`);
    if (metric.numerator != null && metric.denominator != null) parts.push(`Numerator / denominator: ${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`);
    if (metric.eligible_count != null) parts.push(`Eligible observations: ${formatNumber(metric.eligible_count)}`);
    if (metric.comparison_direction) parts.push(`Direction: ${label(metric.comparison_direction)}`);
    paragraph(values, parts.join(" · "));
  }
  if (!values.children.length) paragraph(values, "No metric values were returned.");
  group.append(values);
  listSection(group, "Exclusions", result.exclusions);
  listSection(group, "Limitations", result.limitations);
  return group;
}

// Identifiers (request, result, bundle, source and snapshot IDs) live only here.
function renderTechnicalPanel(result) {
  const group = disclosure("technical-details", "Technical details");
  paragraph(group, `Request ID: ${result.request_id}`);
  paragraph(group, `Result ID: ${result.result_id}`);
  if (result.scope.bundle_id != null) paragraph(group, `Bundle: ${result.scope.bundle_id}`);
  for (const row of result.rows) for (const metric of row.metrics) {
    paragraph(group, `${row.airport} · ${humanMetricLabel(metric.key)} · Source IDs: ${metric.source_ids.join(", ") || "None returned"}`);
  }
  for (const source of result.sources) paragraph(group, `${source.name} · Source ID: ${source.id} · Snapshot: ${source.snapshot_id}`);
  return group;
}

function renderKpiRow(result) {
  if (["congestion", "screen_score", "long_haul_share", "overview"].includes(result.scope.metric)) return null;
  const group = document.createElement("section");
  group.className = "kpi-row";
  group.setAttribute("aria-label", "Key figures");
  // A ranking leads with its two ends on the ranked measure; the full order follows.
  if (result.rows.some((row) => Number.isInteger(row.rank))) return renderRankingKpis(result, group);
  const metricsByKey = new Map();
  for (const row of result.rows) for (const metric of row.metrics) {
    if (!metricsByKey.has(metric.key)) metricsByKey.set(metric.key, []);
    metricsByKey.get(metric.key).push({ airport: row.airport, metric });
  }
  // SFO pressure shows the complete relationship: passenger growth, seat growth,
  // the gap between them and occupancy (the rest stays in Methodology). Every other
  // metric leads with the requested key, then whichever metrics the rows carry.
  const candidates = result.scope.metric === "sfo_pressure"
    ? ["passenger_growth", "seat_growth", "sfo_pressure", "seat_occupancy"]
    : [result.scope.metric, ...metricsByKey.keys()];
  const keys = [...new Set(candidates)].filter((key) => metricsByKey.has(key)).slice(0, 4);
  if (!keys.length) return null;
  for (const key of keys) group.append(kpiCard(key, metricsByKey.get(key).slice(0, 4), result.scope));
  return group;
}

// One card: WHAT is measured (title), the figure with its UNIT, and a short grey
// line with the comparison, denominator or timeframe. Built only from the returned
// value and its resolved scope.
function kpiCard(key, items, scope, title = null) {
  const card = document.createElement("article");
  card.className = "kpi-card";
  card.setAttribute("data-metric-key", key);
  heading(card, "h3", title || metricLabel(key, scope));
  const many = items.length > 1;
  for (const item of items) {
    const line = document.createElement("div");
    line.className = "kpi-value-line";
    if (many || item.showAirport) {
      const airport = document.createElement("span");
      airport.className = "kpi-airport";
      airport.textContent = item.airport;
      line.append(airport);
    }
    const value = document.createElement("strong");
    value.className = item.metric.status === "unavailable" ? "kpi-value is-unavailable" : "kpi-value";
    if (item.metric.status === "unavailable") value.textContent = "—";
    else appendFigure(value, metricFigure(item.metric));
    line.append(value);
    card.append(line);
    if (item.metric.status === "unavailable") paragraph(card, item.metric.reason).className = "kpi-reason";
    else if (many) {
      const detail = metricDetail(item.metric, scope);
      if (detail) paragraph(card, detail).className = "kpi-detail";
    } else paragraph(card, item.context || metricContext(item.metric, scope)).className = "kpi-context";
  }
  const shown = items.find((item) => item.metric.status === "ok");
  // The shared line is skipped when every airport line already states the comparison.
  const allDetailed = items.every((item) => item.metric.status === "ok" && metricDetail(item.metric, scope).includes(" vs. "));
  if (many && shown && !allDetailed) paragraph(card, metricContext({ ...shown.metric, numerator: null, denominator: null }, scope)).className = "kpi-context";
  return card;
}

function renderRankingKpis(result, group) {
  const key = result.scope.metric;
  const ranked = result.rows
    .filter((row) => Number.isInteger(row.rank))
    .map((row) => ({ row, metric: row.metrics.find((item) => item.key === key) }))
    .filter(({ metric }) => metric?.status === "ok" && Number.isFinite(metric.value))
    .sort((a, b) => a.row.rank - b.row.rank);
  if (!ranked.length) return null;
  const label = metricLabel(key, result.scope).toLowerCase();
  const values = ranked.map(({ metric }) => metric.value);
  const ends = [["Highest", ranked[0]], ...(ranked.length > 1 ? [["Lowest", ranked.at(-1)]] : [])];
  for (const [end, { row, metric }] of ends) {
    // "Highest"/"Lowest" only when the returned values say so; otherwise the rank speaks.
    const truthful = end === "Highest" ? metric.value === Math.max(...values) : metric.value === Math.min(...values);
    const title = truthful ? `${end} ${label}` : `Rank ${row.rank} · ${label}`;
    const context = `${metricContext({ ...metric, numerator: null, denominator: null }, result.scope)} · rank ${row.rank} of ${ranked.length} ranked`;
    group.append(kpiCard(key, [{ airport: row.airport, metric, showAirport: true, context }], result.scope, title));
  }
  return group;
}

// The SFO answer in one deterministic sentence, chosen by the sign of the returned gap.
function renderTakeaway(result) {
  if (result.scope.metric !== "sfo_pressure") return null;
  const gap = result.rows.find((row) => row.airport === "SFO")?.metrics.find((item) => item.key === "sfo_pressure");
  if (gap?.status !== "ok" || !Number.isFinite(gap.value)) return null;
  const year = result.scope.year;
  const rounded = Number(gap.value.toFixed(2));
  const answer = rounded < 0
    ? `Seat supply grew faster than passenger traffic in ${year}, so these data do not show a shortage of airline seat capacity.`
    : rounded > 0
      ? `Passenger traffic grew faster than seat supply in ${year}: a sign of pressure on airline seat capacity, not a measure of unmet demand.`
      : `Passenger traffic and seat supply grew at the same pace in ${year}, so these data show no added pressure on airline seat capacity.`;
  const node = document.createElement("p");
  node.className = "result-takeaway";
  node.textContent = `${answer} Latent demand cannot be measured from observed traffic alone.`;
  return node;
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
  updateChatPlaceholder();
  // Opening shows the collapsed bar; clicking it or typing raises the card.
  if (takeFocus) $("#question").focus();
}

let composerCloseTimer = null;
function closeQuestionComposer(returnFocus = true) {
  const composer = $("#conversation-composer");
  if (composer.hidden) return;
  clearTimeout(chatCollapseTimer);
  for (const name of ["is-open", "is-expanded", "is-collapsing"]) composer.classList.remove(name);
  document.body?.classList?.remove("chat-expanded");
  document.body?.classList?.remove("composer-open");
  composer.setAttribute("aria-hidden", "true");
  composer.inert = true;
  askTrigger.setAttribute("aria-expanded", "false");
  askTrigger.hidden = false;
  clearTimeout(composerCloseTimer);
  composerCloseTimer = setTimeout(() => { composer.hidden = true; }, window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 210);
  if (returnFocus) askTrigger.focus();
}

// ── Rising chat card ──────────────────────────────────────────────────────────
// The composer bar is the card's collapsed state. Clicking the bar or typing
// expands it into the transcript; Escape or the header button collapses it back.
// The card sits outside #result, so renderResult() never touches the transcript.
const CHAT_MOTION_MS = 200;
let chatCollapseTimer = null;
// A card that is animating down already counts as collapsed, so a second Escape
// during the motion closes the bar instead of restarting the collapse.
function chatExpanded() {
  const composer = $("#conversation-composer");
  return composer.classList.contains("is-expanded") && !composer.classList.contains("is-collapsing");
}
function updateChatPlaceholder() {
  $("#question").setAttribute("placeholder", conversation.length ? "Ask a follow-up…" : "Ask the airport analyst…");
  $("#chat-empty").hidden = conversation.length > 0;
}
// Scroll anchoring: the transcript is top-anchored, so an arriving message never
// moves the ones above it. The user's own message always scrolls into view; a
// reply follows only when the reader was already at (or within 48px of) the end,
// measured before it lands. Programmatic scrolls are instant (no smooth race).
const CHAT_FOLLOW_PX = 48;
function transcriptNearBottom() {
  const body = $(".chat-body");
  if (!body || !Number.isFinite(body.scrollHeight)) return false;
  return body.scrollHeight - body.scrollTop - body.clientHeight <= CHAT_FOLLOW_PX;
}
function scrollTranscriptToEnd() {
  const body = $(".chat-body");
  if (!body || !Number.isFinite(body.scrollHeight)) return;
  body.scrollTop = body.scrollHeight;
  // Controls added to a reply right after it settles (voice.js adds Read aloud from a
  // mutation callback) land after this scroll; follow them on the next frame too.
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
}
// One line, growing to about five, then scrolling inside the field. If the reader
// was at the end of the transcript, growth keeps the latest message in view.
function fitComposer() {
  const question = $("#question");
  if (!question.style || !Number.isFinite(question.scrollHeight)) return;
  const follow = transcriptNearBottom();
  question.style.height = "auto";
  question.style.height = `${Math.min(question.scrollHeight, 132)}px`;
  if (follow) scrollTranscriptToEnd();
}
// The card grows with the conversation: compact at first, comfortable once two or
// more questions no longer fit, and focus when the analyst enlarges it. It never
// shrinks on its own. A size change is revealed with clip-path, never animated height.
let chatAutoSize = "compact";
let chatEnlarged = false;
function chatSize() { return chatEnlarged ? "focus" : chatAutoSize; }
let chatResizeAnimation = null;
function applyChatSize() {
  const composer = $("#conversation-composer");
  const size = chatSize();
  const previous = composer.getAttribute("data-size") || "compact";
  // A shrink in flight still shows its larger size; cancel it first so it cannot
  // land on a size the analyst has since toggled away from.
  chatResizeAnimation?.cancel();
  chatResizeAnimation = null;
  if (previous === size) return;
  const follow = transcriptNearBottom();
  const resize = (value) => {
    composer.setAttribute("data-size", value);
    document.body?.setAttribute?.("data-chat-size", value);
    if (follow) scrollTranscriptToEnd();
  };
  const before = composer.getBoundingClientRect?.().height;
  resize(size);
  if (!chatExpanded() || prefersReducedMotion() || typeof composer.animate !== "function" || !(before > 0)) return;
  const after = composer.getBoundingClientRect().height;
  const radius = window.matchMedia?.("(max-width: 600px)").matches ? "round 18px 18px 0 0" : "round 18px";
  const clip = (top) => `inset(${top}px 0 0 0 ${radius})`;
  const timing = { duration: CHAT_RESIZE_MS, easing: "cubic-bezier(.2,.8,.2,1)" };
  if (after > before) {
    chatResizeAnimation = composer.animate([{ clipPath: clip(after - before) }, { clipPath: clip(0) }], timing);
  } else if (after < before) {
    // Shrinking: hold the larger size while the top edge lowers, then take the smaller one.
    resize(previous);
    const animation = composer.animate([{ clipPath: clip(0) }, { clipPath: clip(before - after) }], { ...timing, fill: "forwards" });
    chatResizeAnimation = animation;
    animation.finished.then(() => { resize(size); animation.cancel(); }, () => {});
  }
}
const CHAT_RESIZE_MS = 220;
function maybeGrowChat() {
  if (chatAutoSize !== "compact" || !chatExpanded()) return;
  const body = $(".chat-body");
  const asked = conversation.filter((message) => message.role === "user").length;
  if (asked < 2 || !body || !Number.isFinite(body.scrollHeight) || body.scrollHeight <= body.clientHeight + 8) return;
  chatAutoSize = "comfortable";
  applyChatSize();
}
function toggleChatEnlarged() {
  chatEnlarged = !chatEnlarged;
  $("#chat-resize").setAttribute("aria-pressed", String(chatEnlarged));
  applyChatSize();
}
function expandChat(takeFocus = true) {
  const composer = $("#conversation-composer");
  if (composer.hidden || composer.inert) return;
  clearTimeout(chatCollapseTimer);
  composer.classList.remove("is-collapsing");
  composer.classList.add("is-expanded");
  document.body?.classList?.add("chat-expanded");
  $("#chat-collapse").setAttribute("aria-expanded", "true");
  updateChatPlaceholder();
  scrollTranscriptToEnd();
  maybeGrowChat();
  if (takeFocus) $("#question").focus();
}
function collapseChat(returnFocus = true) {
  const composer = $("#conversation-composer");
  if (!chatExpanded()) return;
  clearTimeout(chatCollapseTimer);
  $("#chat-collapse").setAttribute("aria-expanded", "false");
  document.body?.classList?.remove("chat-expanded");
  const finish = () => { composer.classList.remove("is-expanded"); composer.classList.remove("is-collapsing"); };
  if (prefersReducedMotion()) finish();
  else {
    composer.classList.add("is-collapsing");
    chatCollapseTimer = setTimeout(finish, CHAT_MOTION_MS);
  }
  if (returnFocus) $("#question").focus();
}

// States: user "sent" | "failed"; assistant "pending" | "done" | "note" (an
// unsupported or unclear question, answered in words). The history is a role="log"
// read in DOM order, not a live region; outcomes are announced once in #chat-status.
function addChatMessage(role, text, options = {}) {
  const follow = options.follow ?? (role === "user" || transcriptNearBottom());
  const message = { role, text, state: options.state || (role === "user" ? "sent" : "done"), resultId: options.resultId || null, turn: null, node: null, textNode: null, link: null, failure: null };
  const item = document.createElement("li");
  item.className = `chat-message chat-message-${role}`;
  const speaker = document.createElement("span");
  speaker.className = "visually-hidden";
  speaker.textContent = role === "user" ? "You: " : "Analyst: ";
  item.append(speaker);
  if (options.context) paragraph(item, options.context).className = "chat-context";
  message.textNode = paragraph(item, text);
  message.textNode.className = "chat-text";
  if (role === "assistant") {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "chat-view-link";
    link.textContent = "View analysis →";
    link.hidden = true;
    link.addEventListener("click", viewAnalysis);
    item.append(link);
    message.link = link;
  } else {
    // A failed turn keeps its text and offers a real Retry button on the message.
    const failure = document.createElement("p");
    failure.className = "chat-failure";
    failure.hidden = true;
    const note = document.createElement("span");
    note.textContent = "Not sent · ";
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "chat-retry";
    retry.textContent = "Retry";
    retry.addEventListener("click", () => retryChatTurn(message.turn));
    failure.append(note, retry);
    item.append(failure);
    message.failure = failure;
  }
  message.node = item;
  conversation.push(message);
  $("#chat-transcript").append(item);
  setChatState(message, message.state);
  updateChatPlaceholder();
  refreshChatViewLinks();
  if (follow) scrollTranscriptToEnd();
  maybeGrowChat();
  return message;
}
function setChatState(message, state) {
  message.state = state;
  message.node.setAttribute("data-state", state);
  if (message.role === "assistant") message.node.setAttribute("aria-busy", String(state === "pending"));
  if (message.failure) message.failure.hidden = state !== "failed";
}
// Only the sent text is cleared; a different draft in the field is left alone. The
// send that emptied the field owns it until a newer send or Back takes it over.
function clearSentQuestion(message, generation) {
  const question = $("#question");
  if (question.value.trim() !== message) return;
  question.value = "";
  fitComposer();
  composerOwner = { generation, message };
}
// A failed or stopped send ("Not sent · Retry") gives its text back for editing,
// but only while it still owns the field and the analyst has not started another question.
function restoreFailedQuestion(generation) {
  if (composerOwner?.generation !== generation) return;
  const { message } = composerOwner;
  composerOwner = null;
  const question = $("#question");
  if (question.value.trim()) return;
  question.value = message;
  fitComposer();
}
function startChatTurn(text, retryOf) {
  if (retryOf) {
    setChatState(retryOf.user, "sent");
    retryOf.pending.text = "Analyzing…";
    retryOf.pending.textNode.textContent = "Analyzing…";
    setChatState(retryOf.pending, "pending");
    return retryOf;
  }
  const user = addChatMessage("user", text);
  // The working indicator takes the slot the reply will land in (same user action).
  const pending = addChatMessage("assistant", "Analyzing…", { state: "pending", follow: true });
  const turn = { user, pending };
  user.turn = turn;
  pending.turn = turn;
  expandChat(false);
  return turn;
}
function settleChatReply(turn, text, state, resultId = null) {
  if (!turn) return;
  const follow = transcriptNearBottom();
  turn.pending.text = text;
  turn.pending.resultId = resultId;
  turn.pending.textNode.textContent = text;
  setChatState(turn.pending, state);
  refreshChatViewLinks();
  if (follow) scrollTranscriptToEnd();
  maybeGrowChat();
  // Notes are also the field's alert; announcing them here too would read them twice.
  if (state === "done") $("#chat-status").textContent = `Analyst: ${text}`;
}
function failChatTurn(turn, reason) {
  if (!turn) return;
  const follow = transcriptNearBottom();
  turn.pending.text = reason;
  turn.pending.resultId = null;
  turn.pending.textNode.textContent = reason;
  setChatState(turn.pending, "error");
  setChatState(turn.user, "failed");
  refreshChatViewLinks();
  if (follow) scrollTranscriptToEnd();
}
// The measures the server offered for a pending pair, as buttons on its reply. A
// choice runs that comparison directly (no model call); typing an answer still works.
const operationalAirports = new Set(["LAX", "SNA", "SFO"]);
let chatChoices = null;
function comparisonChoices(pair) {
  const choices = [["Overall comparison", "overview"], ["Passenger growth", "passenger_growth"], ["Seat occupancy", "seat_occupancy"],
    ["Passengers", "passengers"], ["Long-haul share", "long_haul_share"]];
  if (pair.every((code) => operationalAirports.has(code))) choices.push(["Congestion", "congestion"]);
  return choices;
}
// A chosen measure keeps the period on screen, as a typed answer does, unless that
// measure has no such period (growth, the overview and congestion need 2024 or 2025).
const choiceNeedsComparisonYear = new Set(["overview", "passenger_growth", "congestion"]);
function choiceAnalysis(pair, metric) {
  const analysis = { action: "compare", airports: [...pair], metric };
  const year = latestSuccessfulResult?.scope?.year;
  if (SUPPORTED_YEARS.includes(year) && year !== DEFAULT_YEAR
      && !(year === 2023 && choiceNeedsComparisonYear.has(metric))) analysis.year = year;
  return analysis;
}
function offerChatChoices(message, pair) {
  const group = document.createElement("div");
  group.className = "chat-choices";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", `Measures to compare for ${pair[0]} and ${pair[1]}`);
  for (const [text, metric] of comparisonChoices(pair)) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "chat-choice";
    button.textContent = text;
    button.addEventListener("click", () => {
      if (busy) return;
      $("#question").focus({ preventScroll: true });
      void submitRequest({ analysis: choiceAnalysis(pair, metric) }, { chatText: text });
    });
    group.append(button);
  }
  const follow = transcriptNearBottom();
  message.node.append(group);
  chatChoices = group;
  if (follow) scrollTranscriptToEnd();
  maybeGrowChat();
}
// Once anything new is asked, an older set of choices no longer answers the question.
function retireChatChoices() {
  if (!chatChoices) return;
  chatChoices.hidden = true;
  chatChoices = null;
}
function followUpRequest(text) {
  const request = { message: text };
  if (contextResultId) request.context_result_id = contextResultId;
  if (pendingComparison) request.pending_comparison = [...pendingComparison];
  return request;
}
// Retry re-sends the same text on the same turn; one request stays in flight.
function retryChatTurn(turn) {
  if (busy || !turn || turn.user.state !== "failed") return;
  // The question is going out again: dictation still arriving must not refill the field.
  window.airportVoice?.discardListening?.();
  clearQuestionError();
  // The Retry button hides once the turn is re-sent; keep focus in the composer
  // instead of letting it fall back to the page.
  $("#question").focus({ preventScroll: true });
  void submitRequest(turn.request || followUpRequest(turn.user.text), { retryOf: turn, chatText: turn.request ? turn.user.text : undefined });
}
// Every reply that produced a result keeps its link row, so a newer answer never
// changes the height of the messages above it. Only the reply behind the result on
// screen is live; earlier ones stay in place, labelled and inactive.
function refreshChatViewLinks() {
  const shownId = latestSuccessfulResult && !$("#result-panel").hidden ? latestSuccessfulResult.result_id : null;
  for (const message of conversation) {
    if (!message.link) continue;
    message.link.hidden = message.state !== "done" || !message.resultId;
    const live = Boolean(shownId) && message.resultId === shownId;
    message.link.textContent = live ? "View analysis →" : "Earlier analysis";
    message.link.setAttribute("aria-disabled", String(!live));
  }
}
function viewAnalysis(event) {
  if (event?.currentTarget?.getAttribute?.("aria-disabled") === "true") return;
  const title = $("#result-title");
  if (!latestSuccessfulResult || title.hidden) return;
  const behavior = prefersReducedMotion() ? "auto" : "smooth";
  // A narrow card covers much of the page, so it steps aside and the page scrolls
  // to the title; on desktop the result pane returns to its top.
  if (window.matchMedia?.("(max-width: 999px)").matches) {
    collapseChat(false);
    title.scrollIntoView?.({ block: "start", behavior });
  } else scrollResultIntoView();
  title.focus({ preventScroll: true });
}

// Deterministic one-line replies, built only from the returned result's values.
function joinAnd(items) {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}
function compactReply(result) {
  const { metric, airports } = result.scope;
  const rowFor = (code) => result.rows.find((row) => row.airport === code);
  const valueOf = (row, key) => row?.metrics.find((item) => item.key === key);
  const usable = (item) => item?.status === "ok" && Number.isFinite(item.value);
  const fallback = result.summary || "The analysis is ready in the result panel.";
  if (metric === "sfo_pressure") {
    // The backend's direct answer also names the reported delay causes (the "why").
    if (result.summary) return result.summary;
    // The gap is passenger growth minus seat growth, in percentage points: seats
    // outpacing passengers is not a seat shortage; only a positive gap is pressure.
    const row = rowFor("SFO");
    const gap = valueOf(row, "sfo_pressure");
    const growth = valueOf(row, "passenger_growth");
    const occupancy = valueOf(row, "seat_occupancy");
    if (!usable(gap) || !usable(growth)) return fallback;
    const points = `${formatNumber(Math.abs(gap.value))} percentage points`;
    const occupied = usable(occupancy) ? `, with seat occupancy at ${formatMetric(occupancy)}` : "";
    const rounded = Math.round(gap.value * 100) / 100;
    if (rounded < 0) return `No sign that seat supply fell behind: seats grew ${points} faster than passengers (${formatMetric(growth)})${occupied}. Traffic data cannot show travellers who could not fly.`;
    if (rounded > 0) return `Passengers grew ${points} faster than seats (${formatMetric(growth)})${occupied}: a demand-pressure signal, not a measured unmet demand.`;
    return `Passengers and seats grew at the same pace (${formatMetric(growth)})${occupied}. Traffic data cannot show travellers who could not fly.`;
  }
  // The backend's congestion headline already names which airport is higher on what.
  if (metric === "sfo_enplaned_trend" || metric === "congestion") return fallback;
  if (metric === "overview") return overviewReply(result) || fallback;
  if (metric === "long_haul_share" && airports.length === 1) {
    const share = valueOf(rowFor(airports[0]), "long_haul_share");
    if (!usable(share) || share.numerator == null || share.denominator == null || result.scope.threshold_miles == null) return fallback;
    return `${formatMetric(share)} of eligible ${airports[0]} departures were at least ${formatNumber(result.scope.threshold_miles)} miles: ${formatNumber(share.numerator)} of ${formatNumber(share.denominator)} scheduled passenger departures. Cargo-only and charter flights are not included.`;
  }
  const ranked = result.rows.filter((row) => Number.isInteger(row.rank) && usable(valueOf(row, metric)));
  if (ranked.length >= 2) {
    const ordered = [...ranked].sort((a, b) => a.rank - b.rank);
    const top = ordered.filter((row) => row.rank === ordered[0].rank).map((row) => row.airport);
    const next = ordered.filter((row) => row.rank !== ordered[0].rank).slice(0, 2).map((row) => row.airport);
    const lead = figureText(metricFigure(valueOf(ordered[0], metric)));
    const where = metric === "screen_score" ? `on the New England screening score (${lead})`
      : metric === "passenger_growth" ? `on passenger growth (${lead} vs. ${previousYear(result.scope)})`
        : `on ${metricLabel(metric, result.scope).toLowerCase()} (${lead} in ${result.scope.year})`;
    let text = top.length === 1 ? `${top[0]} ranks highest ${where}` : `${joinAnd(top)} share the top rank ${where}`;
    text += next.length ? `, followed by ${joinAnd(next)}.` : ".";
    if (ranked.length < airports.length) text += ` ${ranked.length} of ${airports.length} airports were assessable.`;
    return text;
  }
  const parts = airports.map((code) => {
    const item = valueOf(rowFor(code), metric);
    if (!item) return null;
    return `${code} ${usable(item) ? figureText(metricFigure(item)) : "unavailable"}`;
  }).filter(Boolean);
  if (parts.length && parts.length <= 4) return `${valueColumnTitle(result.scope)}: ${parts.join(" · ")}`;
  return fallback;
}

function overviewReply(result) {
  const { airports, year } = result.scope;
  if (airports.length !== 2) return null;
  const [first, second] = airports.map((code) => result.rows.find((row) => row.airport === code));
  if (!first || !second) return null;
  const higher = { [first.airport]: [], [second.airport]: [] };
  let compared = 0;
  for (const metric of first.metrics) {
    if (!["higher", "lower", "tied"].includes(metric.comparison_direction)) continue;
    compared += 1;
    if (metric.comparison_direction === "higher") higher[first.airport].push(humanMetricLabel(metric.key).toLowerCase());
    else if (metric.comparison_direction === "lower") higher[second.airport].push(humanMetricLabel(metric.key).toLowerCase());
  }
  if (!compared) return null;
  const named = (labels) => labels.length <= 3 ? joinAnd(labels) : `${labels.length} of ${compared} measures, including ${joinAnd(labels.slice(0, 2))}`;
  const parts = Object.entries(higher).filter(([, labels]) => labels.length).map(([code, labels]) => `${code} is higher on ${named(labels)}`);
  const operations = first.metrics.some((metric) => operationalMetrics.has(metric.key));
  return `${airports[0]} vs ${airports[1]} in ${year}: ${parts.join("; ") || "tied on every comparable measure"}.`
    + (operations ? " For the operational measures, higher means more disruption." : "")
    + " The side-by-side view has every value.";
}

function describePeriod(scope) {
  const compared = ["passenger_growth", "screen_score", "sfo_pressure", "sfo_enplaned_trend", "overview"].includes(scope.metric);
  const period = compared ? `Calendar year ${scope.year} compared with ${previousYear(scope)}` : `Calendar year ${scope.year}`;
  return scope.bundle_id == null ? `${period} · historical data` : `${period} · accepted data release`;
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
  // Exact values, denominators and source IDs for every renderer are listed in
  // the collapsed methodology and technical disclosures below the result.
  if (typeof renderer === "function" && renderer(result, target) !== false) return;
  renderGenericMetricTable(result, target);
}
function renderSeriesChart(series, labels = {}) {
  const usable = series.filter(point => point.status === "ok" && Number.isFinite(point.value));
  if (!usable.length || usable.some(point => point.unit !== "count")) return null;
  const points = series.map((point, index) => ({ point, index, month: Number(point.period.slice(4)), year: Number(point.period.slice(0, 4)) }))
    .filter(({ point }) => point.status === "ok" && Number.isFinite(point.value));
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  // A viewBox close to the rendered width keeps axis text near its CSS size.
  const width = 640, height = 280, left = 74, right = 16, top = 16, bottom = 36;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  const titleId = `series-chart-title-${series[0].period}`;
  const descId = `series-chart-description-${series[0].period}`;
  svg.setAttribute("aria-labelledby", `${titleId} ${descId}`);
  const noun = labels.noun || "";
  const exact = (value) => `${formatNumber(value)}${noun ? ` ${noun}` : ""}`;
  const low = points.reduce((best, item) => item.point.value < best.point.value ? item : best, points[0]);
  const high = points.reduce((best, item) => item.point.value > best.point.value ? item : best, points[0]);
  const title = document.createElementNS(ns, "title");
  title.id = titleId; title.textContent = labels.title || "Returned monthly values";
  const description = document.createElementNS(ns, "desc");
  description.id = descId;
  description.textContent = `${monthLabel(series[0].period)} to ${monthLabel(series.at(-1).period)}. Lowest month ${monthLabel(low.point.period)}: ${exact(low.point.value)}. Highest month ${monthLabel(high.point.period)}: ${exact(high.point.value)}. The vertical axis starts at zero; unavailable months and gaps break the line. Exact monthly values are listed under Methodology & limitations.`;
  svg.append(title, description);
  const values = points.map(({ point }) => point.value);
  const min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const x = index => left + (series.length <= 1 ? plotWidth / 2 : index * plotWidth / (series.length - 1));
  const y = value => top + (max - value) / span * plotHeight;
  // Round gridlines (0, 1M, 2M …) plus the peak, labelled compactly; exact counts
  // stay in the tooltip, the description and the methodology table.
  const ticks = axisTicks(min, max);
  for (const value of ticks) {
    const position = y(value);
    if (value !== 0) {
      const grid = document.createElementNS(ns, "line");
      grid.setAttribute("x1", String(left)); grid.setAttribute("x2", String(width - right));
      grid.setAttribute("y1", String(position)); grid.setAttribute("y2", String(position)); grid.setAttribute("class", "series-grid"); svg.append(grid);
    }
    const tick = document.createElementNS(ns, "text");
    tick.setAttribute("x", String(left - 8)); tick.setAttribute("y", String(position + 5)); tick.setAttribute("text-anchor", "end");
    tick.setAttribute("class", "series-axis-label"); tick.textContent = formatCompact(value); svg.append(tick);
  }
  const axis = document.createElementNS(ns, "line");
  axis.setAttribute("x1", String(left)); axis.setAttribute("x2", String(width - right));
  axis.setAttribute("y1", String(y(0))); axis.setAttribute("y2", String(y(0))); axis.setAttribute("class", "series-zero-axis"); svg.append(axis);
  if (labels.axis) {
    const axisTitle = document.createElementNS(ns, "text");
    axisTitle.setAttribute("class", "series-axis-title");
    axisTitle.setAttribute("x", String(-(top + plotHeight / 2))); axisTitle.setAttribute("y", "14");
    axisTitle.setAttribute("transform", "rotate(-90)"); axisTitle.setAttribute("text-anchor", "middle");
    axisTitle.textContent = labels.axis; svg.append(axisTitle);
  }
  const yearStarts = series.map((point, index) => point.period.endsWith("01") ? index : -1).filter(index => index >= 0);
  const tickIndices = new Set([0, ...yearStarts, series.length - 1]);
  for (const index of [...tickIndices]) {
    const point = series[index]; if (!point) continue;
    const tick = document.createElementNS(ns, "text");
    tick.setAttribute("x", String(x(index))); tick.setAttribute("y", String(height - 8)); tick.setAttribute("text-anchor", index === 0 ? "start" : index === series.length - 1 ? "end" : "middle");
    tick.setAttribute("class", "series-axis-label");
    tick.textContent = monthLabel(point.period);
    svg.append(tick);
  }
  let segment = [];
  const flush = () => {
    if (segment.length > 1) {
      const line = document.createElementNS(ns, "polyline");
      line.setAttribute("points", segment.map(item => `${x(item.index)},${y(item.point.value)}`).join(" "));
      line.setAttribute("fill", "none"); line.setAttribute("class", "series-line"); svg.append(line);
    } else if (segment.length === 1) {
      const dot = document.createElementNS(ns, "circle");
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
  return attachSeriesTooltip(svg, points, { x, y, width, top, bottom: height - bottom, exact });
}
// Round ticks from zero (about three steps) plus the extreme, unless it would
// crowd the last round tick.
function axisTicks(min, max) {
  const ticks = [0];
  if (max > 0) {
    const raw = max / 3;
    const power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((multiple) => multiple * power).find((candidate) => candidate >= raw);
    for (let value = step; value <= max + step * 1e-9; value += step) ticks.push(Number(value.toPrecision(12)));
    if ((max - ticks.at(-1)) / max > .12) ticks.push(max);
  }
  if (min < 0) ticks.push(min);
  return ticks;
}
const compactFormatter = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
function formatCompact(value) { return compactFormatter.format(value); }
// Pointer tooltip for the monthly chart: nearest returned month, exact value.
// Keyboard and screen-reader users get the exact table in the methodology panel.
function attachSeriesTooltip(svg, points, geometry) {
  const wrapper = document.createElement("div");
  wrapper.className = "series-chart";
  const ns = "http://www.w3.org/2000/svg";
  const guide = document.createElementNS(ns, "line");
  guide.setAttribute("class", "series-hover-guide");
  guide.setAttribute("y1", String(geometry.top)); guide.setAttribute("y2", String(geometry.bottom));
  const marker = document.createElementNS(ns, "circle");
  marker.setAttribute("class", "series-hover-point"); marker.setAttribute("r", "5");
  const tooltip = document.createElement("div");
  tooltip.className = "series-tooltip";
  tooltip.setAttribute("aria-hidden", "true");
  tooltip.hidden = true;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const hide = () => { tooltip.hidden = true; svg.classList?.remove?.("is-hovering"); };
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect?.();
    if (!box || !box.width || !points.length) return;
    const userX = (event.clientX - box.left) * geometry.width / box.width;
    const nearest = points.reduce((best, item) => Math.abs(geometry.x(item.index) - userX) < Math.abs(geometry.x(best.index) - userX) ? item : best, points[0]);
    const cx = geometry.x(nearest.index), cy = geometry.y(nearest.point.value);
    guide.setAttribute("x1", String(cx)); guide.setAttribute("x2", String(cx));
    marker.setAttribute("cx", String(cx)); marker.setAttribute("cy", String(cy));
    tooltip.textContent = `${months[nearest.month - 1]} ${nearest.year} · ${geometry.exact ? geometry.exact(nearest.point.value) : formatMetric(nearest.point)}`;
    tooltip.hidden = false;
    const scale = box.width / geometry.width;
    tooltip.style.transform = `translate(${Math.round(cx * scale)}px, ${Math.round(cy * scale)}px)`;
    tooltip.classList.toggle("is-flipped", cx > geometry.width * .66);
    svg.classList?.add?.("is-hovering");
  });
  svg.addEventListener("pointerleave", hide);
  svg.append(guide, marker);
  wrapper.append(svg, tooltip);
  return wrapper;
}
function isNextMonth(previous, current) {
  return (current.year === previous.year && current.month === previous.month + 1)
    || (current.year === previous.year + 1 && previous.month === 12 && current.month === 1);
}
metricRenderers.congestion = renderCongestionView;
metricRenderers.screen_score = renderScreeningView;
metricRenderers.long_haul_share = renderLongHaulView;
metricRenderers.sfo_pressure = renderSfoPressureView;
metricRenderers.overview = renderOverviewView;
const overviewGroups = [
  ["Traffic", ["passengers", "passenger_growth", "seat_occupancy"]],
  ["Network", ["long_haul_share"]],
  ["Operations", ["cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"]],
];
const overviewMeaning = {
  passengers: "Passengers on departing scheduled flights",
  passenger_growth: "Change in passengers against the previous year",
  seat_occupancy: "Share of supplied seats that were filled",
  long_haul_share: "Share of departures on routes of 3,000 miles or more",
};
// Every returned measure, grouped; two airports get the same zero-based bars as the
// operations view, one airport gets a card per measure. No combined score is shown.
function renderOverviewView(result, target) {
  const airports = result.scope.airports;
  const returned = new Set(result.rows.flatMap((row) => row.metrics.map((metric) => metric.key)));
  const groups = overviewGroups.map(([name, keys]) => [name, keys.filter((key) => returned.has(key))]).filter(([, keys]) => keys.length);
  heading(target, "h3", `${airports.length === 2 ? "Key measures, side by side" : "Key measures"}, ${result.scope.year}`);
  if (airports.length === 2) {
    const legend = document.createElement("p");
    legend.className = "congestion-legend";
    airports.forEach((airport, airportIndex) => {
      const entry = document.createElement("span");
      entry.className = "legend-entry";
      const swatch = document.createElement("span");
      swatch.className = `series-key series-key-${airportIndex + 1}`;
      swatch.setAttribute("aria-hidden", "true");
      const name = document.createElement("span");
      name.textContent = airport;
      entry.append(swatch, name);
      legend.append(entry);
    });
    const note = document.createElement("span");
    note.className = "legend-note";
    note.textContent = "Bars start at zero. Higher traffic is not better or worse on its own; for operations, higher means more disruption.";
    legend.append(note);
    target.append(legend);
  }
  const table = makeTable("Exact values by airport", ["Measure", ...airports]);
  table.region.className += " congestion-table-region";
  for (const [name, keys] of groups) {
    const section = document.createElement("section");
    section.className = "overview-group";
    heading(section, "h3", name).className = "overview-group-title";
    if (airports.length === 2) {
      const bars = document.createElement("div");
      bars.className = "congestion-bars";
      bars.setAttribute("role", "list");
      for (const key of keys) appendMeasureBars(bars, table, result, key, humanMetricLabel(key), overviewMeaning[key] || congestionMeaning[key], "h4");
      section.append(bars);
    } else {
      const cards = document.createElement("div");
      cards.className = "kpi-row overview-cards";
      const row = result.rows[0];
      for (const key of keys) {
        const metric = row.metrics.find((item) => item.key === key);
        cards.append(kpiCard(key, [{ airport: row.airport, metric }], result.scope));
        const tr = document.createElement("tr");
        cell(tr, humanMetricLabel(key));
        cell(tr, metric.status === "ok" ? formatMetric(metric) : `Unavailable: ${metric.reason}`);
        table.body.append(tr);
      }
      section.append(cards);
    }
    target.append(section);
  }
  methodologyExtras.push(["Exact values", [table.region]]);
  return true;
}
function renderSfoPressureView(result, target) {
  // The KPI row, the takeaway and the monthly chart carry SFO pressure; the
  // remaining returned indicators are listed under Methodology & limitations.
  return result.scope.metric === "sfo_pressure";
}
function renderLongHaulView(result, target) {
  for (const airport of result.scope.airports) {
    const block = document.createElement("div");
    block.className = "long-haul-result";
    // A single-airport result already names the airport in the result title.
    if (result.scope.airports.length > 1) heading(block, "h3", airport);
    const metric = result.rows.find((row) => row.airport === airport)?.metrics.find((item) => item.key === "long_haul_share");
    if (!metric) paragraph(block, "Not returned.");
    else if (metric.status === "unavailable") {
      paragraph(block, `Unavailable: ${metric.reason}`);
      if (metric.numerator != null && metric.denominator != null) {
        paragraph(block, `Long-haul performed departures: ${formatNumber(metric.numerator)}`);
        paragraph(block, `Eligible performed departures: ${formatNumber(metric.denominator)}`);
      }
    } else {
      const share = heading(block, "h3", "Long-haul share");
      share.className = "long-haul-share-label";
      const threshold = result.scope.threshold_miles == null ? null : formatNumber(result.scope.threshold_miles);
      const answer = paragraph(block, `${formatMetric(metric)} of eligible ${airport} departures flew routes of ${threshold == null ? "the long-haul threshold" : `${threshold} miles`} or more.`);
      answer.className = "long-haul-answer";
      const figure = document.createElement("div");
      figure.className = "long-haul-figure";
      const track = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      track.setAttribute("class", "share-ring");
      track.setAttribute("viewBox", "0 0 168 168");
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", `${airport} returned long-haul share ${formatMetric(metric)}`);
      const circumference = 2 * Math.PI * 64; // viewBox matches the rendered 168px, so SVG text sizes are real pixels
      const ringTrack = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ringTrack.setAttribute("cx", "84"); ringTrack.setAttribute("cy", "84"); ringTrack.setAttribute("r", "64"); ringTrack.setAttribute("class", "share-ring-track");
      const fill = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      fill.setAttribute("cx", "84"); fill.setAttribute("cy", "84"); fill.setAttribute("r", "64"); fill.setAttribute("class", "share-ring-value");
      fill.setAttribute("stroke-dasharray", `${circumference * Math.max(0, Math.min(100, metric.value)) / 100} ${circumference}`);
      fill.setAttribute("transform", "rotate(-90 84 84)");
      const centerValue = document.createElementNS("http://www.w3.org/2000/svg", "text");
      centerValue.setAttribute("class", "share-ring-center");
      centerValue.setAttribute("x", "84"); centerValue.setAttribute("y", "78");
      centerValue.setAttribute("text-anchor", "middle"); centerValue.setAttribute("dominant-baseline", "middle");
      centerValue.setAttribute("fill", "currentColor"); centerValue.setAttribute("font-size", "26"); centerValue.setAttribute("font-weight", "600");
      centerValue.textContent = formatMetric(metric);
      const centerCaption = document.createElementNS("http://www.w3.org/2000/svg", "text");
      centerCaption.setAttribute("class", "share-ring-caption");
      centerCaption.setAttribute("x", "84"); centerCaption.setAttribute("y", "104");
      centerCaption.setAttribute("text-anchor", "middle"); centerCaption.setAttribute("dominant-baseline", "middle");
      centerCaption.setAttribute("font-size", "13"); centerCaption.setAttribute("aria-hidden", "true");
      centerCaption.textContent = "of departures";
      track.append(ringTrack, fill, centerValue, centerCaption);
      const stats = document.createElement("dl");
      stats.className = "long-haul-stats";
      const stat = (value, name) => {
        const item = document.createElement("div");
        const term = document.createElement("dt"); term.textContent = name;
        const detail = document.createElement("dd"); detail.textContent = value;
        item.append(term, detail); // dt before dd; CSS shows the value first

        stats.append(item);
      };
      stat(formatNumber(metric.numerator), `Long-haul departures (${threshold == null ? "at or above the threshold" : `≥ ${threshold} mi`})`);
      stat(formatNumber(metric.denominator), "Eligible departures (all distances)");
      stat(threshold == null ? "Not supplied" : `≥ ${threshold} mi`, "Long-haul threshold (route distance)");
      figure.append(track, stats);
      block.append(figure);
      // ANC-style hubs carry heavy freighter traffic; the measure does not, and says so here.
      const scopeNote = document.createElement("p");
      scopeNote.className = "scope-note";
      const scopeLabel = document.createElement("strong");
      scopeLabel.textContent = "Scope: ";
      const scopeText = document.createElement("span");
      scopeText.textContent = "scheduled passenger departures only — cargo-only and charter flights are not included.";
      scopeNote.append(scopeLabel, scopeText);
      block.append(scopeNote);
    }
    target.append(block);
  }
  return true;
}
function renderScreeningView(result, target) {
  const ranked = result.rows.filter(row => Number.isInteger(row.rank) && row.metrics.some(metric => metric.key === "screen_score" && metric.status === "ok"));
  const preview = document.createElement("div");
  heading(target, "h3", `Top 5 of ${ranked.length} ranked airports · screening score out of 100`);
  paragraph(target, "Score = passenger growth (up to 40 points) + passenger volume (up to 30) + seat occupancy (up to 30), each scored against the other New England airports. A shortlist for closer review, not an investment verdict.").className = "ranking-explainer";
  const columns = document.createElement("div");
  columns.className = "ranking-columns";
  columns.setAttribute("aria-hidden", "true");
  for (const text of ["Rank", "Airport", "", "Score /100"]) { const node = document.createElement("span"); node.textContent = text; columns.append(node); }
  target.append(columns);
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
      scoreBar.setAttribute("role", "img"); scoreBar.setAttribute("aria-label", `${row.airport}, rank ${row.rank}, screening score ${formatMetric(score)}`);
      const fill = document.createElement("span"); fill.className = "score-fill";
      fill.setAttribute("style", `width:${Math.max(0, Math.min(100, score.value))}%`); scoreBar.append(fill); item.append(scoreBar);
      paragraph(item, compact ? fixed(score.value) : `Screening score · ${formatMetric(score)}`).className = "ranking-detail score-value";
    } else if (score?.status === "ok") paragraph(item, compact ? fixed(score.value) : `Screening score · ${formatMetric(score)}`).className = "ranking-detail score-value";
    // Where the score comes from: growth (of 40), volume (of 30) and occupancy (of 30) points.
    const parts = ["growth_points", "volume_points", "occupancy_points"].map((key) => row.metrics.find((value) => value.key === key));
    if (score?.status === "ok" && parts.every((part) => part?.status === "ok")) {
      const [growth, volume, occupancy] = parts.map((part) => part.value.toFixed(1));
      const breakdown = paragraph(item, `Growth ${growth}/40 · Volume ${volume}/30 · Occupancy ${occupancy}/30`);
      breakdown.className = "ranking-detail score-parts";
      breakdown.setAttribute("aria-label", `${growth} of 40 growth points, ${volume} of 30 volume points, ${occupancy} of 30 occupancy points`);
    }
    // Supporting values and source IDs for every row are listed once in the
    // methodology and technical disclosures.
    list.append(item);
  };
  const top = ranked.slice(0, 5);
  for (const row of top) appendRow(preview, row, true, true);
  target.append(preview);
  // The disclosure continues the list below the top five; it never repeats them.
  const rest = result.rows.filter((row) => !top.includes(row));
  if (!rest.length) return true;
  const restRanks = rest.map((row) => row.rank).filter(Number.isInteger);
  const range = restRanks.length ? `Ranks ${Math.min(...restRanks)}–${Math.max(...restRanks)}` : "More airports";
  const full = document.createElement("details");
  full.className = "full-ranking-disclosure";
  heading(full, "summary", `${range} (${rest.length} more airport${rest.length === 1 ? "" : "s"})`);
  const list = document.createElement("div"); list.className = "ranking-list full-ranking"; list.setAttribute("role", "list");
  list.setAttribute("aria-label", "Remaining returned airports in backend order");
  for (const row of rest) appendRow(list, row, true, true);
  full.append(list); target.append(full);
  return true;
}
const congestionMeaning = {
  cancellation_rate: "Share of scheduled departures that were cancelled",
  diversion_rate: "Share of scheduled departures diverted to another airport",
  departure_delay_minutes: "Average minutes of departure delay per departed flight",
  taxi_out_minutes: "Average minutes from gate to take-off per departed flight",
};
function renderCongestionView(result, target) {
  if (result.scope.airports.length !== 2) return false;
  const airports = result.scope.airports;
  const keys = ["cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes"];
  const titles = keys.map(humanMetricLabel);
  const metricFor = (airport, key) => result.rows.find(item => item.airport === airport)?.metrics.find(item => item.key === key);
  heading(target, "h3", `Four operational indicators, ${result.scope.year}`);
  // The denominator: how many scheduled departures each airport's rates describe.
  const bases = airports.map(airport => metricFor(airport, "cancellation_rate")).map(metric => metric?.status === "ok" ? metric.denominator : null);
  if (bases.every(Number.isFinite)) {
    paragraph(target, `Based on ${airports.map((airport, index) => `${formatNumber(bases[index])} ${airport}`).join(" and ")} scheduled domestic departures by reporting airlines.`).className = "congestion-basis";
  }
  const legend = document.createElement("p");
  legend.className = "congestion-legend";
  airports.forEach((airport, airportIndex) => {
    const entry = document.createElement("span");
    entry.className = "legend-entry";
    const key = document.createElement("span");
    key.className = `series-key series-key-${airportIndex + 1}`;
    key.setAttribute("aria-hidden", "true");
    const name = document.createElement("span");
    name.textContent = airport;
    entry.append(key, name);
    legend.append(entry);
  });
  const legendNote = document.createElement("span");
  legendNote.className = "legend-note";
  legendNote.textContent = "Bars start at zero, so a small difference looks small; for all four, higher means more disruption.";
  legend.append(legendNote);
  target.append(legend);
  const bars = document.createElement("div");
  bars.className = "congestion-bars";
  bars.setAttribute("role", "list");
  const table = makeTable("Exact operational values by airport", ["Measure", ...airports]);
  table.region.className += " congestion-table-region";
  table.region.setAttribute("aria-label", "Operational comparison; measures by airport");
  table.region.children[0].className = "congestion-table";
  keys.forEach((key, index) => appendMeasureBars(bars, table, result, key, titles[index], congestionMeaning[key]));
  target.append(bars);
  methodologyExtras.push(["Exact operational values", [table.region]]);
  return true;
}
// One measure as zero-based bars for each airport, plus its exact-values table row.
function appendMeasureBars(bars, table, result, key, title, meaning, titleTag = "h3") {
  const airports = result.scope.airports;
  const ns = "http://www.w3.org/2000/svg";
  const metricFor = (airport) => result.rows.find(item => item.airport === airport)?.metrics.find(item => item.key === key);
  const metrics = airports.map(airport => metricFor(airport));
  const valid = metrics.filter(metric => metric?.status === "ok" && Number.isFinite(metric.value));
  const minimum = Math.min(0, ...valid.map(metric => metric.value));
  const maximum = Math.max(0, ...valid.map(metric => metric.value));
  const span = maximum - minimum || 1;
  const percent = value => (value - minimum) / span * 100;
  const unit = valid[0]?.unit || "count";
  const measure = document.createElement("div"); measure.className = "congestion-measure"; measure.setAttribute("role", "listitem");
  const head = document.createElement("div"); head.className = "measure-head";
  heading(head, titleTag, metricLabel(key, result.scope)).className = "congestion-measure-title";
  const pair = metrics.map((metric, airportIndex) => ({ airport: airports[airportIndex], metric }));
  if (pair.every(({ metric }) => metric?.status === "ok" && Number.isFinite(metric.value))) {
    paragraph(head, comparisonDelta(pair, unit, key)).className = "measure-delta";
  }
  measure.append(head);
  paragraph(measure, meaning).className = "measure-context";
  const group = document.createElement("div");
  group.className = "comparison-bars";
  group.setAttribute("role", "img");
  const plotted = [];
  const row = document.createElement("tr");
  cell(row, title);
  pair.forEach(({ airport, metric }, airportIndex) => {
    let text = "Not returned", spoken = text;
    if (metric?.status === "unavailable") { text = "Unavailable"; spoken = `Unavailable: ${metric.reason}`; }
    else if (metric?.status === "ok") {
      text = formatMetric(metric); spoken = text;
      const cue = { higher: "↑", lower: "↓", tied: "↔" }[metric.comparison_direction];
      if (cue) { text += ` ${cue}`; spoken += `, ${label(metric.comparison_direction)}`; }
    }
    cell(row, text);
    row.children[row.children.length - 1].setAttribute("aria-label", spoken);
    const barRow = document.createElement("div"); barRow.className = `bar-row bar-row-${airportIndex + 1}`;
    const airportNode = document.createElement("span"); airportNode.className = "bar-airport"; airportNode.textContent = airport;
    barRow.append(airportNode);
    if (metric?.status === "ok" && Number.isFinite(metric.value)) {
      plotted.push({ airport, metric });
      // No viewBox: x and width are percentages of the track, heights are pixels.
      const track = document.createElementNS(ns, "svg");
      track.setAttribute("class", "comparison-bar-track");
      track.setAttribute("aria-hidden", "true");
      const rect = (className, x, w) => {
        const node = document.createElementNS(ns, "rect");
        node.setAttribute("x", `${x}%`); node.setAttribute("width", `${w}%`);
        node.setAttribute("y", "3"); node.setAttribute("height", "12"); node.setAttribute("class", className);
        track.append(node);
      };
      rect("bar-track", 0, 100);
      const start = percent(Math.min(0, metric.value));
      rect(`bar-fill bar-fill-${airportIndex + 1}`, start, percent(Math.max(0, metric.value)) - start);
      const zero = document.createElementNS(ns, "line");
      zero.setAttribute("x1", `${percent(0)}%`); zero.setAttribute("x2", `${percent(0)}%`);
      zero.setAttribute("y1", "0"); zero.setAttribute("y2", "18"); zero.setAttribute("class", "bar-zero");
      track.append(zero);
      barRow.append(track);
    } else {
      const note = document.createElement("span"); note.className = "not-plotted"; note.textContent = "Not plotted"; barRow.append(note);
    }
    const exact = document.createElement("strong"); exact.className = "comparison-exact";
    exact.textContent = metric?.status === "ok" ? formatMetric(metric) : metric?.status === "unavailable" ? `Unavailable: ${metric.reason}` : "Not returned";
    barRow.append(exact);
    group.append(barRow);
  });
  group.setAttribute("aria-label", `${metricLabel(key, result.scope)}, bars on a zero-inclusive scale from ${formatMetric({ unit, value: minimum })} to ${formatMetric({ unit, value: maximum })}: ${pair.map(({ airport, metric }) => `${airport} ${metric?.status === "ok" ? formatMetric(metric) : metric?.status === "unavailable" ? `unavailable (${metric.reason})` : "not returned"}`).join(", ")}`);
  measure.append(group);
  bars.append(measure);
  table.body.append(row);
}
// "SNA +1.37 min (10% higher)": the absolute difference in the measure's own unit,
// with the relative size so a small gap reads as small. A growth rate gets no
// relative size: "50% higher" than a growth rate misreads as a growth figure.
function comparisonDelta(pair, unit, key = null) {
  const [first, second] = pair;
  const difference = second.metric.value - first.metric.value;
  const amount = Math.abs(difference);
  if (Number(amount.toFixed(2)) === 0) return "No measurable difference";
  const [lower, higher] = difference > 0 ? [first, second] : [second, first];
  const shown = unit === "minutes" ? `${fixed(amount)} min` : unit === "percent" ? `${fixed(amount)} percentage points` : formatMetric({ unit, value: amount });
  const relative = lower.metric.value > 0 && !growthKeys.has(key) ? Math.round(amount / lower.metric.value * 100) : null;
  return `${higher.airport} +${shown}${relative ? ` (${relative}% higher)` : ""}`;
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
  // The headline figures are the KPI cards above. A ranking of more airports than
  // the cards can hold gets a compact Rank / Airport / Value list. Every exact
  // value, denominator and count moves to Methodology; source IDs stay in
  // Technical details only.
  const ranked = result.rows.some((row) => Number.isInteger(row.rank));
  if (ranked || result.rows.length > 4) {
    const valueColumn = valueColumnTitle(result.scope);
    const list = makeTable(`${humanScopeMetricLabel(result.scope.metric)} by airport`, [ranked ? "Rank" : "Airport", ranked ? "Airport" : valueColumn, ...(ranked ? [valueColumn] : [])]);
    list.region.classList.add("compact-values");
    for (const row of result.rows) {
      const metric = row.metrics.find((item) => item.key === result.scope.metric) || row.metrics[0];
      if (!metric) continue;
      const value = metric.status === "unavailable" ? "Unavailable" : figureText(metricFigure(metric));
      const tr = document.createElement("tr");
      for (const text of ranked ? [row.rank == null ? "—" : String(row.rank), row.airport, value] : [row.airport, value]) cell(tr, text);
      list.body.append(tr);
    }
    target.append(list.region);
  }
  const table = makeTable("Exact values by airport", ["Airport", "Measure", "Value", "Numerator / denominator", "Eligible observations", "Higher or lower"]);
  for (const row of result.rows) for (const metric of row.metrics) {
    const tr = document.createElement("tr");
    if (metric.key === result.scope.metric) tr.className = "selected-metric-row";
    const value = metric.status === "unavailable" ? `Unavailable: ${metric.reason}` : formatMetric(metric);
    for (const text of [row.airport, humanMetricLabel(metric.key), value,
      metric.denominator == null ? "—" : `${formatNumber(metric.numerator)} / ${formatNumber(metric.denominator)}`,
      metric.eligible_count == null ? "—" : formatNumber(metric.eligible_count),
      metric.comparison_direction ? humanDirection(metric.comparison_direction) : "—"]) cell(tr, text);
    table.body.append(tr);
  }
  methodologyExtras.push(["Exact values", [table.region]]);
}

function validateResult(result) {
  const fail = () => { throw new Error("Invalid analysis response"); };
  const scopeMetrics = new Set(["passengers", "seats", "departures", "passenger_growth", "seat_occupancy", "long_haul_share", "screen_score", "congestion", "cancellation_rate", "diversion_rate", "departure_delay_minutes", "taxi_out_minutes", "sfo_enplaned_trend", "sfo_pressure", "overview"]);
  const metricUnits = { passengers: "count", seats: "count", departures: "count", passenger_growth: "percent", seat_occupancy: "percent", long_haul_share: "percent", screen_score: "score", cancellation_rate: "percent", diversion_rate: "percent", departure_delay_minutes: "minutes", taxi_out_minutes: "minutes", sfo_enplaned_trend: "count", enplaned_growth: "percent", sfo_pressure: "percentage_points", seat_growth: "percent", growth_points: "score", volume_points: "score", occupancy_points: "score" };
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
  const names = { screen_score: "Screening score", passenger_growth: "Passenger growth", passengers: "Passengers", seats: "Seats", departures: "Departures", seat_occupancy: "Seat occupancy", long_haul_share: "Long-haul share", cancellation_rate: "Cancellation rate", diversion_rate: "Diversion rate", departure_delay_minutes: "Departure delay", taxi_out_minutes: "Taxi-out time", sfo_enplaned_trend: "SFO passenger trend", enplaned_growth: "Enplaned passenger growth", sfo_pressure: "Passenger growth gap", seat_growth: "Seat supply growth", growth_points: "Growth points", volume_points: "Volume points", occupancy_points: "Occupancy points", overview: "Overall comparison" };
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
// Weighted screen-score components and the most points each can contribute.
const screenPointMax = { growth_points: 40, volume_points: 30, occupancy_points: 30 };
function formatMetric(metric) {
  if (screenPointMax[metric.key]) return `${metric.value.toFixed(1)} of ${screenPointMax[metric.key]} points`;
  const value = metric.unit === "count" ? formatNumber(metric.value) : metric.value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (metric.unit === "percentage_points") return `${value} percentage points`;
  return `${value}${({ percent: "%", percentage_points: " percentage points", minutes: " min", score: " / 100", count: "" })[metric.unit]}`;
}
// ── What each displayed number means ──────────────────────────────────────────
// Every figure carries WHAT is measured, its UNIT and its comparison, denominator
// or timeframe. The words come from the returned value and its resolved scope;
// nothing is estimated in the browser.
const MINUS = "−";
const growthKeys = new Set(["passenger_growth", "enplaned_growth", "seat_growth"]);
function previousYear(scope) {
  return scope.baseline_year != null && scope.year === scope.comparison_year ? scope.baseline_year : scope.year - 1;
}
function fixed(value, digits = 2) { return Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
function signedNumber(value, digits = 2) {
  const rounded = Number(value.toFixed(digits));
  return rounded > 0 ? `+${fixed(rounded, digits)}` : rounded < 0 ? `${MINUS}${fixed(rounded, digits)}` : fixed(0, digits);
}
function signedCount(value) { return value > 0 ? `+${formatNumber(value)}` : value < 0 ? `${MINUS}${formatNumber(Math.abs(value))}` : "0"; }
function monthLabel(period) {
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${months[Number(period.slice(4)) - 1]} ${period.slice(0, 4)}`;
}
function metricLabel(key, scope) {
  const pressure = scope.metric === "sfo_pressure";
  const names = {
    passengers: scope.metric === "sfo_enplaned_trend" ? "Passengers boarding at SFO" : "Passengers",
    sfo_enplaned_trend: "Passengers boarding at SFO",
    passenger_growth: pressure ? "Passenger traffic growth" : "Passenger growth",
    enplaned_growth: "Boarding passenger growth (DataSF)",
    seat_growth: "Airline seat supply growth",
    sfo_pressure: "Traffic vs. seat growth",
    seats: "Seats supplied", departures: "Departures flown",
    departure_delay_minutes: "Average departure delay", taxi_out_minutes: "Average taxi-out time",
  };
  return names[key] || humanMetricLabel(key);
}
// The figure split into number and unit, so the unit reads as a unit.
function metricFigure(metric) {
  const value = metric.value;
  if (screenPointMax[metric.key]) return { number: value.toFixed(1), unit: `of ${screenPointMax[metric.key]} points` };
  if (growthKeys.has(metric.key)) return { number: signedNumber(value), unit: "%" };
  const plain = `${value < 0 && Number(value.toFixed(2)) !== 0 ? MINUS : ""}${fixed(value)}`;
  return ({
    count: { number: formatNumber(value), unit: "" },
    percent: { number: plain, unit: "%" },
    percentage_points: { number: signedNumber(value), unit: "percentage points" },
    minutes: { number: plain, unit: "min" },
    score: { number: plain, unit: "/ 100" },
  })[metric.unit];
}
function figureText(figure) { return !figure.unit || figure.unit === "%" ? `${figure.number}${figure.unit}` : `${figure.number} ${figure.unit}`; }
function appendFigure(parent, figure) {
  const number = document.createElement("span");
  number.className = "kpi-number";
  number.textContent = figure.number;
  parent.append(number);
  if (!figure.unit) return;
  const unit = document.createElement("span");
  // "%" hugs the number; a worded unit ("percentage points") gets its own short line.
  unit.className = figure.unit === "%" ? "kpi-unit is-tight" : figure.unit.length > 6 ? "kpi-unit is-words" : "kpi-unit";
  unit.textContent = figure.unit;
  parent.append(unit);
}
// The grey line under a figure: comparison, denominator or timeframe.
function metricContext(metric, scope) {
  const year = scope.year, previous = previousYear(scope);
  const counted = metric.numerator != null && metric.denominator != null;
  switch (metric.key) {
    case "passengers": case "sfo_enplaned_trend":
      return scope.metric === "sfo_enplaned_trend" || metric.key === "sfo_enplaned_trend" ? `in ${year} · DataSF enplanements` : `on departing flights in ${year}`;
    case "seats": return `on departing flights in ${year}`;
    case "departures": return `departing flights performed in ${year}`;
    case "passenger_growth": case "enplaned_growth":
      return scope.metric === "sfo_pressure" || !counted ? `vs. ${previous}` : `vs. ${previous} · ${signedCount(metric.numerator)} passengers`;
    case "seat_growth": return `vs. ${previous}`;
    case "sfo_pressure": {
      const rounded = Number(metric.value.toFixed(2));
      if (rounded < 0) return `Seat supply grew ${fixed(rounded)} percentage points faster (calculated before rounding)`;
      if (rounded > 0) return `Passenger traffic grew ${fixed(rounded)} percentage points faster (calculated before rounding)`;
      return "Passenger traffic and seat supply grew at the same pace";
    }
    case "seat_occupancy": return `of supplied seats filled in ${year}`;
    case "long_haul_share": return `of eligible departures were ≥${formatNumber(scope.threshold_miles ?? 3000)} miles`;
    case "screen_score": return "relative score: growth /40 + volume /30 + occupancy /30";
    case "cancellation_rate": case "diversion_rate": {
      const outcome = metric.key === "cancellation_rate" ? "cancelled" : "diverted";
      return counted ? `${formatNumber(metric.numerator)} of ${formatNumber(metric.denominator)} scheduled departures ${outcome} in ${year}` : `of scheduled departures ${outcome} in ${year}`;
    }
    case "departure_delay_minutes": return `average per departed flight in ${year}`;
    case "taxi_out_minutes": return `gate to take-off, average per departed flight in ${year}`;
    default: return "";
  }
}
// A per-airport detail line when one card lists several airports.
function metricDetail(metric, scope) {
  if (metric.numerator == null || metric.denominator == null) return "";
  if (growthKeys.has(metric.key)) return `${signedCount(metric.numerator)} passengers vs. ${previousYear(scope)}`;
  return `${formatNumber(metric.numerator)} of ${formatNumber(metric.denominator)}`;
}
function valueColumnTitle(scope) {
  const name = metricLabel(scope.metric, scope);
  return growthKeys.has(scope.metric) ? `${name}, ${scope.year} vs. ${previousYear(scope)}` : `${name} in ${scope.year}`;
}
function resultHeadline(result) {
  const { metric, airports } = result.scope;
  if (metric === "screen_score") return "New England screening";
  const ranked = result.rows.some((row) => Number.isInteger(row.rank));
  if (ranked && airports.length > 2 && airports.every((code) => newEnglandAirports.has(code))) return `New England ${metricLabel(metric, result.scope).toLowerCase()}`;
  if (airports.length === 2) return `${airports[0]} vs ${airports[1]}`;
  return airports.length > 2 ? "Airports in scope" : airports[0];
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
  const presetKey = Object.keys(demos).find((key) => demos[key] === analysis);
  return submitRequest({ analysis }, { presetLabel: presetLabels[presetKey] || null });
}
// Back: the analysis choices slide in rather than replacing the result in one frame.
const VIEW_IN_MS = 240;
let viewInTimer = null;
function slideInAnalysisChoices() {
  const workspace = $("#analysis-workspace");
  clearTimeout(viewInTimer);
  workspace.classList.remove("is-returning");
  if (prefersReducedMotion()) return;
  void workspace.offsetWidth; // restart the animation on a repeated Back
  workspace.classList.add("is-returning");
  viewInTimer = setTimeout(() => workspace.classList.remove("is-returning"), VIEW_IN_MS + 40);
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
  cancelResultFade();
  setLoading(false);
  latestSuccessfulResult = null;
  contextResultId = null;
  pendingComparison = null;
  retireChatChoices();
  composerOwner = null;
  explanation = null;
  resultIsPrevious = false;
  $("#hero-layout").classList.remove("has-result");
  slideInAnalysisChoices();
  $("#result-panel").hidden = true;
  $("#result").hidden = true;
  $("#result-title").hidden = true;
  $("#back-to-analysis").hidden = true;
  $("#fresh").hidden = true;
  $("#view-evidence").hidden = true;
  $("#explain").hidden = true;
  $("#result-skip").hidden = true;
  $("#setup-controls").open = true;
  $("#setup-toggle").textContent = "Choose an analysis";
  positionFeedback($("#analyze"));
  $("#question").value = "";
  clearQuestionError();
  $("#context-summary").textContent = COVERAGE_SUMMARY;
  $("#explain").disabled = true;
  refreshChatViewLinks();
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
  if (input.id === "question") { clearQuestionError(); expandChat(false); fitComposer(); return; }
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
  const request = followUpRequest($("#question").value.trim());
  clearQuestionError();
  if (!request.message) {
    showQuestionError("Enter a question before sending.");
    $("#question").focus();
    return;
  }
  // The question is going out (not refused as empty or busy): drop any dictation the
  // browser has not delivered yet, so it cannot refill the field afterwards.
  if (!busy) window.airportVoice?.discardListening?.();
  // Sending the text of the latest failed turn again is that turn's Retry, not a
  // second copy of the question in the transcript.
  const failed = [...conversation].reverse().find((message) => message.role === "user" && message.state === "failed");
  if (failed?.turn && failed.text === request.message) {
    retryChatTurn(failed.turn);
    return;
  }
  void submitRequest(request);
});
// Mobile: keep the card above the on-screen keyboard where the browser overlays it.
if (window.visualViewport && document.documentElement?.style) {
  const viewport = window.visualViewport;
  const syncKeyboardInset = () => document.documentElement.style.setProperty("--keyboard-inset", `${Math.max(0, Math.round(window.innerHeight - viewport.height - viewport.offsetTop))}px`);
  viewport.addEventListener("resize", syncKeyboardInset);
  viewport.addEventListener("scroll", syncKeyboardInset);
}
// Enter sends; Shift+Enter keeps a newline. IME composition (isComposing / 229)
// is left alone so confirming a candidate never sends a half-typed question.
$("#question").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (busy) return;
  const form = $("#chat-form");
  if (typeof form.requestSubmit === "function") form.requestSubmit();
  else form.dispatchEvent(new Event("submit", { cancelable: true }));
});
askTrigger.addEventListener("click", openQuestionComposer);
// Clicking anywhere on the collapsed bar raises the card (focus alone does not).
$("#chat-form").addEventListener("click", () => expandChat(false));
$("#chat-collapse").addEventListener("click", () => collapseChat());
$("#chat-resize").addEventListener("click", toggleChatEnlarged);
$("#back-to-analysis").addEventListener("click", startNewAnalysis);
// Escape first lowers an expanded card to its bar, then closes the bar.
document.addEventListener?.("keydown", (event) => {
  if (event.key !== "Escape" || $("#conversation-composer").hidden) return;
  if (chatExpanded()) collapseChat();
  else closeQuestionComposer();
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
