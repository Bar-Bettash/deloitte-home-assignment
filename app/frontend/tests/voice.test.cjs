// Contract tests for voice.js with a minimal fake DOM and fake browser speech APIs.
// They prove the control flow (no auto-send, error copy, start/stop, read-aloud
// state); they are not a substitute for trying a real microphone in a browser.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Node {
  constructor(tag = 'div', id = '') {
    this.tagName = tag; this.id = id; this.children = []; this.parent = null; this.attrs = {}; this.listeners = {};
    this.textContent = ''; this.value = ''; this.hidden = false; this.focused = false; this.type = '';
    const classes = new Set();
    this.classList = { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, on) => { if (on === undefined) on = !classes.has(c); if (on) classes.add(c); else classes.delete(c); return on; } };
  }
  set className(value) { for (const c of String(value).split(/\s+/).filter(Boolean)) this.classList.add(c); this._className = value; }
  get className() { return this._className || ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) { for (const fn of this.listeners[type] || []) fn({ stopPropagation() {}, preventDefault() {}, ...event }); }
  append(...nodes) { for (const n of nodes) { n.remove(); n.parent = this; this.children.push(n); } }
  before(node) { node.remove(); const siblings = this.parent.children; siblings.splice(siblings.indexOf(this), 0, node); node.parent = this.parent; }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
  focus() { this.focused = true; }
  setSelectionRange(a, b) { this.selection = [a, b]; }
  querySelector(selector) {
    const cls = selector.startsWith('.') ? selector.slice(1) : null;
    for (const child of this.children) {
      if (cls && child.classList.contains(cls)) return child;
      const deep = child.querySelector(selector); if (deep) return deep;
    }
    return null;
  }
}

function assistant(text, state = 'done') {
  const item = new Node('li'); item.className = 'chat-message chat-message-assistant'; item.setAttribute('data-state', state);
  const p = new Node('p'); p.className = 'chat-text'; p.textContent = text;
  const link = new Node('button'); link.className = 'chat-view-link';
  item.append(p, link);
  return item;
}

function setup({ recognition = true, speech = true, startThrows = false } = {}) {
  const ids = Object.fromEntries(['question', 'chat-form', 'voice-input', 'chat-transcript', 'chat-status'].map((id) => [id, new Node('x', id)]));
  ids['voice-input'].hidden = true;
  const docListeners = {};
  const document = {
    querySelector: (sel) => ids[sel.slice(1)] || null,
    createElement: (tag) => new Node(tag),
    addEventListener: (type, fn) => { (docListeners[type] ||= []).push(fn); },
  };
  const recognizers = [];
  class FakeRecognition {
    constructor() { this.started = 0; this.stopped = 0; this.aborted = 0; recognizers.push(this); }
    start() { if (startThrows) throw new Error('InvalidStateError'); this.started += 1; }
    stop() { this.stopped += 1; }
    abort() { this.aborted += 1; }
  }
  const spoken = [];
  const synth = { cancelled: 0, speak(u) { spoken.push(u); }, cancel() { this.cancelled += 1; } };
  class FakeUtterance { constructor(text) { this.text = text; } }
  let observerCallback = null;
  class FakeObserver { constructor(cb) { observerCallback = cb; } observe() {} }
  const calls = [];
  const window = {
    __voiceTestHarness: true,
    MutationObserver: FakeObserver,
    addEventListener() {},
    expandChat: (...a) => calls.push(['expandChat', ...a]),
    fitComposer: () => calls.push(['fitComposer']),
    clearQuestionError: () => calls.push(['clearQuestionError']),
    showQuestionError: (text) => calls.push(['showQuestionError', text]),
  };
  if (recognition) window.webkitSpeechRecognition = FakeRecognition;
  if (speech) { window.speechSynthesis = synth; window.SpeechSynthesisUtterance = FakeUtterance; }
  const context = vm.createContext({ window, document, Array });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../voice.js'), 'utf8'), context);
  const api = vm.runInContext('installVoice(window, document)', context);
  const flush = () => observerCallback?.([]);
  const submits = [];
  ids['chat-form'].addEventListener('submit', () => submits.push(ids.question.value));
  return { ids, api, recognizers, spoken, synth, calls, flush, submits, docListeners, mic: ids['voice-input'], question: ids.question };
}

const errorsShown = (ui) => ui.calls.filter((c) => c[0] === 'showQuestionError').map((c) => c[1]);

test('unsupported browser: the microphone stays hidden and no read-aloud control appears', () => {
  const ui = setup({ recognition: false, speech: false });
  assert.equal(ui.mic.hidden, true);
  ui.ids['chat-transcript'].append(assistant('LAX has the higher taxi-out time.'));
  ui.flush();
  assert.equal(ui.ids['chat-transcript'].children[0].querySelector('.chat-speak'), null);
});

test('speech becomes editable text in the field and is never sent automatically', () => {
  const ui = setup();
  assert.equal(ui.mic.hidden, false);
  ui.question.value = 'At SFO,';
  ui.question.setAttribute('placeholder', 'Ask a follow-up…');
  ui.mic.dispatch('click');
  const rec = ui.recognizers[0];
  assert.equal(rec.started, 1);
  assert.equal(rec.lang, 'en-US');
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'true', 'pressed at once, even while a permission prompt is open');
  assert.equal(ui.question.getAttribute('placeholder'), 'Listening…');
  assert.match(ui.ids['chat-status'].textContent, /Allow the microphone/);
  rec.onstart();
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'true');
  assert.equal(ui.mic.getAttribute('aria-label'), null, 'the name stays fixed in the markup; only aria-pressed changes');
  assert.match(ui.ids['chat-status'].textContent, /Listening/);
  rec.onresult({ results: [[{ transcript: 'is there unmet' }], [{ transcript: ' demand?' }]] });
  assert.equal(ui.question.value, 'At SFO, is there unmet demand?');
  rec.onend();
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'false');
  assert.equal(ui.question.getAttribute('placeholder'), 'Ask a follow-up…', 'the resting placeholder comes back');
  assert.equal(ui.question.focused, true);
  assert.deepEqual(ui.question.selection, [ui.question.value.length, ui.question.value.length]);
  assert.match(ui.ids['chat-status'].textContent, /Review or edit the question, then send it/);
  assert.deepEqual(ui.submits, [], 'nothing is submitted by voice');
  ui.question.value = 'At SFO, is there unmet passenger demand?';
  assert.equal(ui.question.value, 'At SFO, is there unmet passenger demand?', 'the analyst can still edit the text');
});

test('clicking the microphone again stops listening and keeps what was heard', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  const rec = ui.recognizers[0];
  rec.onstart();
  rec.onresult({ results: [[{ transcript: 'compare LAX and SNA' }]] });
  ui.mic.dispatch('click');
  assert.equal(rec.stopped, 1);
  rec.onend();
  assert.equal(ui.question.value, 'compare LAX and SNA');
  assert.equal(ui.recognizers.length, 1, 'stopping never starts a second recognizer');
});

for (const [code, expected] of [
  ['not-allowed', /Microphone access is blocked/],
  ['service-not-allowed', /not available in this browser/],
  ['audio-capture', /No microphone was found/],
  ['no-speech', /No speech was heard/],
  ['network', /needs a network connection/],
  ['something-new', /stopped unexpectedly/],
]) {
  test(`recognition error "${code}" shows a plain message and leaves typing available`, () => {
    const ui = setup();
    ui.mic.dispatch('click');
    const rec = ui.recognizers[0];
    rec.onstart();
    rec.onerror({ error: code });
    rec.onend();
    assert.equal(errorsShown(ui).length, 1);
    assert.match(errorsShown(ui)[0], expected);
    assert.equal(ui.mic.getAttribute('aria-pressed'), 'false');
    assert.ok(!/finished/.test(ui.ids['chat-status'].textContent));
    assert.deepEqual(ui.submits, []);
  });
}

test('an aborted recognition is silent', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  ui.recognizers[0].onerror({ error: 'aborted' });
  ui.recognizers[0].onend();
  assert.deepEqual(errorsShown(ui), []);
});

test('a recognizer that refuses to start reports it and is not left listening', () => {
  const ui = setup({ startThrows: true });
  ui.mic.dispatch('click');
  assert.match(errorsShown(ui)[0], /could not start/);
  assert.equal(ui.api.listening, false);
});

test('sending the question or pressing Escape ends listening', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  ui.recognizers[0].onstart();
  ui.ids['chat-form'].dispatch('submit');
  assert.equal(ui.recognizers[0].stopped, 1);
  ui.recognizers[0].onend(); // a real recognizer reports its end after stop()
  ui.mic.dispatch('click');
  ui.recognizers[1].onstart();
  for (const fn of ui.docListeners.keydown) fn({ key: 'Escape' });
  assert.equal(ui.recognizers[1].stopped, 1);
});

test('a result that arrives after sending never rewrites the field or refocuses it', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  const rec = ui.recognizers[0];
  rec.onstart();
  rec.onresult({ results: [[{ transcript: 'what is sfo passenger growth' }]] });
  ui.ids['chat-form'].dispatch('submit');
  assert.deepEqual(ui.submits, ['what is sfo passenger growth']);
  ui.api.discardListening(); // app.js, once it has accepted and sent the question
  assert.equal(rec.aborted, 1, 'sending aborts, so no late result arrives');
  ui.question.value = ''; // the app clears the field once the question is sent
  ui.question.focused = false;
  rec.onresult({ results: [[{ transcript: 'What is SFO passenger growth?' }]] }); // late final result
  rec.onerror({ error: 'aborted' });
  rec.onend();
  assert.equal(ui.question.value, '', 'nothing left behind to send a second time');
  assert.equal(ui.question.focused, false);
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'false');
  assert.deepEqual(errorsShown(ui), []);
});

test('after a dictated question is sent, the next dictation holds only the new words', () => {
  for (const finishedFirst of [false, true]) {
    const ui = setup();
    ui.mic.dispatch('click');
    const first = ui.recognizers[0];
    first.onstart();
    first.onresult({ results: [[{ transcript: 'Why is SFO under pressure' }]] });
    if (finishedFirst) first.onend(); // the browser ended listening before Send
    ui.ids['chat-form'].dispatch('submit');
    ui.api.discardListening(); // app.js accepted and sent the question...
    ui.question.value = ''; // ...and cleared the field at once
    if (!finishedFirst) { first.onerror({ error: 'aborted' }); first.onend(); }
    assert.equal(ui.mic.getAttribute('aria-pressed'), 'false', 'the microphone is idle after the send');
    ui.mic.dispatch('click');
    const second = ui.recognizers[1];
    second.onstart();
    second.onresult({ results: [[{ transcript: 'and compare it with LAX' }]] });
    assert.equal(ui.question.value, 'and compare it with LAX', `only the new transcript (first ${finishedFirst ? 'finished' : 'still listening'} at send)`);
    first.onresult?.({ results: [[{ transcript: 'Why is SFO under pressure today' }]] }); // a stray late event from the first
    assert.equal(ui.question.value, 'and compare it with LAX', 'the sent question never comes back');
  }
});

test('late events from a discarded recognizer cannot disturb the next dictation', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  const first = ui.recognizers[0];
  first.onstart();
  first.onresult({ results: [[{ transcript: 'Why is SFO under pressure' }]] });
  ui.ids['chat-form'].dispatch('submit');
  ui.api.discardListening();
  ui.question.value = '';
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'false', 'idle at once, without waiting for the browser');
  ui.mic.dispatch('click'); // dictating the next question before the first recognizer has ended
  const second = ui.recognizers[1];
  second.onstart();
  second.onresult({ results: [[{ transcript: 'and compare it with LAX' }]] });
  ui.question.focused = false;
  const status = ui.ids['chat-status'].textContent;
  first.onstart();
  first.onresult({ results: [[{ transcript: 'Why is SFO under pressure today' }]] });
  first.onerror({ error: 'network' });
  first.onend();
  assert.equal(ui.question.value, 'and compare it with LAX');
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'true', 'the new session is still listening');
  assert.equal(ui.api.listening, true);
  assert.deepEqual(errorsShown(ui), [], 'no error from the old recognizer');
  assert.equal(ui.question.focused, false, 'the old end does not move focus');
  assert.equal(ui.ids['chat-status'].textContent, status);
  second.onend();
  assert.equal(ui.mic.getAttribute('aria-pressed'), 'false');
  assert.equal(ui.question.value, 'and compare it with LAX');
});

test('a send the app refuses (busy or empty) keeps the dictated text, final result included', () => {
  const ui = setup();
  ui.mic.dispatch('click');
  const rec = ui.recognizers[0];
  rec.onstart();
  rec.onresult({ results: [[{ transcript: 'and compare it with' }]] });
  ui.ids['chat-form'].dispatch('submit'); // app.js refuses it, so discardListening() is never called
  assert.equal(rec.aborted, 0);
  rec.onresult({ results: [[{ transcript: 'and compare it with LAX' }]] });
  rec.onend();
  assert.equal(ui.question.value, 'and compare it with LAX');
  assert.equal(ui.question.focused, true);
  assert.match(ui.ids['chat-status'].textContent, /Review or edit the question/);
});

test('finished replies get Read aloud; it reads the deterministic text and toggles to Stop reading', () => {
  const ui = setup();
  const reply = assistant('No sign in 2025 that airline seat supply at SFO fell behind passenger traffic.');
  ui.ids['chat-transcript'].append(reply);
  ui.flush();
  const button = reply.querySelector('.chat-speak');
  assert.equal(button.textContent, 'Read aloud');
  assert.equal(reply.children.indexOf(button), reply.children.indexOf(reply.querySelector('.chat-view-link')) - 1);
  button.dispatch('click');
  assert.equal(ui.spoken.length, 1);
  assert.equal(ui.spoken[0].text, 'No sign in 2025 that airline seat supply at SFO fell behind passenger traffic.');
  assert.equal(button.textContent, 'Stop reading');
  button.dispatch('click');
  assert.ok(ui.synth.cancelled >= 1);
  assert.equal(button.textContent, 'Read aloud');
  button.dispatch('click');
  assert.equal(ui.spoken.length, 2, 'replay reads the reply again');
  ui.spoken[1].onend();
  assert.equal(button.textContent, 'Read aloud');
});

test('pending replies have no Read aloud; a reply going back to pending loses it and stops reading', () => {
  const ui = setup();
  const reply = assistant('Analyzing…', 'pending');
  ui.ids['chat-transcript'].append(reply);
  ui.flush();
  assert.equal(reply.querySelector('.chat-speak'), null);
  reply.setAttribute('data-state', 'note');
  ui.flush();
  const button = reply.querySelector('.chat-speak');
  button.dispatch('click');
  reply.setAttribute('data-state', 'pending');
  ui.flush();
  assert.equal(reply.querySelector('.chat-speak'), null);
  assert.equal(ui.api.speaking, null);
});

test('a new answer stops the reply being read, and a stale end event cannot reset the newer one', () => {
  const ui = setup();
  const first = assistant('First answer.');
  ui.ids['chat-transcript'].append(first);
  ui.flush();
  first.querySelector('.chat-speak').dispatch('click');
  const cancelledBefore = ui.synth.cancelled;
  const second = assistant('Second answer.');
  ui.ids['chat-transcript'].append(second);
  ui.flush();
  assert.ok(ui.synth.cancelled > cancelledBefore);
  assert.equal(first.querySelector('.chat-speak').textContent, 'Read aloud');
  second.querySelector('.chat-speak').dispatch('click');
  ui.spoken[0].onend(); // the first, cancelled utterance reports late
  assert.equal(second.querySelector('.chat-speak').textContent, 'Stop reading');
});

test('starting voice input stops any reply being read aloud', () => {
  const ui = setup();
  const reply = assistant('An answer.');
  ui.ids['chat-transcript'].append(reply);
  ui.flush();
  reply.querySelector('.chat-speak').dispatch('click');
  ui.mic.dispatch('click');
  assert.equal(ui.api.speaking, null);
  assert.equal(reply.querySelector('.chat-speak').textContent, 'Read aloud');
});
