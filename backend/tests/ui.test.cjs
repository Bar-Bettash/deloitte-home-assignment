// Node's built-in test runner exercises the real browser script with a minimal DOM.
// These are contract/state tests, not a browser or accessibility audit.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor() { this.children = []; this.attrs = {}; this.hidden = false; this.value = ''; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this.attrs[key] = value; }
  addEventListener() {}
}
function setup(query) {
  const nodes = new Map();
  const context = vm.createContext({
    document: {
      querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); },
      querySelectorAll() { return []; }, createElement() { return new Element(); },
    },
    fetch: async (url, options) => url === '/health' ? { ok: true, json: async () => ({ status: 'ok' }) } : query(url, options),
    setTimeout, clearTimeout, AbortController, URL, Intl, console,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static/app.js'), 'utf8'), context);
  return { context, nodes, run: (code) => vm.runInContext(code, context) };
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
const success = (payload) => ({ ok: true, json: async () => payload });

test('chat helper accurately states that free-text interpretation is disabled', () => {
  const html = fs.readFileSync(path.join(__dirname, '../app/static/index.html'), 'utf8');
  const helper = html.match(/<p id="question-help"[^>]*>([^<]*)<\/p>/);
  assert.ok(helper, 'chat helper must be present');
  assert.equal(helper[1], 'Structured examples, scope controls, and Explain work now. Free-text interpretation is not enabled.');
});

test('renders rows, rank, backend percentages, denominator, directions, evidence, series and limitations', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('renderResult(validateResult(payload), false)');
  const text = content(ui.nodes.get('#result'));
  for (const expected of ['Partial result', '2.50%', '1 / 40', 'higher', 'Unavailable', 'Counterevidence retained', 'PVC incomplete', 'Not a causal conclusion', '<b>Plain evidence</b>']) assert.ok(text.includes(expected), expected);
  assert.ok(!text.includes('250.00%'));
});

test('partial metric is explicitly unavailable rather than zero', () => {
  const ui = setup(); const payload = result();
  Object.assign(payload.rows[0].metrics[0], { value: null, status: 'unavailable', reason: 'Missing month' });
  ui.context.payload = payload; ui.run('renderResult(validateResult(payload), false)');
  assert.ok(content(ui.nodes.get('#result')).includes('Unavailable: Missing month'));
});

test('evidence claim and counterevidence are visible without expanding a closed panel', () => {
  const ui = setup(); ui.context.payload = result();
  ui.run('renderResult(validateResult(payload), false)');
  const evidencePanel = ui.nodes.get('#result').children.find(node =>
    node.children.some(child => child.textContent === 'source · 2024'));
  assert.ok(evidencePanel, 'evidence source/date panel exists');
  assert.equal(evidencePanel.open, true, 'claim and limitation must be visible on initial display');
  assert.ok(evidencePanel.children.some(node => node.textContent === 'Evidence: <b>Plain evidence</b>'));
  assert.ok(evidencePanel.children.some(node => node.textContent === 'Locator: page 2'));
  assert.ok(evidencePanel.children.some(node => node.textContent === 'Limitation / counterevidence: Counterevidence retained'));
});

test('rejects nonfinite values, unsafe URLs and unresolved source references', () => {
  const ui = setup();
  for (const mutate of [p => p.rows[0].metrics[0].value = NaN, p => p.sources[0].url = 'javascript:alert(1)', p => p.rows[0].metrics[0].source_ids = ['missing']]) {
    const payload = result(); mutate(payload); ui.context.payload = payload;
    assert.throws(() => ui.run('validateResult(payload)'));
  }
});

test('HTTP error after success preserves Previous result and does not retry', async () => {
  let calls = 0;
  const ui = setup(async () => ++calls === 1 ? success(result()) : { ok: false, status: 409, json: async () => ({ error: { message: 'Stale result', request_id: 'error-id' } }) });
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(calls, 2);
  assert.ok(content(ui.nodes.get('#result')).includes('Previous result'));
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

test('duplicate send is suppressed and non-JSON errors preserve the previous result', async () => {
  let calls = 0, resolve;
  const ui = setup(() => { calls++; return new Promise(done => { resolve = done; }); });
  const pending = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  await ui.run('submitRequest({analysis: demos["lax-sna"]})');
  assert.equal(calls, 1); resolve(success(result())); await pending;
  const failure = ui.run('submitRequest({analysis: demos["lax-sna"]})');
  resolve({ ok: true, json: async () => { throw new SyntaxError('HTML'); } }); await failure;
  assert.ok(content(ui.nodes.get('#result')).includes('Previous result'));
  assert.equal(ui.nodes.get('#feedback').attrs.role, 'alert');
});
