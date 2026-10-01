// Voice input and output for the chat, using only the browser's own speech APIs.
//
// Input: SpeechRecognition turns speech into ordinary text in the question field.
// Nothing is sent automatically; the analyst reviews or edits the text and sends
// it exactly like a typed question, so the server sees the same text path and
// makes the same single model call. Output: speechSynthesis reads a finished
// reply aloud on request; the written reply stays authoritative. The app never
// records, stores or uploads audio. In some browsers (e.g. Chrome) the browser
// vendor's speech service performs the recognition. Both controls stay hidden
// where the browser lacks the API.
"use strict";

function installVoice(win, doc) {
  const Recognition = win.SpeechRecognition || win.webkitSpeechRecognition;
  const synth = win.speechSynthesis;
  const Utterance = win.SpeechSynthesisUtterance;
  const question = doc.querySelector("#question");
  const form = doc.querySelector("#chat-form");
  const mic = doc.querySelector("#voice-input");
  const transcript = doc.querySelector("#chat-transcript");
  const status = doc.querySelector("#chat-status");
  const api = { listening: false, speaking: null };
  if (!question || !form || !transcript) return api;

  const announce = (text) => { if (status) status.textContent = text; };
  const call = (name, ...args) => { if (typeof win[name] === "function") win[name](...args); };

  // ── Speech output ───────────────────────────────────────────────────────────
  const canSpeak = Boolean(synth && typeof synth.speak === "function" && typeof Utterance === "function");
  let current = null; // { utterance, button }

  function resetSpeakButton(button) {
    button.removeAttribute("data-speaking");
    button.textContent = "Read aloud";
  }
  function stopSpeaking() {
    if (!current) return;
    const { button } = current;
    current = null;
    api.speaking = null;
    synth.cancel();
    resetSpeakButton(button);
  }
  function speak(item, button) {
    const wasThis = current && current.button === button;
    stopSpeaking();
    if (wasThis) return;
    const text = item.querySelector(".chat-text")?.textContent?.trim();
    if (!text) return;
    const utterance = new Utterance(text);
    utterance.lang = "en-US";
    const finish = () => {
      // A cancelled utterance can report its end after a newer one started.
      if (current?.utterance !== utterance) return;
      current = null;
      api.speaking = null;
      resetSpeakButton(button);
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    current = { utterance, button };
    api.speaking = item;
    button.setAttribute("data-speaking", "true");
    button.textContent = "Stop reading";
    synth.speak(utterance);
  }
  // Finished replies ("done" answers and "note" replies) get a Read aloud button.
  function syncSpeakButtons() {
    for (const item of transcript.children) {
      if (!item.classList?.contains("chat-message-assistant")) continue;
      const ready = ["done", "note"].includes(item.getAttribute("data-state"));
      let button = item.querySelector?.(".chat-speak") || null;
      if (!ready) {
        if (button) {
          if (current?.button === button) stopSpeaking();
          button.remove();
        }
        continue;
      }
      if (button) continue;
      button = doc.createElement("button");
      button.type = "button";
      button.className = "chat-speak";
      resetSpeakButton(button);
      button.addEventListener("click", () => speak(item, button));
      const link = item.querySelector?.(".chat-view-link");
      if (link) link.before(button); else item.append(button);
    }
  }
  if (canSpeak && typeof win.MutationObserver === "function") {
    let known = transcript.children.length;
    new win.MutationObserver(() => {
      // A new reply arriving stops any reply still being read.
      if (transcript.children.length !== known) { known = transcript.children.length; stopSpeaking(); }
      syncSpeakButtons();
    }).observe(transcript, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-state"] });
    syncSpeakButtons();
    win.addEventListener?.("pagehide", stopSpeaking);
  }

  // ── Speech input ────────────────────────────────────────────────────────────
  if (!Recognition || !mic) return Object.assign(api, { stopSpeaking, syncSpeakButtons });
  mic.hidden = false;
  // The live session. Every handler checks it, so events from a recognizer that was
  // discarded or replaced (late results, a late end) never touch the field or the button.
  let recognition = null;
  let before = "";
  let heard = "";
  let failed = false;

  let restingPlaceholder = null;
  function setListening(value) {
    if (value === api.listening) return;
    api.listening = value;
    mic.setAttribute("aria-pressed", String(value));
    mic.classList.toggle("is-listening", value);
    // A visible, non-colour cue in the field itself while the microphone is on.
    if (value) {
      restingPlaceholder = question.getAttribute("placeholder") ?? "";
      question.setAttribute("placeholder", "Listening…");
    } else if (restingPlaceholder !== null) {
      question.setAttribute("placeholder", restingPlaceholder);
      restingPlaceholder = null;
    }
  }
  const ERRORS = {
    "not-allowed": "Microphone access is blocked. Allow it in your browser settings, or type your question.",
    "service-not-allowed": "Voice input is not available in this browser. Type your question instead.",
    "audio-capture": "No microphone was found. Type your question instead.",
    "no-speech": "No speech was heard. Try again, or type your question.",
    "network": "Voice input needs a network connection. Type your question instead.",
    "language-not-supported": "Voice input does not support this language here. Type your question instead.",
  };
  function stopListening() { if (recognition && api.listening) recognition.stop(); }
  // Called by app.js once a question is really sent. The session is dropped at once:
  // stop() would still deliver a final result, rewriting the field after the question
  // has gone, and the microphone goes idle without waiting for the browser.
  function discardListening() {
    before = "";
    heard = "";
    const session = recognition;
    if (!session) return;
    recognition = null;
    setListening(false);
    if (typeof session.abort === "function") session.abort(); else session.stop();
  }
  function startListening() {
    stopSpeaking(); // never let the analyst's speakers feed the microphone
    call("expandChat", false);
    call("clearQuestionError");
    before = question.value.trim();
    heard = "";
    failed = false;
    const session = new Recognition();
    recognition = session;
    const live = () => recognition === session;
    session.lang = "en-US";
    session.interimResults = true;
    session.continuous = false;
    session.maxAlternatives = 1;
    session.onstart = () => { if (!live()) return; setListening(true); announce("Listening. Speak your question, then review it before sending."); };
    session.onresult = (event) => {
      if (!live()) return;
      heard = Array.from(event.results, (result) => result[0]?.transcript || "").join("").trim();
      question.value = [before, heard].filter(Boolean).join(" ");
      call("fitComposer");
    };
    session.onerror = (event) => {
      if (!live() || event.error === "aborted") return;
      failed = true;
      call("showQuestionError", ERRORS[event.error] || "Voice input stopped unexpectedly. Type your question instead.");
    };
    session.onend = () => {
      if (!live()) return;
      setListening(false);
      recognition = null;
      if (!failed) announce(heard ? "Voice input finished. Review or edit the question, then send it." : "No speech was recognized.");
      question.focus();
      const end = question.value.length;
      question.setSelectionRange?.(end, end);
    };
    try {
      session.start();
      // Show the listening state at once: the browser may first ask for microphone
      // permission, and a second click must be able to cancel that wait.
      setListening(true);
      announce("Starting voice input. Allow the microphone if your browser asks.");
    } catch {
      recognition = null;
      setListening(false);
      call("showQuestionError", "Voice input could not start. Type your question instead.");
    }
  }
  mic.addEventListener("click", (event) => {
    event.stopPropagation(); // the bar's own click handler must not steal focus
    if (api.listening) stopListening(); else startListening();
  });
  // Submitting or Escape ends listening and keeps the text heard so far. When app.js
  // actually sends the question it calls discardListening() to drop later results.
  form.addEventListener("submit", stopListening, true);
  doc.addEventListener?.("keydown", (event) => { if (event.key === "Escape") stopListening(); });
  return Object.assign(api, { stopSpeaking, syncSpeakButtons, startListening, stopListening, discardListening });
}

if (typeof window !== "undefined" && typeof document !== "undefined" && !window.__voiceTestHarness) {
  window.airportVoice = installVoice(window, document);
}
