// Node's built-in test runner exercises the real browser script with a minimal DOM.
// These are contract/state tests, not a browser or accessibility audit.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(id = '') { this.children = []; this.attrs = {}; this.dataset = {}; this.hidden = false; this.value = ''; this.textContent = ''; this.id = id; this.listeners = {}; this.focused = false; this.parent = null; this.classList = { values: new Set(), add: value => this.classList.values.add(value), remove: value => this.classList.values.delete(value), contains: value => this.classList.values.has(value), toggle: (value, enabled) => { if (enabled === undefined) enabled = !this.classList.values.has(value); if (enabled) this.classList.values.add(value); else this.classList.values.delete(value); return enabled; } }; }
  append(...nodes) { for (const node of nodes) { if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node); this.children.push(node); node.parent = this; } }
  replaceChildren(...nodes) { for (const child of this.children) if (child.parent === this) child.parent = null; this.children = []; this.append(...nodes); }
  insertBefore(node, reference) { this.insertedBefore = { node, reference }; if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node); const index = this.children.indexOf(reference); this.children.splice(index < 0 ? this.children.length : index, 0, node); node.parent = this; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  removeAttribute(key) { delete this.attrs[key]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  after(node) { this.afterNode = node; node.parent = this; }
  before(node) { this.beforeNode = node; node.parent = this; }
  click() { const event = { currentTarget: this, target: this, preventDefault() {} }; this.listeners.click?.(event); this.onclick?.(event); }
  focus() { this.focused = true; }
}
function setup(query, overrides = {}, healthQuery = async () => ({ ok: true, json: async () => ({ status: 'ok' }) })) {
  const nodes = new Map();
  const documentListeners = new Map();
  const document = {
    activeElement: null,
    querySelector(selector) {
      if (!nodes.has(selector)) {
        const node = new Element(selector.startsWith('#') ? selector.slice(1) : '');
        if (selector === '#conversation-composer') { node.hidden = true; node.inert = true; node.setAttribute('aria-hidden', 'true'); }
        if (selector === '#ask-trigger') { node.textContent = 'Ask your own question →'; node.setAttribute('aria-expanded', 'false'); }
        nodes.set(selector, node);
      }
      return nodes.get(selector);
    },
    querySelectorAll(selector) {
      if (selector === 'input, select, textarea') return ['action', 'airports', 'metric', 'year', 'threshold', 'question', 'airport-picker'].map(id => {
        const key = `#${id}`; if (!nodes.has(key)) nodes.set(key, new Element(id)); return nodes.get(key);
      });
      return [];
    }, addEventListener(type, listener) { documentListeners.set(type, listener); }, createElement(tag) { const node = new Element(); node.tagName = tag; if (tag === 'details') node.open = false; return node; }, createElementNS(_ns, tag) { const node = new Element(); node.tagName = tag; Object.defineProperty(node, 'className', { configurable: true, get: () => node.getAttribute('class') || '' }); return node; },
  };
  const events = [];
  const listeners = new Map();
  class UiCustomEvent { constructor(type, options = {}) { this.type = type; Object.assign(this, options); } }
  const window = {
    addEventListener(type, listener, options = {}) { listeners.set(type, { listener, once: options.once }); },
    dispatchEvent(event) { events.push(event); const entry = listeners.get(event.type); entry?.listener(event); if (entry?.once) listeners.delete(event.type); return true; },
  };
  const context = vm.createContext({
    document,
    fetch: async (url, options) => url === '/health' ? healthQuery(url, options) : query(url, options),
    window, CustomEvent: UiCustomEvent, requestAnimationFrame: callback => callback(),
    setTimeout, clearTimeout, AbortController, URL, Intl, console, ...overrides,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), context);
  return { context, document, documentListeners, nodes, events, window, CustomEvent: UiCustomEvent, run: (code) => vm.runInContext(code, context) };
}
function result() {
  return { result_id: 'result-1', request_id: 'request-1', status: 'partial',
    scope: { airports: ['LAX'], year: 2024, metric: 'cancellation_rate', population: 'Reporting carriers' },
    summary: 'A qualified summary', rows: [{ airport: 'LAX', rank: 1, metrics: [{ key: 'cancellation_rate', value: 2.5, unit: 'percent', status: 'ok', numerator: 1, denominator: 40, eligible_count: 40, comparison_direction: 'higher', source_ids: ['source'] }] }],
    series: [{ period: '202401', value: null, unit: 'count', status: 'unavailable' }],
    sources: [{ id: 'source', name: 'BTS', url: 'https://example.test', snapshot_id: 'snap', period: '2024', retrieved_at: null }],
    evidence: [{ source_id: 'source', date: '2024', locator: 'page 2', claim: '<b>Plain evidence</b>', limitation: 'Counterevidence retained' }],
    exclusions: ['PVC incomplete'], limitations: ['Not a causal conclusion'] };
}
function content(node) { return [node.textContent, ...node.children.map(content)].join(' '); }
function findDescendant(node, predicate) {
  if (!node) return null;
  if (predicate(node)) return node;
  for (const child of node.children || []) { const found = findDescendant(child, predicate); if (found) return found; }
  return null;
}
function resultContent(ui) { return `${content(ui.nodes.get('#result-title'))} ${content(ui.nodes.get('#result'))}`; }
function disclosureByTitle(node, title) { return findDescendant(node, child => child.children?.[0]?.textContent === title); }
const success = (payload) => ({ ok: true, json: async () => payload });

test('composer explains that free-text is sent to the analysis service', () => {
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  const helper = html.match(/<p id="question-help"[^>]*>([^<]*)<\/p>/);
  assert.ok(helper, 'chat helper must be present');
  assert.equal(helper[1], 'Your question is sent to the analysis service.');
  assert.match(html, /<form id="chat-form" novalidate>/, 'the accessible inline empty-question error runs instead of a browser tooltip');
  assert.match(html, /id="conversation-composer"[^>]*hidden inert/);
  assert.match(html, /id="ask-trigger"[^>]*aria-expanded="false"/);
  assert.match(html, /id="question"[^>]*aria-describedby="question-help question-error"/);
  assert.match(html, /id="question-error"[^>]*role="alert"[^>]*aria-live="assertive"/);
  for (const field of ['action', 'airports', 'metric', 'year', 'threshold']) {
    assert.match(html, new RegExp(`id="${field}-error"[^>]*role="alert"[^>]*aria-live="assertive"`));
  }
});

test('empty composer submission links and announces its field error, then clears it on input', () => {
  const ui = setup();
  const form = ui.run('$("#chat-form")');
  const question = ui.run('$("#question")');
  const error = ui.run('$("#question-error")');
  form.listeners.submit({ preventDefault() {} });
  assert.equal(error.textContent, 'Enter a question before sending.');
  assert.equal(error.hidden, false);
  assert.equal(question.getAttribute('aria-invalid'), 'true');
  assert.equal(question.focused, true);
  question.value = 'Compare SFO demand';
  question.listeners.input();
  assert.equal(error.hidden, true);
  assert.equal(error.textContent, '');
  assert.equal(question.getAttribute('aria-invalid'), null);
});

test('idle and result states keep the compact contact header and open the result composer on activation', async () => {
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  const header = html.match(/<header class="contact-header">([\s\S]*?)<\/header>/)?.[1] || '';
  assert.match(header, /class="contact"/);
  assert.doesNotMatch(header, /<nav|<h1|<button|brand|logo/i);
  const idlePromptBlock = html.match(/<div class="prompt-grid">([\s\S]*?)<\/div>/)?.[1] || '';
  assert.equal([...idlePromptBlock.matchAll(/data-preset=/g)].length, 4, 'the initial state offers four prompts');
  assert.match(html, /id="back-to-analysis"[^>]*hidden/);
  assert.match(html, /id="conversation-composer"[^>]*hidden[^>]*inert/);
  assert.match(html, /id="ask-trigger"[^>]*aria-expanded="false"/);
  assert.doesNotMatch(html, /id="composer-close"|class="follow-up-suggestions"/);
  const composerMarkup = html.match(/<section id="conversation-composer"[\s\S]*?<\/section>/)?.[0] || '';
  assert.match(composerMarkup, /id="question"/);
  assert.match(composerMarkup, /class="composer-send"/);
  assert.doesNotMatch(composerMarkup, /data-preset=|chip/i);

  const ui = setup(async () => success(result()));
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(ui.nodes.get('#back-to-analysis').hidden, false);
  const composer = ui.nodes.get('#conversation-composer');
  const trigger = ui.nodes.get('#ask-trigger');
  assert.equal(composer.hidden, true, 'the full composer stays collapsed when a result arrives');
  assert.equal(composer.inert, true);
  assert.equal(composer.attrs['aria-hidden'], 'true');
  assert.equal(trigger.hidden, false);
  assert.equal(trigger.textContent, 'Ask a follow-up →');
  assert.ok(ui.nodes.get('#result').children.includes(trigger), 'the follow-up trigger is in the visible result content');
  assert.equal(trigger.attrs['aria-expanded'], 'false');
  trigger.click();
  assert.equal(composer.hidden, false);
  assert.equal(composer.inert, false);
  assert.equal(composer.attrs['aria-hidden'], 'false');
  assert.equal(trigger.hidden, true);
  assert.equal(trigger.attrs['aria-expanded'], 'true');
  assert.equal(ui.nodes.get('#question').focused, true, 'explicit activation moves focus into the composer');
  ui.documentListeners.get('keydown')({ key: 'Escape' });
  assert.equal(composer.inert, true, 'Escape closes the active composer');
  assert.equal(composer.attrs['aria-hidden'], 'true');
  assert.equal(trigger.hidden, false);
  assert.equal(trigger.focused, true, 'Escape restores focus to the compact trigger');
});

test('renders rows, rank, backend percentages, denominator, directions, evidence, series and limitations', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  for (const expected of ['Partial result', '2.50%', '1 / 40', 'higher', 'Unavailable', 'Counterevidence retained', 'PVC incomplete', 'Not a causal conclusion', '<b>Plain evidence</b>']) assert.ok(text.includes(expected), expected);
  assert.ok(!text.includes('250.00%'));
});

test('result heading leads with status, returned scope, and verbatim backend summary before metric details', () => {
  const ui = setup(async () => success(result())); ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  assert.equal(ui.nodes.get('#result-title').textContent, 'Partial result');
  const children = ui.nodes.get('#result').children;
  assert.equal(children[0].className, 'result-scope-heading');
  assert.equal(children[0].children[0].textContent, 'LAX');
  assert.equal(children[0].children[1].textContent, 'Cancellation rate · 2024');
  const insights = findDescendant(ui.nodes.get('#result'), node => node.className === 'key-insights');
  assert.ok(insights);
  assert.equal(insights.children[1].textContent, result().summary);
  assert.ok(findDescendant(ui.nodes.get('#result'), node => node.id === 'metric-view'));
});

test('context strip mirrors validated scope, status and at most two returned values', () => {
  const ui = setup(); const payload = result();
  payload.scope.airports = ['LAX', 'SNA']; payload.scope.metric = 'congestion';
  payload.rows = ['LAX', 'SNA'].map((airport, index) => ({ airport, metrics: [
    { key: 'cancellation_rate', value: index, unit: 'percent', status: 'ok', numerator: index, denominator: 100, source_ids: ['source'] },
    { key: 'diversion_rate', value: 2, unit: 'percent', status: 'ok', numerator: 2, denominator: 100, source_ids: ['source'] },
  ] }));
  ui.context.payload = payload; ui.run('latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  const strip = ui.nodes.get('#context-summary').textContent;
  assert.match(strip, /^LAX \/ SNA · congestion · 2024/);
  assert.match(strip, /LAX Cancellation rate · 0\.00%/);
  assert.match(strip, /SNA Cancellation rate · 1\.00%/);
  assert.doesNotMatch(strip, /diversion rate/);
  ui.run('renderResult(latestSuccessfulResult, true)');
  assert.match(ui.nodes.get('#context-summary').textContent, /^LAX \/ SNA · congestion · 2024/);
  assert.equal(ui.nodes.get('#result-title').textContent, 'Previous result');
});

test('typed result dispatcher keeps the complete generic fallback for other supported metrics', () => {
  const ui = setup(async () => success(result()));
  ui.context.payload = result();
  ui.run('payload.scope.metric = "passengers"; payload.rows[0].metrics = [{ key: "passengers", value: 420, unit: "count", status: "ok", numerator: null, denominator: null, eligible_count: null, comparison_direction: null, source_ids: ["source"] }]; latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  assert.ok(resultContent(ui).includes('Airport metrics — values supplied by the backend'));
  assert.ok(resultContent(ui).includes('420'));
  assert.ok(resultContent(ui).includes('Sources'));
  assert.ok(resultContent(ui).includes('Not a causal conclusion'));
});

test('congestion comparison aligns both backend airport columns and preserves partial and numeric-zero states', () => {
  const keys = ['cancellation_rate', 'diversion_rate', 'departure_delay_minutes', 'taxi_out_minutes'];
  const build = (value = .5, unavailable = false) => {
    const payload = result(); payload.status = 'ok'; payload.scope.metric = 'congestion'; payload.scope.airports = ['LAX', 'SNA'];
    payload.rows = ['LAX', 'SNA'].map((airport, airportIndex) => ({ airport, metrics: keys.map((key, index) => {
      const unit = index < 2 ? 'percent' : 'minutes';
      const absent = unavailable && airportIndex === 1 && index === 1;
      return { key, unit, status: absent ? 'unavailable' : 'ok', value: absent ? null : value + airportIndex + index,
        reason: absent ? 'No eligible observations' : null, numerator: index < 2 ? (absent ? null : value + airportIndex) : null,
        denominator: index < 2 ? (absent ? null : 100) : null, eligible_count: absent ? null : 100,
        comparison_direction: absent ? 'unavailable' : airportIndex ? 'lower' : 'higher', source_ids: ['source'] };
    }) }));
    return payload;
  };
  const render = payload => { const ui = setup(async () => success(payload)); ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)'); return ui; };
  const complete = render(build(0));
  const view = findDescendant(complete.nodes.get('#result'), node => node.id === 'metric-view');
  const exact = disclosureByTitle(view, 'Exact values and methodology');
  assert.equal(exact.children[0].textContent, 'Exact values and methodology');
  const region = exact.children.find(node => (node.className || '').includes('table-region'));
  const body = region.children[0].children[2];
  assert.deepEqual(body.children.map(row => row.children.map(cell => cell.textContent)), [
    ['Cancellation rate', '0.00% ↑', '1.00% ↓'],
    ['Diversion rate', '1.00% ↑', '2.00% ↓'],
    ['Departure delay', '2.00 min ↑', '3.00 min ↓'],
    ['Taxi out', '3.00 min ↑', '4.00 min ↓'],
  ]);
  assert.equal(body.children[0].children[1].getAttribute('aria-label'), '0.00%, higher');
  const plot = findDescendant(view, node => node.className === 'comparison-dumbbell');
  assert.equal(plot.getAttribute('role'), 'img');
  assert.match(plot.getAttribute('aria-label'), /zero-inclusive/);
  assert.equal(plot.children.filter(node => (node.getAttribute('class') || '').startsWith('dumbbell-point')).length, 2);
  const methodology = exact.children.find(node => node.className === 'methodology-detail');
  assert.match(content(methodology), /Numerator \/ denominator: 0 \/ 100/);
  assert.match(content(methodology), /Eligible observations: 100/);
  assert.match(content(methodology), /Direction: higher/);
  assert.match(content(methodology), /Source IDs: source/);
  assert.doesNotMatch(content(view), /4 of 4|combined congestion/i);
  const partialPayload = build(.5, true);
  partialPayload.rows[0].metrics = partialPayload.rows[0].metrics.slice(0, 3);
  const partial = render(partialPayload);
  assert.ok(resultContent(partial).includes('Unavailable: No eligible observations'));
  assert.ok(resultContent(partial).includes('Not returned'));
});

test('screening visual order follows backend rows, keeps tied ranks, and labels unranked and unavailable values', () => {
  const ui = setup(async () => success(result()));
  const payload = result(); payload.status = 'ok'; payload.scope.metric = 'screen_score'; payload.scope.airports = ['BOS', 'BDL', 'BGR'];
  const metric = (key, value, unit, extra = {}) => ({ key, value, unit, status: extra.reason ? 'unavailable' : 'ok',
    numerator: key === 'seat_occupancy' ? 80 : null, denominator: key === 'seat_occupancy' ? 100 : null,
    eligible_count: null, comparison_direction: null, reason: extra.reason || null, source_ids: ['source'] });
  payload.rows = [
    { airport: 'BOS', rank: 2, metrics: [metric('screen_score', 74, 'score'), metric('passenger_growth', 3.1, 'percent'), metric('passengers', 900, 'count'), metric('seat_occupancy', 80, 'percent')] },
    { airport: 'BDL', rank: 2, metrics: [metric('screen_score', 74, 'score'), metric('passenger_growth', 2.2, 'percent'), metric('passengers', 700, 'count'), metric('seat_occupancy', 80, 'percent')] },
    { airport: 'BGR', rank: null, metrics: [metric('screen_score', null, 'score', { reason: 'Incomplete historical measures' })] },
  ];
  ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const view = findDescendant(ui.nodes.get('#result'), node => node.id === 'metric-view');
  const list = view.children.find(node => (node.className || '').includes('ranking-preview'));
  assert.deepEqual(list.children.map(row => row.children.find(child => child.className === 'backend-rank').textContent), ['2', '2'], 'tied backend ranks remain tied');
  assert.deepEqual(list.children.map(row => row.children.find(child => child.tagName === 'h3').textContent), ['BOS', 'BDL']);
  assert.equal(list.children[0].getAttribute('role'), 'listitem');
});

test('dumbbell bounds include zero and keep negative, zero and equal values distinct', () => {
  const payload = result(); payload.status = 'ok'; payload.scope.metric = 'congestion'; payload.scope.airports = ['LAX', 'SNA'];
  const values = { LAX: [-2, 0, 0, 1], SNA: [3, 0, -1, 1] };
  const keys = ['cancellation_rate', 'diversion_rate', 'departure_delay_minutes', 'taxi_out_minutes'];
  payload.rows = Object.keys(values).map(airport => ({ airport, metrics: keys.map((key, index) => ({
    key, unit: index < 2 ? 'percent' : 'minutes', value: values[airport][index], status: 'ok',
    numerator: index < 2 ? Math.max(0, values[airport][index]) : null, denominator: index < 2 ? 100 : null,
    eligible_count: 100, comparison_direction: values.LAX[index] === values.SNA[index] ? 'tied' : 'higher', source_ids: ['source'],
  })) }));
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const view = findDescendant(ui.nodes.get('#result'), node => node.id === 'metric-view');
  const plots = [];
  const collect = node => { if (node.className === 'comparison-dumbbell') plots.push(node); for (const child of node.children || []) collect(child); };
  collect(view);
  assert.match(plots[0].getAttribute('aria-label'), /-2\.00% to 3\.00%/);
  const equalCircles = plots[1].children.filter(node => (node.getAttribute('class') || '').startsWith('dumbbell-point'));
  assert.equal(equalCircles.length, 2);
  assert.equal(equalCircles[0].getAttribute('cx'), equalCircles[1].getAttribute('cx'));
  assert.notEqual(equalCircles[0].getAttribute('cy'), equalCircles[1].getAttribute('cy'));
});

test('long-haul share shows supplied percent, threshold, and exact counts without deriving values', () => {
  const make = (status, value, numerator, denominator, reason = null) => {
    const payload = result(); payload.status = status === 'unavailable' ? 'partial' : 'ok';
    payload.scope.metric = 'long_haul_share'; payload.scope.airports = ['ANC']; payload.scope.threshold_miles = 3000;
    payload.rows = [{ airport: 'ANC', metrics: [{ key: 'long_haul_share', value, unit: 'percent', status, numerator, denominator,
      eligible_count: null, comparison_direction: null, reason, source_ids: ['source'] }] }];
    return payload;
  };
  const render = payload => { const ui = setup(async () => success(payload)); ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)'); return ui; };
  const complete = render(make('ok', 25, 5, 20));
  const text = content(findDescendant(complete.nodes.get('#result'), node => node.id === 'metric-view'));
  assert.match(text, /25\.00%/);
  assert.match(text, /at least 3,000 miles/);
  assert.match(text, /Long-haul performed departures.*5/);
  assert.match(text, /Eligible performed departures: 20/);
  assert.doesNotMatch(text, /25\/100|calculated|approx/i);
  const ring = findDescendant(complete.nodes.get('#result'), node => node.className === 'share-ring');
  assert.equal(ring.getAttribute('role'), 'img');
  assert.match(ring.getAttribute('aria-label'), /25\.00%/);
  assert.equal(ring.children[1].getAttribute('stroke-dasharray').split(' ')[0], String(2 * Math.PI * 36 * .25));
  assert.equal(content(findDescendant(complete.nodes.get('#result'), node => node.className === 'long-haul-share-label')), 'Long-haul share');
  assert.equal(ring.children.find(node => node.getAttribute('class') === 'share-ring-center').textContent, '25.00%');
  const returnedShare = render(make('ok', 2.373991, 950, 40017));
  const returnedRing = findDescendant(returnedShare.nodes.get('#result'), node => node.className === 'share-ring');
  assert.equal(returnedRing.children.find(node => node.getAttribute('class') === 'share-ring-center').textContent, '2.37%', 'center label is formatted directly from the returned metric.value');
  const zero = render(make('ok', 0, 0, 0));
  assert.match(content(zero.nodes.get('#result')), /0\.00%/);
  assert.match(content(zero.nodes.get('#result')), /Eligible performed departures: 0/);
  const missing = render(make('unavailable', null, null, null, 'Coverage incomplete'));
  assert.match(content(missing.nodes.get('#result')), /Unavailable: Coverage incomplete/);
  assert.doesNotMatch(content(missing.nodes.get('#result')), /0\.00%/);
});

test('SFO pressure rendering preserves returned values, unavailable reasons and source lineage', () => {
  const ui = setup(async () => success(result()));
  const payload = result(); payload.status = 'partial'; payload.scope.metric = 'sfo_pressure'; payload.scope.airports = ['SFO'];
  const metric = (key, value, unit, extra = {}) => ({ key, value, unit, status: extra.reason ? 'unavailable' : 'ok',
    numerator: extra.numerator ?? null, denominator: extra.denominator ?? null, eligible_count: extra.eligible_count ?? null,
    comparison_direction: null, reason: extra.reason || null, source_ids: ['source'] });
  payload.rows = [{ airport: 'SFO', metrics: [
    metric('passengers', 1000, 'count'), metric('seats', 1200, 'count'), metric('departures', 12, 'count'),
    metric('passenger_growth', 2.5, 'percent'), metric('seat_occupancy', null, 'percent', { reason: 'Seats unavailable' }),
    metric('sfo_pressure', -1.2, 'percentage_points'), metric('sfo_enplaned_trend', 90, 'count'),
    metric('taxi_out_minutes', 15, 'minutes'),
  ] }];
  ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  assert.match(text, /-1\.2/);
  assert.match(text, /Unavailable: Seats unavailable/);
  assert.match(text, /1,000/);
  assert.match(text, /Source IDs: source/);
  assert.match(text, /90/);
  assert.doesNotMatch(text, /seat growth/i);
  const missingOptional = result(); missingOptional.status = 'partial'; missingOptional.scope.metric = 'sfo_pressure';
  missingOptional.rows = [{ airport: 'SFO', metrics: [metric('sfo_pressure', 0, 'percentage_points')] }];
  missingOptional.scope.airports = ['SFO'];
  ui.context.payload = missingOptional;
  ui.run('renderResult(validateResult(payload), false)');
  assert.match(resultContent(ui), /Passenger growth gap|-1\.2|0/);
  assert.doesNotMatch(resultContent(ui), /seat growth/i);
});

test('monthly series rendering retains returned values and unavailable gaps', () => {
  const ui = setup(async () => success(result()));
  const payload = result(); payload.series = [
    { period: '202401', value: 0, unit: 'count', status: 'ok' },
    { period: '202402', value: null, unit: 'count', status: 'unavailable' },
    { period: '202403', value: 12, unit: 'count', status: 'ok' },
  ];
  ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)');
  const seriesText = resultContent(ui);
  assert.match(seriesText, /2024-01/);
  assert.match(seriesText, /Unavailable: Value unavailable/);
  assert.match(seriesText, /2024-03/);
  assert.match(seriesText, /12/);
  const empty = result(); empty.series = []; empty.status = 'partial'; empty.scope.metric = 'sfo_pressure'; empty.scope.airports = ['SFO'];
  empty.rows = [{ airport: 'SFO', metrics: [{ key: 'sfo_pressure', value: 0, unit: 'percentage_points', status: 'ok', source_ids: ['source'] }] }];
  ui.context.payload = empty;
  ui.run('renderResult(validateResult(payload), false)');
  assert.match(resultContent(ui), /No monthly series was returned|No series/i);
});

test('monthly trend rendering keeps gaps and isolated returned observations explicit', () => {
  const payload = result(); payload.scope.metric = 'sfo_enplaned_trend'; payload.scope.airports = ['SFO'];
  payload.rows = [{ airport: 'SFO', metrics: [{ key: 'sfo_enplaned_trend', value: 10, unit: 'count', status: 'ok', source_ids: ['source'] }] }];
  payload.series = [
    { period: '202401', value: 0, unit: 'count', status: 'ok' },
    { period: '202402', value: 4, unit: 'count', status: 'ok' },
    { period: '202403', value: null, unit: 'count', status: 'unavailable' },
    { period: '202404', value: 8, unit: 'count', status: 'ok' },
    { period: '202406', value: 12, unit: 'count', status: 'ok' },
  ];
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  for (const value of ['2024-01', '2024-02', 'Unavailable', '2024-04', '2024-06', '12']) assert.ok(text.includes(value), value);
});

test('two-year monthly chart labels returned year boundaries and keeps exact series values', () => {
  const payload = result(); payload.scope.metric = 'sfo_enplaned_trend'; payload.scope.airports = ['SFO'];
  payload.rows = [{ airport: 'SFO', metrics: [{ key: 'sfo_enplaned_trend', value: 24, unit: 'count', status: 'ok', source_ids: ['source'] }] }];
  payload.series = Array.from({ length: 24 }, (_, index) => ({
    period: `${index < 12 ? '2023' : '2024'}${String((index % 12) + 1).padStart(2, '0')}`,
    value: index + 1, unit: 'count', status: 'ok',
  }));
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const chart = findDescendant(ui.nodes.get('#result'), node => node.tagName === 'svg' && node.getAttribute('role') === 'img');
  const labels = chart.children.filter(node => node.getAttribute('class') === 'series-axis-label').map(node => node.textContent);
  assert.deepEqual(labels.filter(text => text.startsWith('Jan ')), ['Jan 2023', 'Jan 2024']);
  assert.match(labels.at(-1), /2024-12/);
  assert.match(resultContent(ui), /2023-01/);
  assert.match(resultContent(ui), /2024-12/);
});

test('request and source technical identifiers stay within the closed evidence disclosure', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  const group = findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group');
  assert.ok(group);
  assert.equal(group.open, false, 'evidence, source lineage and technical details are initially contained');
  const technical = findDescendant(group, node => node.className === 'technical-details');
  assert.equal(technical.open, false, 'technical identifiers are disclosed only on request');
  assert.match(content(technical), /Request ID: request-1/);
  assert.match(resultContent(ui), /BTS/);
  assert.match(resultContent(ui), /Source ID: source/);
  assert.match(resultContent(ui), /Snapshot: snap/);
});

test('partial metric is explicitly unavailable rather than zero', () => {
  const ui = setup(); const payload = result();
  Object.assign(payload.rows[0].metrics[0], { value: null, status: 'unavailable', reason: 'Missing month' });
  ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)');
  assert.ok(content(ui.nodes.get('#result')).includes('Unavailable: Missing month'));
});

test('evidence claim and counterevidence remain available inside the closed result disclosure', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('renderResult(validateResult(payload), false)');
  const group = findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group');
  assert.ok(group);
  assert.equal(group.open, false);
  const text = resultContent(ui);
  assert.match(text, /Plain evidence/);
  assert.match(text, /page 2/);
  assert.match(text, /Counterevidence retained/);
});

test('rejects nonfinite values, unsafe URLs and unresolved source references', () => {
  const ui = setup();
  for (const mutate of [p => p.rows[0].metrics[0].value = NaN, p => p.sources[0].url = 'javascript:alert(1)', p => p.rows[0].metrics[0].source_ids = ['missing']]) {
    const payload = result(); mutate(payload); ui.context.payload = payload;
    assert.throws(() => ui.run('validateResult(payload)'));
  }
});

test('result admission mirrors supported scope, metric/unit, rank and ratio-pair contract invariants', () => {
  const airports = 'BDL HVN PWM BGR PQI RKD BHB AUG BOS ACK ORH MVY HYA PVC MHT PSM LEB PVD WST BID BTV RUT ANC LAX'.split(' ');
  const invalid = [
    ['empty scope airports', value => { value.scope.airports = []; }],
    ['unsupported scope airport', value => { value.scope.airports[0] = 'XXX'; }],
    ['duplicate scope airport', value => { value.scope.airports.push('LAX'); }],
    ['scope exceeds contract airport limit', value => { value.scope.airports = airports; }],
    ['unsupported scope metric', value => { value.scope.metric = 'invented_score'; }],
    ['unsupported metric key', value => { value.rows[0].metrics[0].key = 'invented_score'; }],
    ['metric/unit mismatch', value => { value.rows[0].metrics[0].unit = 'count'; }],
    ['fractional rank', value => { value.rows[0].rank = 1.5; }],
    ['rank below range', value => { value.rows[0].rank = 0; }],
    // The accepted 2025 New England cohort has 23 airports (EWB added), so rank 23 is now valid.
    ['rank above range', value => { value.rows[0].rank = 24; }],
    ['unpaired ratio counts', value => { value.rows[0].metrics[0].denominator = null; }],
    ['nonfinite ratio counts', value => { value.rows[0].metrics[0].numerator = Infinity; }],
    ['successful ratio without counts', value => { value.rows[0].metrics[0].numerator = null; value.rows[0].metrics[0].denominator = null; }],
  ];
  for (const [name, mutate] of invalid) {
    const ui = setup(async () => success(result()));
    ui.context.fixture = result(); mutate(ui.context.fixture);
    assert.throws(() => ui.run('validateResult(fixture)'), undefined, name);
  }
});

test('malformed response fails admission before commit and retains a previous result', async () => {
  const malformed = result(); malformed.rows[0].metrics[0].unit = 'count';
  let requests = 0;
  const ui = setup(async () => { requests++; return success(malformed); });
  ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  const retained = ui.run('latestSuccessfulResult');
  await ui.run('submitRequest({ analysis: { action: "metric" } })');
  assert.equal(requests, 1);
  assert.equal(ui.run('latestSuccessfulResult'), retained);
  assert.equal(ui.nodes.get('#result-title').textContent, 'Previous result');
  assert.equal(ui.run('latestSuccessfulResult.result_id'), 'result-1');
  assert.equal(ui.nodes.get('#feedback').getAttribute('role'), 'alert');
});

test('HTTP error after success preserves Previous result and does not retry', async () => {
  let calls = 0;
  const ui = setup(async () => ++calls === 1 ? success(result()) : { ok: false, status: 409, json: async () => ({ success: false, error: { code: 'result_mismatch', message: 'Stale result', request_id: '123e4567-e89b-42d3-a456-426614174000' } }) });
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(calls, 2);
  assert.ok(resultContent(ui).includes('Previous result'));
  assert.ok(ui.nodes.get('#feedback').textContent.includes('Start a new analysis'));
  assert.equal(ui.run('busy'), false);
});

test('input-change generation guard rejects late response and releases busy state', async () => {
  let resolve;
  const ui = setup(() => new Promise(done => { resolve = done; }));
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  ui.run('changedDraft()'); resolve(success(result())); await pending;
  assert.equal(ui.run('latestSuccessfulResult'), null);
  assert.equal(ui.run('busy'), false);
});

test('Back returns to idle and a late success cannot restore the result or replace its idle feedback', async () => {
  let resolve;
  const ui = setup(() => new Promise(done => { resolve = done; }));
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  const back = ui.nodes.get('#back-to-analysis');
  ui.run('$("#back-to-analysis").hidden = false');
  back.click();
  const idleFeedback = ui.nodes.get('#feedback').textContent;
  assert.equal(ui.nodes.get('#result-panel').hidden, true);
  assert.equal(ui.nodes.get('#conversation-composer').hidden, true);
  assert.equal(ui.run('busy'), false);
  resolve(success(result()));
  await pending;
  assert.equal(ui.run('latestSuccessfulResult'), null);
  assert.equal(ui.nodes.get('#result-panel').hidden, true);
  assert.equal(ui.nodes.get('#result-title').hidden, true);
  assert.equal(ui.nodes.get('#feedback').textContent, idleFeedback, 'stale finally must not overwrite the Back state');
});

test('Back invalidates late request errors and their finally feedback', async () => {
  let reject;
  const ui = setup(() => new Promise((_resolve, fail) => { reject = fail; }));
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  ui.run('$("#back-to-analysis").hidden = false');
  ui.nodes.get('#back-to-analysis').click();
  const idleFeedback = ui.nodes.get('#feedback').textContent;
  reject(new Error('late transport failure'));
  await pending;
  assert.equal(ui.nodes.get('#result-panel').hidden, true);
  assert.equal(ui.nodes.get('#feedback').textContent, idleFeedback, 'late catch and finally must not overwrite the Back state');
  assert.equal(ui.run('busy'), false);
});

test('Back after a completed result clears retained context before invalidating the next response', async () => {
  let calls = 0, resolveSecond;
  const ui = setup(() => ++calls === 1 ? Promise.resolve(success(result())) : new Promise(resolve => { resolveSecond = resolve; }));
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.ok(ui.run('latestSuccessfulResult'));
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  ui.nodes.get('#back-to-analysis').click();
  assert.equal(ui.run('latestSuccessfulResult'), null, 'Back clears the retained result from active context');
  assert.equal(ui.run('contextResultId'), null, 'Back clears conversational result context');
  resolveSecond(success({ ...result(), result_id: 'late-result' }));
  await pending;
  assert.equal(ui.run('latestSuccessfulResult'), null);
  assert.equal(ui.nodes.get('#result-panel').hidden, true);
});

test('duplicate send is suppressed and non-JSON errors preserve the previous result', async () => {
  let calls = 0, resolve;
  const ui = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(calls, 1); resolve(success(result())); await pending;
  const failure = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  resolve({ ok: true, json: async () => { throw new SyntaxError('HTML'); } }); await failure;
  assert.ok(resultContent(ui).includes('Previous result'));
  assert.equal(ui.nodes.get('#feedback').attrs.role, 'alert');
});

test('scope validator rejects invalid structures and accepts supported historical scopes', () => {
  const ui = setup(async () => { throw new Error('invalid scope reached fetch'); });
  const invalid = [
    { action: 'compare', airports: ['BOS'], metric: 'passengers', year: 2024 },
    { action: 'rank', region: 'new_england', metric: 'departures', year: 2024 },
    { action: 'metric', airports: ['BOS', 'PVD'], metric: 'passengers', year: 2024 },
    { action: 'metric', airports: ['BOS'], metric: 'passenger_growth', year: 2023 },
  ];
  for (const analysis of invalid) {
    ui.context.analysis = analysis;
    assert.ok(ui.run('validateAnalysisScope(analysis).length > 0'));
  }
  const valid = [
    { action: 'metric', airports: ['ANC'], metric: 'passengers', year: 2023 },
    { action: 'compare', airports: ['BOS', 'PVD'], metric: 'seat_occupancy', year: 2023 },
    { action: 'rank', region: 'new_england', metric: 'passengers', year: 2023 },
  ];
  for (const analysis of valid) {
    ui.context.analysis = analysis;
    assert.equal(ui.run('validateAnalysisScope(analysis).length'), 0);
  }
});

test('invalid scope sends no query request', async () => {
  let calls = 0;
  const ui = setup(async () => { calls++; return success(result()); });
  await ui.run(`submitScope({ action: 'compare', airports: ['BOS'], metric: 'passengers', year: 2024 })`);
  assert.equal(calls, 0);
});

test('parses only the declared error envelope fields', () => {
  const ui = setup(async () => success(result()));
  ui.context.envelope = { success: false, error: { code: 'busy', message: 'Wait for the current analysis.', request_id: '123e4567-e89b-42d3-a456-426614174000' } };
  const parsed = ui.run('parseErrorResponse(envelope)');
  assert.equal(parsed.code, 'busy');
  assert.equal(parsed.message, 'Wait for the current analysis.');
  assert.equal(parsed.requestId, '123e4567-e89b-42d3-a456-426614174000');
  for (const malformed of [
    { error: { code: 'busy', message: 'missing success', request_id: '123e4567-e89b-42d3-a456-426614174000' } },
    { success: false, error: { code: 'unknown', message: 'bad code', request_id: '123e4567-e89b-42d3-a456-426614174000' } },
    { success: false, error: { code: 'busy', message: '', request_id: '123e4567-e89b-42d3-a456-426614174000' } },
    { success: false, error: { code: 'busy', message: 'bad id', request_id: 'not-a-uuid' } },
  ]) {
    ui.context.malformed = malformed;
    assert.equal(ui.run('parseErrorResponse(malformed)'), null);
  }
});

test('malformed HTTP error uses only the local connection message', async () => {
  const ui = setup(async () => ({ ok: false, status: 503, json: async () => ({ raw: 'upstream traceback' }) }));
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(ui.nodes.get('#feedback').textContent, 'The backend is unavailable or returned an invalid response. The previous result is retained. Check the local server and retry explicitly.');
  assert.ok(!ui.nodes.get('#feedback').textContent.includes('traceback'));
});

test('busy response retains context and asks the user to wait without reset', async () => {
  let calls = 0;
  const busy = { success: false, error: { code: 'busy', message: 'Another analysis is running. Wait and try again.', request_id: '123e4567-e89b-42d3-a456-426614174000' } };
  const ui = setup(async () => ++calls === 1 ? success(result()) : { ok: false, status: 409, json: async () => busy });
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(calls, 2);
  assert.ok(resultContent(ui).includes('Previous result'));
  assert.equal(ui.run('contextResultId'), 'result-1');
  assert.ok(ui.nodes.get('#feedback').textContent.includes('Wait and try again'));
  assert.ok(!ui.nodes.get('#feedback').textContent.includes('Start a new analysis'));
  assert.match(content(ui.nodes.get('#feedback')), /Technical request details/);
  assert.match(content(ui.nodes.get('#feedback')), /123e4567-e89b-42d3-a456-426614174000/);
});

test('reset recovery is limited to expired or mismatched session context', () => {
  const ui = setup(async () => success(result()));
  for (const code of ['session_expired', 'result_mismatch']) {
    ui.context.detail = { code };
    assert.ok(ui.run('errorRecovery(detail, 409)').includes('Start a new analysis'));
  }
  ui.context.detail = { code: 'unsupported_scope' };
  assert.ok(!ui.run('errorRecovery(detail, 409)').includes('Start a new analysis'));
});

test('non-session failures retain drafts and context without retry', async () => {
  const error = (code, message) => ({ ok: false, status: code === 'unsupported_scope' ? 422 : 503, json: async () => ({
    success: false, error: { code, message, request_id: '123e4567-e89b-42d3-a456-426614174000' },
  }) });
  const cases = [
    ['422', async () => error('unsupported_scope', 'Unsupported scope.'), {}],
    ['503', async () => error('ai_unavailable', 'Interpretation is unavailable.'), {}],
    ['timeout', async (_url, options) => {
      if (options.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))));
    }, { setTimeout: (callback) => { callback(); return 1; }, clearTimeout: () => {} }],
    ['network', async () => { throw new TypeError('offline'); }, {}],
    ['non-JSON', async () => ({ ok: false, status: 502, json: async () => { throw new SyntaxError('HTML'); } }), {}],
  ];
  for (const [name, outcome, overrides] of cases) {
    let calls = 0;
    const ui = setup(async (url, options) => { calls++; return outcome(url, options); }, overrides);
    ui.context.seed = result();
    ui.run('latestSuccessfulResult = seed; contextResultId = seed.result_id; renderResult(seed, false)');
    const draft = { question: 'Keep my follow-up', action: 'compare', airports: 'BOS, PVD', metric: 'passengers', year: '2023', threshold: '2750' };
    for (const [id, value] of Object.entries(draft)) ui.run(`$("#${id}")`).value = value;
    await ui.run('submitRequest({analysis: {action: "compare", airports: ["BOS", "PVD"], metric: "passengers", year: 2023}})');
    assert.equal(calls, 1, `${name}: no automatic retry`);
    for (const [id, value] of Object.entries(draft)) assert.equal(ui.nodes.get(`#${id}`).value, value, `${name}: ${id} draft`);
    assert.ok(resultContent(ui).includes('Previous result'), `${name}: prior result`);
    assert.equal(ui.run('contextResultId'), 'result-1', `${name}: context retained`);
  }
});

test('successful response keeps focus in place and evidence navigation is deliberate', async () => {
  const ui = setup(async () => success(result()));
  const title = ui.run('$("#result-title")');
  title.focused = false;
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(title.hidden, false);
  assert.equal(title.focused, false, 'response arrival must not move focus');
  assert.equal(ui.nodes.get('#conversation-composer').hidden, true, 'result arrival leaves the full composer collapsed');
  assert.equal(ui.nodes.get('#ask-trigger').hidden, false, 'the result exposes an explicit follow-up action');
  ui.nodes.get('#view-evidence').click();
  const evidenceHeading = findDescendant(ui.nodes.get('#result'), node => node.id === 'analysis-evidence');
  assert.equal(evidenceHeading.focused, true, 'evidence action moves focus to its heading');
  assert.equal(findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group').open, true,
    'evidence navigation opens the containing disclosure before moving focus');
});

test('Back moves the follow-up trigger from result content into controls without relying on relocated feedback', async () => {
  const ui = setup(async () => success(result()));
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  const trigger = ui.nodes.get('#ask-trigger');
  assert.equal(trigger.parent, ui.nodes.get('#result'));
  assert.doesNotThrow(() => ui.run('startNewAnalysis()'));
  assert.equal(trigger.parent, ui.nodes.get('#controls'));
  assert.ok(ui.nodes.get('#controls').children.includes(trigger));
  assert.ok(!ui.nodes.get('#result').children.includes(trigger));
  assert.equal(trigger.hidden, false);
  assert.equal(trigger.textContent, 'Ask your own question →');
});

test('completion feedback remains available after setup collapses without duplicating the result status', async () => {
  const ui = setup(async () => success(result()));
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(ui.run('$("#setup-controls").open'), false);
  assert.equal(ui.nodes.get('#feedback').parent.id, 'result-title');
  assert.match(content(ui.nodes.get('#feedback')), /Partial result received/);
  assert.equal(ui.nodes.get('#result-title').focused, false, 'success does not steal focus');
  assert.equal(ui.nodes.get('#result-title').textContent, 'Partial result');
});

test('health probe times out once without implying source or model readiness', async () => {
  let calls = 0;
  const health = async (_url, options) => {
    calls++;
    if (!options.signal) return new Promise(() => {});
    if (options.signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))));
  };
  const ui = setup(async () => success(result()), { setTimeout: (callback) => { callback(); return 1; }, clearTimeout: () => {} }, health);
  await new Promise(setImmediate);
  assert.equal(calls, 1);
  const status = ui.run('$("#health-status")').textContent;
  assert.ok(status.includes('Connection unavailable'));
  assert.ok(!/source (?:data )?ready|model ready/i.test(status), 'connection state does not imply source or model readiness');
});

test('empty metric rows explain recovery and provide a working path to scope', () => {
  const ui = setup(async () => success(result()));
  const payload = result();
  payload.rows = [];
  ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = content(ui.nodes.get('#result'));
  assert.ok(text.includes('No metric rows were returned'));
  assert.ok(text.includes('exclusions and limitations'));
  const view = findDescendant(ui.nodes.get('#result'), node => node.id === 'metric-view');
  const action = view.children.find(node => node.textContent === 'Adjust scope');
  assert.ok(action, 'empty result includes a recovery action');
  action.click();
  assert.equal(ui.run('$("#setup-controls").open'), true);
  assert.equal(ui.run('$("#scope-panel").open'), true);
  assert.equal(ui.run('$("#action")').focused, true);
});

test('missing source references render diagnostic copy and a valid next action', () => {
  const ui = setup(async () => success(result()));
  const payload = result();
  payload.sources = [];
  payload.evidence = [];
  payload.rows[0].metrics[0].source_ids = [];
  ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = content(ui.nodes.get('#result'));
  assert.ok(text.includes('No source references were returned'));
  assert.ok(!text.includes('source confidence'));
  const evidenceGroup = findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group');
  assert.equal(evidenceGroup.open, false, 'the recovery action remains in the expandable evidence and source group');
  const sources = findDescendant(evidenceGroup, node => node.children.some(child => child.textContent === 'Values and sources'));
  const action = sources.children.find(node => node.textContent === 'Review scope');
  assert.ok(action, 'missing sources include a recovery action');
  action.click();
  assert.equal(ui.run('$("#setup-controls").open'), true);
  assert.equal(ui.run('$("#scope-panel").open'), true);
  assert.equal(ui.run('$("#action")').focused, true);
});

test('Start new analysis and valid presets clear stale scope validation state', async () => {
  const ui = setup(async () => success(result()));
  const metric = ui.run('$("#metric")');
  const metricError = ui.run('$("#metric-error")');
  const setScreeningError = () => ui.run(`showScopeErrors([{ field: "metric", message: "This metric is not available for comparisons." }])`);

  setScreeningError();
  assert.equal(metric.getAttribute('aria-invalid'), 'true');
  assert.ok(metric.getAttribute('aria-describedby').includes('metric-error'));
  ui.run('startNewAnalysis()');
  assert.equal(metric.getAttribute('aria-invalid'), null);
  assert.equal(metric.getAttribute('aria-describedby'), null);
  assert.equal(metricError.hidden, true);
  assert.equal(metricError.textContent, '');

  setScreeningError();
  await ui.run('runPreset(demos["anc-long-haul"])');
  assert.equal(metric.value, 'long_haul_share');
  assert.equal(ui.run('$("#airports")').value, 'ANC');
  assert.equal(metric.getAttribute('aria-invalid'), null);
  assert.equal(metric.getAttribute('aria-describedby'), null);
  assert.equal(metricError.hidden, true);
  assert.equal(metricError.textContent, '');
});

test('airport draft picker follows metric, compare and rank rules without submitting or moving focus', () => {
  let queries = 0;
  const ui = setup(async () => { queries++; return success(result()); });
  const airports = ui.run('$("#airports")'); airports.focused = true;
  ui.run('$("#action").value = "metric"'); airports.value = 'BOS, PVD';
  ui.run('addAirportToDraft("SFO")'); assert.equal(airports.value, 'SFO');
  ui.run('$("#action").value = "compare"'); airports.value = 'LAX';
  ui.run('addAirportToDraft("SNA")'); ui.run('addAirportToDraft("SFO")');
  ui.run('addAirportToDraft("SNA")'); assert.equal(airports.value, 'LAX, SNA');
  ui.run('$("#action").value = "rank"'); airports.value = 'BDL';
  ui.run('addAirportToDraft("BOS")'); assert.equal(airports.value, 'BDL, BOS');
  assert.equal(ui.run('addAirportToDraft("ANC")'), false);
  assert.equal(airports.value, 'BDL, BOS');
  assert.ok(ui.nodes.get('#feedback').textContent.includes('New England'));
  assert.equal(airports.focused, true); assert.equal(queries, 0);
});

test('picker selection alone preserves an in-flight request; adding the airport changes its generation', async () => {
  let resolveRequest;
  const ui = setup(() => new Promise(resolve => { resolveRequest = resolve; }));
  const request = ui.run('runPreset(demos["lax-sna"])');
  const generation = ui.run('requestGeneration');
  ui.run('$("#airport-picker").value = "BOS"');
  assert.equal(ui.run('$("#airport-picker").listeners.input'), undefined, 'helper selection has no draft-change handler');
  assert.equal(ui.run('requestGeneration'), generation, 'helper selection does not mutate the submitted draft');
  ui.run('$("#add-airport").click()');
  assert.equal(ui.run('requestGeneration'), generation + 1, 'explicit Add changes the draft and invalidates the response');
  resolveRequest(success(result()));
  await request;
  assert.equal(ui.run('latestSuccessfulResult'), null);
});

test('one feedback region follows the active action without taking focus', async () => {
  const ui = setup(async () => success(result()));
  const feedback = ui.nodes.get('#feedback');
  const analyze = ui.nodes.get('#analyze');
  const scope = ui.nodes.get('#scope-form');
  const followUp = ui.run('$("#follow-up")');
  ui.run('showFeedback("Preset message", false, $("#analyze"))');
  assert.equal(analyze.afterNode, feedback);
  ui.run('showFeedback("Scope message", true, $("#scope-form"))');
  assert.equal(scope.afterNode, feedback);
  ui.run('showFeedback("Follow-up message", false, $("#follow-up"))');
  assert.equal(followUp.afterNode, feedback);
  assert.equal(feedback.focused, false);
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  assert.equal((html.match(/id="feedback"/g) || []).length, 1);
});

test('current and previous result states remain labeled and keep the returned summary once', () => {
  const ui = setup(async () => success(result()));
  ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); renderResult(latestSuccessfulResult, false)');
  assert.equal(ui.nodes.get('#result-title').textContent, 'Partial result');
  assert.equal(content(ui.nodes.get('#result')).split(result().summary).length - 1, 1);
  const evidenceGroup = findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group');
  const limits = findDescendant(evidenceGroup, node => node.className === 'limits-detail');
  assert.equal(ui.nodes.get('#result-title').textContent, 'Partial result', 'partial status remains visible outside the closed disclosure');
  assert.equal(evidenceGroup.open, false);
  assert.equal(limits.open, false, 'limitations remain available inside the expandable disclosure');
  assert.match(content(evidenceGroup), /PVC incomplete/);
  ui.run('renderResult(latestSuccessfulResult, true)');
  assert.equal(ui.nodes.get('#result-title').textContent, 'Previous result');
  assert.equal(findDescendant(ui.nodes.get('#result'), node => node.className === 'evidence-source-group').open, false);
  assert.equal(content(ui.nodes.get('#result')).split(result().summary).length - 1, 1);
});

test('accepted airport add clears only airport validation and keeps the full picker draft local', () => {
  const ui = setup(async () => success(result()));
  const airports = ui.run('$("#airports")'); airports.focused = true;
  const error = ui.run('$("#airports-error")');
  airports.setAttribute('aria-invalid', 'true'); airports.setAttribute('aria-describedby', 'airports-error');
  error.hidden = false; error.textContent = 'Choose an airport.';
  const metric = ui.run('$("#metric")'); metric.setAttribute('aria-invalid', 'true');
  ui.run('$("#action").value = "compare"'); ui.run('$("#airport-picker").value = "PVD"');
  ui.nodes.get('#add-airport').click();
  assert.equal(airports.value, 'PVD'); assert.equal(ui.run('$("#airport-picker").value'), '');
  assert.equal(airports.getAttribute('aria-invalid'), null); assert.equal(error.hidden, true);
  assert.equal(metric.getAttribute('aria-invalid'), 'true'); assert.equal(airports.focused, true);
});

test('airportdraftselect is handled on window and emits non-bubbling draft state without a request', () => {
  let queries = 0;
  const ui = setup(async () => { queries++; return success(result()); });
  ui.run('$("#action").value = "metric"');
  ui.window.dispatchEvent(new ui.CustomEvent('globerendererready', { bubbles: false }));
  ui.window.dispatchEvent(new ui.CustomEvent('airportdraftselect', { detail: { code: 'SFO' }, bubbles: false }));
  assert.equal(ui.run('$("#airports").value'), 'SFO');
  const changes = ui.events.filter(event => event.type === 'airportdraftchange');
  assert.equal(changes.length, 2); assert.equal(changes[1].bubbles, false);
  assert.equal(changes[1].detail.airports[0], 'SFO'); assert.equal(queries, 0);
});

test('renderer readiness replays the latest draft and previous result exactly once', () => {
  const ui = setup(async () => success(result()));
  ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); resultIsPrevious = true; $("#airports").value = "BOS, PVD"; changedDraft()');
  assert.equal(ui.events.filter(event => ['airportdraftchange', 'analysisresultchange'].includes(event.type)).length, 0,
    'state is retained while the renderer is loading');
  ui.window.dispatchEvent(new ui.CustomEvent('globerendererready', { bubbles: false }));
  const draft = ui.events.filter(event => event.type === 'airportdraftchange');
  const resultState = ui.events.filter(event => event.type === 'analysisresultchange');
  assert.equal(draft.length, 1); assert.equal(resultState.length, 1);
  assert.equal(draft[0].bubbles, false); assert.equal(resultState[0].bubbles, false);
  assert.deepEqual(Array.from(draft[0].detail.airports), ['BOS', 'PVD']);
  assert.deepEqual(Array.from(resultState[0].detail.airports), ['LAX']);
  assert.equal(resultState[0].detail.previous, true);
  assert.equal(resultState[0].detail.airportDetails, undefined, 'result metric/status details are not duplicated over the Earth');
  ui.window.dispatchEvent(new ui.CustomEvent('globerendererready', { bubbles: false }));
  assert.equal(ui.events.filter(event => event.type === 'airportdraftchange').length, 1);
  assert.equal(ui.events.filter(event => event.type === 'analysisresultchange').length, 1);
});

test('globe result state publishes scope and focus without redundant metric/status overlays', () => {
  const ui = setup(async () => success(result()));
  const payload = result(); payload.rows[0].metrics = [];
  ui.context.payload = payload;
  ui.run('latestSuccessfulResult = validateResult(payload); rendererReady = true; publishResultState()');
  const event = ui.events.filter(event => event.type === 'analysisresultchange').at(-1).detail;
  assert.deepEqual(Array.from(event.airports), ['LAX']);
  assert.equal(event.airportDetails, undefined);
  assert.deepEqual(Array.from(event.focusAirports), ['LAX']);
});

test('regional screening focuses BOS/PVD context while marker highlights remain returned rows only', () => {
  const ui = setup(async () => success(result()));
  const payload = result(); payload.scope.metric = 'screen_score';
  payload.scope.airports = ['ANC', 'LAX', 'SNA', 'SFO', 'BOS', 'PVD', 'BDL'];
  payload.rows = ['BOS', 'PVD'].map((airport, index) => ({ airport, rank: index + 1, metrics: [
    { key: 'screen_score', value: 88 - index, unit: 'score', status: 'ok', source_ids: ['source'] },
  ] }));
  ui.context.payload = payload;
  ui.run('latestSuccessfulResult = validateResult(payload); rendererReady = true; publishResultState()');
  const event = ui.events.filter(item => item.type === 'analysisresultchange').at(-1).detail;
  assert.deepEqual(Array.from(event.airports), ['BOS', 'PVD']);
  assert.deepEqual(Array.from(event.focusAirports), ['BOS', 'PVD']);
  payload.scope.metric = 'passenger_growth';
  ui.run('publishResultState()');
  const growthEvent = ui.events.filter(item => item.type === 'analysisresultchange').at(-1).detail;
  assert.deepEqual(Array.from(growthEvent.focusAirports), ['BOS', 'PVD'], 'any regional ranked measure uses the same truthful geographic context');
});

test('preset edits publish the new airports before its request and retain the older result as previous', async () => {
  let resolveRequest;
  const ui = setup(() => new Promise(resolve => { resolveRequest = resolve; }));
  ui.context.payload = result();
  ui.run('latestSuccessfulResult = validateResult(payload); contextResultId = payload.result_id');
  ui.window.dispatchEvent(new ui.CustomEvent('globerendererready', { bubbles: false }));
  const request = ui.run('runPreset(demos["lax-sna"])');
  const drafts = ui.events.filter(event => event.type === 'airportdraftchange');
  const resultStates = ui.events.filter(event => event.type === 'analysisresultchange');
  assert.deepEqual(Array.from(drafts.at(-1).detail.airports), ['LAX', 'SNA']);
  assert.equal(resultStates.at(-1).detail.previous, true);
  assert.ok(content(ui.nodes.get('#result-title')).includes('Previous result'));
  assert.ok(resolveRequest, 'one request was initiated');
  resolveRequest(success(result()));
  await request;
  assert.equal(ui.events.filter(event => event.type === 'analysisresultchange').at(-1).detail.previous, false);
});

test('final UI markup keeps the local renderer, mapped airports and honest data labels', () => {
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  const css = fs.readFileSync(path.join(__dirname, '../app/static/styles.css'), 'utf8');
  assert.equal((html.match(/<h1\b/g) || []).length, 1);
  assert.equal((html.match(/id="feedback"/g) || []).length, 1);
  assert.match(html, /<script type="module" src="\/static\/globe\.js"><\/script>/);
  assert.ok(fs.existsSync(path.join(__dirname, '../app/static/globe.js')));
  const coordinates = JSON.parse(fs.readFileSync(path.join(__dirname, '../app/static/assets/airport-coordinates.json'), 'utf8'));
  assert.deepEqual(Object.keys(coordinates).sort(), ['ANC', 'BOS', 'LAX', 'PVD', 'SFO', 'SNA']);
  for (const asset of ['earth-day-2048.webp', 'earth-night-2048.webp', 'earth-clouds-2048.webp', 'earth-poster.webp', 'airport-coordinates.json']) {
    assert.ok(fs.existsSync(path.join(__dirname, `../app/static/assets/${asset}`)), asset);
  }
  assert.match(css, /touch-action: pan-y/);
  const globeBackgroundRule = [...css.matchAll(/\.earth-stage\s*\{[^}]*\}/g)].map(match => match[0]).find(rule => /position:\s*fixed/.test(rule) && /inset:\s*var\(--header-h\)\s+0\s+0/.test(rule)) || '';
  assert.match(globeBackgroundRule, /position:\s*fixed/);
  assert.match(css, /--header-h:\s*48px\s*;/, 'the shared header token defines a 48px header');
  assert.match(globeBackgroundRule, /inset:\s*var\(--header-h\)\s+0\s+0/);
  assert.match(css, /\.globe-motion-toggle\s*\{[^}]*width:\s*44px;[^}]*min-height:\s*44px;/, 'the motion button keeps a 44px minimum hit area');
  assert.match(css, /\.more-examples\s*>\s*summary\s*\{[^}]*min-height:\s*44px;/, 'More analyses summary keeps a 44px minimum hit area');
  assert.match(css, /#scope-panel\s*>\s*summary\s*\{[^}]*min-height:\s*44px;/, 'Adjust scope summary keeps a 44px minimum hit area');
  assert.match(css, /#analyze\s+\.more-examples\s+\.prompt\s*\{[^}]*min-height:\s*44px;[^}]*width:\s*auto;[^}]*border-radius:\s*999px;/, 'secondary prompts retain compact chip styles and a 44px hit area with specificity above the primary prompt rule');
  assert.match(css, /#analyze\s+\.more-examples\s+\.prompt\s+strong\s*\{[^}]*font-size:\s*\.72rem;/, 'secondary prompt labels keep compact chip typography');
  assert.match(css, /#result\s+\.comparison-dumbbell\s*\{[^}]*width:\s*100%;/);
  assert.match(css, /#result\s+\.share-ring-value\s*\{[^}]*stroke:\s*var\(--cyan\);/);
  assert.match(css, /#result\s+\.comparison-label\s*\{[^}]*font-size:\s*var\(--space-3\);/);
  assert.match(css, /#result\s+\.dumbbell-axis\s*\{[^}]*stroke-width:\s*calc\(var\(--space-2\) \/ 4\);/);
  assert.match(css, /\.series-line\s*\{[^}]*stroke-width:\s*calc\(var\(--space-1\) \* \.625\);/);
  assert.match(css, /series-axis-label\s*\{[^}]*font-variant-numeric:\s*tabular-nums;/);
  assert.match(css, /#result-panel\s*\{\s*position:\s*absolute;\s*z-index:\s*2;/);
  assert.match(css, /\.earth-stage\s*\{\s*overflow:\s*visible;/, 'wide layouts let the full globe extend behind foreground content');
  assert.doesNotMatch(html, /latest live flights|real-time flight data|investment score|market totals/i);
});

test('congestion graph preserves signed, zero and airport-specific backend values', () => {
  const keys = ['cancellation_rate', 'diversion_rate', 'departure_delay_minutes', 'taxi_out_minutes'];
  const build = (leftDelay, rightDelay) => {
    const payload = result(); payload.scope.metric = 'congestion'; payload.scope.airports = ['LAX', 'SNA']; payload.status = 'ok';
    payload.rows = ['LAX', 'SNA'].map((airport, airportIndex) => ({ airport, metrics: keys.map((key, keyIndex) => {
      const ratio = keyIndex < 2;
      const value = keyIndex === 2 ? (airportIndex ? rightDelay : leftDelay) : 2 + airportIndex;
      return { key, value, unit: ratio ? 'percent' : 'minutes', status: 'ok',
        numerator: ratio ? value : null, denominator: ratio ? 100 : null, eligible_count: ratio ? 100 : null,
        comparison_direction: 'higher', source_ids: ['source'] };
    }) }));
    return payload;
  };
  const render = payload => { const ui = setup(async () => success(payload)); ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)'); return ui; };
  const mixed = resultContent(render(build(-10, 20)));
  assert.match(mixed, /LAX/); assert.match(mixed, /SNA/);
  assert.match(mixed, /-10/); assert.match(mixed, /20/);
  const negative = resultContent(render(build(-10, -5)));
  assert.match(negative, /-10/); assert.match(negative, /-5/);
  const zero = resultContent(render(build(0, 0)));
  assert.match(zero, /0\.00/);
});

test('congestion graph labels unavailable and missing values without inventing zeroes', () => {
  const payload = result(); payload.scope.metric = 'congestion'; payload.scope.airports = ['LAX', 'SNA']; payload.status = 'partial';
  const pairs = ['LAX', 'SNA'].map(airport => ({ airport, metrics: [
    { key: 'departure_delay_minutes', value: null, unit: 'minutes', status: 'unavailable', reason: 'Coverage incomplete', source_ids: ['source'] },
  ] }));
  pairs[1].metrics = [];
  payload.rows = pairs;
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  assert.match(text, /Unavailable: Coverage incomplete/);
  assert.match(text, /Not returned/);
  assert.doesNotMatch(text, /0\.00 min/);
});

test('screening graph keeps airport identities, backend ranks and unavailable states', () => {
  const payload = result(); payload.scope.metric = 'screen_score'; payload.scope.airports = ['BOS', 'BDL', 'BGR', 'ACK', 'HYA', 'MHT']; payload.status = 'partial';
  const score = (value, reason) => ({ key: 'screen_score', value, unit: 'score', status: reason ? 'unavailable' : 'ok', reason: reason || null, source_ids: ['source'] });
  const support = { key: 'passengers', value: 123, unit: 'count', status: 'ok', source_ids: ['source'] };
  payload.rows = [
    { airport: 'BOS', rank: 2, metrics: [score(74), support] },
    { airport: 'BDL', rank: 1, metrics: [score(90), support] },
    { airport: 'BGR', rank: 3, metrics: [score(0), support] },
    { airport: 'ACK', rank: 4, metrics: [score(88), support] },
    { airport: 'HYA', rank: 5, metrics: [score(null, 'Incomplete history')] },
    { airport: 'MHT', rank: null, metrics: [score(50)] },
  ];
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  for (const code of ['BOS', 'BDL', 'BGR', 'ACK', 'HYA', 'MHT']) assert.ok(text.includes(code), `${code} is retained`);
  assert.match(text, /Backend rank|rank/i);
  assert.match(text, /Incomplete history/);
  assert.match(text, /— MHT/, 'available values without backend rank stay visibly unranked');
  assert.match(text, /Source IDs: source/);
});

test('mixed-unit series retains each supplied unit and unavailable reason', () => {
  const payload = result(); payload.scope.metric = 'sfo_enplaned_trend'; payload.scope.airports = ['SFO'];
  payload.rows = [{ airport: 'SFO', metrics: [{ key: 'sfo_enplaned_trend', value: 10, unit: 'count', status: 'ok', source_ids: ['source'] }] }];
  payload.series = [
    { period: '202401', value: 10, unit: 'count', status: 'ok' },
    { period: '202402', value: null, unit: 'count', status: 'unavailable', reason: 'Source coverage gap' },
    { period: '202403', value: 2, unit: 'minutes', status: 'ok' },
  ];
  const ui = setup(async () => success(payload)); ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  assert.match(text, /2024-01/);
  assert.match(text, /10/);
  assert.match(text, /Unavailable: Source coverage gap/);
  assert.match(text, /minutes|min/);
});

test('result rendering, local disclosures and navigation never add a request; one submission is one query post', async () => {
  const calls = [];
  const ui = setup(async (url, options) => { calls.push({ url, method: options?.method }); return success(result()); });
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.deepEqual(calls.filter(call => call.url === '/api/query'), [{ url: '/api/query', method: 'POST' }]);
  const payload = ui.run('latestSuccessfulResult');
  ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  for (const disclosure of ui.nodes.get('#result').children.filter(node => node.tagName === 'details')) disclosure.open = true;
  ui.nodes.get('#evidence-link').click();
  ui.nodes.get('#fresh').click();
  assert.equal(calls.filter(call => call.url === '/api/query').length, 1, 'rendering, expanding and local navigation stay local');
  assert.ok(calls.every(call => call.url === '/health' || call.url === '/api/query'));
});

test('evidence rendering keeps resolved source names and unknown reference identifiers', () => {
  const payload = result(); payload.evidence = [
    { source_id: 'source', date: '2024-02', locator: 'page 3', claim: 'Resolved claim', limitation: 'Resolved caveat' },
    { source_id: 'unresolved-id', date: '2024-03', locator: 'table 4', claim: 'Fallback claim', limitation: 'Fallback caveat' },
  ];
  payload.rows[0].metrics[0].source_ids = [];
  const ui = setup(async () => success(payload)); ui.context.payload = payload;
  ui.run('renderResult(validateResult(payload), false)');
  const text = resultContent(ui);
  assert.match(text, /BTS · 2024-02/);
  assert.match(text, /Reviewed source · 2024-03/);
  assert.match(text, /Source ID: unresolved-id/);
  assert.match(text, /Fallback claim/);
});

const NE_2025 = 'ACK AUG BDL BGR BHB BID BOS BTV EWB HVN HYA LEB MHT MVY ORH PQI PSM PVC PVD PWM RKD RUT WST'.split(' ');
function bundleResult() {
  const sources = [{ id: 't100', name: 'BTS T-100', url: 'https://example.test', snapshot_id: 'snap', period: '2024-2025', retrieved_at: null }];
  return { result_id: 'result-2025', request_id: 'request-2025', status: 'ok',
    scope: { airports: NE_2025.slice(), year: 2025, bundle_id: 'annual-2025-r1', baseline_year: 2024, comparison_year: 2025,
      metric: 'screen_score', threshold_miles: null, population: 'New England airports normalized against the frozen eligible 2025 cohort' },
    summary: 'Screening summary', rows: NE_2025.map((airport, index) => ({ airport, rank: index + 1,
      metrics: [{ key: 'screen_score', value: 100 - index, unit: 'score', status: 'ok', source_ids: ['t100'] }] })),
    series: [{ period: '202501', value: 5, unit: 'count', status: 'ok' }], sources, evidence: [], exclusions: [], limitations: [] };
}
const errorEnvelope = (code, message) => ({ success: false, error: { code, message, request_id: '123e4567-e89b-42d3-a456-426614174000' } });

test('2025 bundle result with 23 New England rows including EWB is admitted and shows its period', () => {
  const ui = setup(async () => success(bundleResult()));
  ui.context.payload = bundleResult();
  const admitted = ui.run('validateResult(payload)');
  assert.equal(admitted.rows.length, 23);
  assert.ok(admitted.rows.some(row => row.airport === 'EWB'));
  ui.run('renderResult(validateResult(payload), false)');
  const scopeHeading = ui.nodes.get('#result').children[0];
  const period = scopeHeading.children.find(child => child.className === 'result-period');
  assert.equal(period.textContent, 'Period · CY2024 → CY2025 · showing 2025 · Bundle annual-2025-r1');
  assert.ok(ui.run('supportedAirports.has("EWB") && newEnglandAirports.has("EWB")'));
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  assert.match(html, /<option>EWB<\/option>/);
  assert.match(html, /<option value="2025" selected>2025 \(vs 2024, accepted bundle\)<\/option>/);
  assert.match(html, /CY2024→CY2025 accepted bundle; 2023–2024 historical/);
  assert.doesNotMatch(html, /2023–2024 historical coverage ·/);
});

test('historical result without bundle fields shows a historical period and stays admitted', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('renderResult(validateResult(payload), false)');
  const scopeHeading = ui.nodes.get('#result').children[0];
  assert.equal(scopeHeading.children.find(child => child.className === 'result-period').textContent, 'Period · 2024 historical data');
  ui.context.nulls = result();
  Object.assign(ui.context.nulls.scope, { bundle_id: null, baseline_year: null, comparison_year: null });
  assert.doesNotThrow(() => ui.run('validateResult(nulls)'));
});

test('result admission enforces resolved bundle scope invariants', () => {
  const invalid = [
    ['2025 without bundle', value => { delete value.scope.bundle_id; delete value.scope.baseline_year; delete value.scope.comparison_year; }],
    ['partial bundle fields', value => { value.scope.baseline_year = null; }],
    ['bundle id wrong type', value => { value.scope.bundle_id = 7; }],
    ['bundle id bad pattern', value => { value.scope.bundle_id = '../x'; }],
    ['baseline not before comparison', value => { value.scope.baseline_year = 2025; }],
    ['year outside bundle', value => { value.scope.year = 2023; }],
    ['fractional baseline', value => { value.scope.baseline_year = 2024.5; }],
    ['unsupported year', value => { value.scope.year = 2026; value.scope.comparison_year = 2026; }],
    ['24 rows', value => { value.scope.airports.push('ANC'); value.rows.push({ airport: 'ANC', rank: null, metrics: value.rows[0].metrics }); }],
  ];
  for (const [name, mutate] of invalid) {
    const ui = setup();
    ui.context.fixture = bundleResult(); mutate(ui.context.fixture);
    assert.throws(() => ui.run('validateResult(fixture)'), undefined, name);
  }
  const ui = setup();
  ui.context.fixture = bundleResult(); ui.context.fixture.scope.year = 2024;
  assert.doesNotThrow(() => ui.run('validateResult(fixture)'), 'baseline year inside a bundle is valid');
});

test('all presets omit year so the server default period applies', async () => {
  const bodies = [];
  const ui = setup(async (_url, options) => { bodies.push(JSON.parse(options.body)); return success(bundleResult()); });
  const names = Array.from(ui.run('Object.keys(demos)'));
  assert.deepEqual(names, ['new-england', 'lax-sna', 'anc-long-haul', 'sfo-pressure', 'sfo-trend', 'bos-pvd', 'growth']);
  for (const name of names) {
    ui.context.name = name;
    await ui.run('runPreset(demos[name])');
    assert.equal(ui.nodes.get('#year').value, '2025', `${name}: year selector shows the default period`);
  }
  assert.equal(bodies.length, names.length);
  for (const body of bodies) {
    assert.ok(!('year' in body.analysis), JSON.stringify(body));
    assert.ok(!('bundle_id' in body.analysis), JSON.stringify(body));
  }
});

test('scope form omits year for 2025 and sends explicit historical years', async () => {
  const bodies = [];
  const ui = setup(async (_url, options) => { bodies.push(JSON.parse(options.body)); return success(result()); });
  for (const [year, expected] of [['2025', undefined], ['2024', 2024], ['2023', 2023]]) {
    ui.nodes.get('#action').value = 'metric';
    ui.nodes.get('#airports').value = 'PVD';
    ui.nodes.get('#metric').value = 'passengers';
    ui.nodes.get('#year').value = year;
    ui.nodes.get('#scope-form').listeners.submit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(bodies.at(-1).analysis.year, expected, year);
  }
  assert.equal(bodies.length, 3);
});

test('year validation mirrors the contract for 2025, 2024 and 2023', () => {
  const ui = setup(async () => { throw new Error('validation only'); });
  const valid = [
    { action: 'rank', region: 'new_england', metric: 'screen_score' },
    { action: 'rank', region: 'new_england', metric: 'screen_score', year: 2025 },
    { action: 'rank', airports: ['EWB', 'BOS'], metric: 'passenger_growth', year: 2025 },
    { action: 'compare', airports: ['LAX', 'SNA'], metric: 'congestion', year: 2025 },
    { action: 'compare', airports: ['LAX', 'SNA'], metric: 'congestion', year: 2024 },
    { action: 'metric', airports: ['SFO'], metric: 'sfo_pressure', year: 2025 },
    { action: 'metric', airports: ['SFO'], metric: 'sfo_enplaned_trend', year: 2024 },
    { action: 'metric', airports: ['ANC'], metric: 'long_haul_share', year: 2023, threshold_miles: 3000 },
    { action: 'compare', airports: ['BOS', 'PVD'], metric: 'passenger_growth', year: 2024 },
  ];
  for (const analysis of valid) {
    ui.context.analysis = analysis;
    assert.equal(ui.run('validateAnalysisScope(analysis).length'), 0, JSON.stringify(analysis));
  }
  const invalid = [
    { action: 'rank', region: 'new_england', metric: 'screen_score', year: 2023 },
    { action: 'compare', airports: ['BOS', 'PVD'], metric: 'passenger_growth', year: 2023 },
    { action: 'compare', airports: ['LAX', 'SNA'], metric: 'congestion', year: 2023 },
    { action: 'metric', airports: ['LAX'], metric: 'taxi_out_minutes', year: 2023 },
    { action: 'metric', airports: ['SFO'], metric: 'sfo_pressure', year: 2023 },
    { action: 'metric', airports: ['PVD'], metric: 'passengers', year: 2026 },
    { action: 'metric', airports: ['PVD'], metric: 'passengers', year: 2022 },
  ];
  for (const analysis of invalid) {
    ui.context.analysis = analysis;
    const errors = Array.from(ui.run('validateAnalysisScope(analysis)'));
    assert.ok(errors.some(error => error.field === 'year'), JSON.stringify(analysis));
  }
});

test('access_required redirects to the sign-in page without retrying', async () => {
  const assigned = [];
  let calls = 0;
  const ui = setup(async () => { calls++; return { ok: false, status: 401, json: async () => errorEnvelope('access_required', 'Sign in to continue.') }; });
  ui.window.location = { assign: url => assigned.push(url) };
  await ui.run('runPreset(demos["new-england"])');
  assert.equal(calls, 1);
  assert.deepEqual(assigned, ['/login']);
  assert.equal(ui.run('busy'), false);
  ui.context.envelope = errorEnvelope('access_denied', 'Access code not recognised.');
  assert.equal(ui.run('parseErrorResponse(envelope)').code, 'access_denied');
});

test('budget_exhausted is no longer a declared error code', () => {
  const ui = setup();
  ui.context.envelope = errorEnvelope('budget_exhausted', 'Budget exhausted.');
  assert.equal(ui.run('parseErrorResponse(envelope)'), null);
  assert.equal(ui.run('errorCodes.has("budget_exhausted")'), false);
  assert.ok(ui.run('errorCodes.has("access_required")'));
});

test('ai_unavailable keeps the server message and says presets still work', async () => {
  const ui = setup(async () => ({ ok: false, status: 503, json: async () => errorEnvelope('ai_unavailable', 'AI interpretation is unavailable.') }));
  ui.nodes.get('#question').value = 'Which airport is busiest?';
  ui.nodes.get('#chat-form').listeners.submit({ preventDefault() {} });
  await new Promise(resolve => setImmediate(resolve));
  const text = ui.nodes.get('#feedback').textContent;
  assert.match(text, /^AI interpretation is unavailable\. /);
  assert.match(text, /Presets and Adjust scope still work/);
});

test('login page is self-contained, labeled and posts the access code as JSON', () => {
  const html = fs.readFileSync(path.join(__dirname, '../app/login.html'), 'utf8');
  assert.doesNotMatch(html, /\son[a-z]+=/i, 'no inline event-handler attributes');
  assert.doesNotMatch(html, /<(?:link|img)\b|\bsrc=|https?:\/\//i, 'no external assets');
  assert.match(html, /<label for="code">Access code<\/label>/);
  assert.match(html, /<input id="code"[^>]*type="password"[^>]*autocomplete="current-password"[^>]*maxlength="256"/);
  assert.match(html, /id="login-error" role="alert"/);
  assert.match(html, /fetch\("\/api\/access", \{\s*method: "POST",\s*headers: \{ "Content-Type": "application\/json" \},\s*credentials: "same-origin",\s*body: JSON\.stringify\(\{ code \}\)/);
  assert.match(html, /response\.status === 204\) \{ window\.location\.assign\("\/"\)/);
  assert.match(html, /response\.status === 401\) showError\("Access code not recognised\."\)/);
});
