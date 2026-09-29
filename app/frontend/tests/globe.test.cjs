const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../globe.js'), 'utf8');

function harness({ context = true, throwContext = false, autoLoad = true, shaderOk = true, failUploadAt = 0, rectWidth = 1200, rectHeight = 600, innerWidth = 1440, finePointer = true, reducedMotion = false, hidden = false } = {}) {
  let activeRectWidth = rectWidth, activeRectHeight = rectHeight;
  const imageInstances = [];
  const timers = new Map();
  const windowListeners = new Map();
  const canvasListeners = new Map();
  const documentListeners = new Map();
  const frames = new Map();
  let frameId = 0;
  let now = 0;
  let activeElement = null;
  const calls = { draws: [], buffers: [], shaderSources: [], uploads: 0, deletedTextures: 0, deletedBuffers: 0, deletedPrograms: 0, urls: [], events: [], contextOptions: null };
  let timerId = 0;
  const gl = {
    VERTEX_SHADER: 1, FRAGMENT_SHADER: 2, COMPILE_STATUS: 3, LINK_STATUS: 4,
    TEXTURE0: 10, TEXTURE_2D: 11, TEXTURE_MIN_FILTER: 12, TEXTURE_MAG_FILTER: 13,
    TEXTURE_WRAP_S: 14, TEXTURE_WRAP_T: 15, LINEAR: 16, CLAMP_TO_EDGE: 17,
    RGB: 18, UNSIGNED_BYTE: 19, ARRAY_BUFFER: 20, STATIC_DRAW: 21, FLOAT: 22,
    COLOR_BUFFER_BIT: 23, TRIANGLES: 24, REPEAT: 25,
    createShader: (type) => ({ type }), shaderSource: (shader, text) => { shader.source = text; calls.shaderSources.push(text); },
    compileShader: (shader) => { shader.ok = shaderOk; }, getShaderParameter: (s) => s.ok,
    getShaderInfoLog: () => 'mock shader error', deleteShader() {},
    createProgram: () => ({}), attachShader() {}, linkProgram() {}, getProgramParameter: () => true,
    getProgramInfoLog: () => '', deleteProgram: () => { calls.deletedPrograms += 1; },
    createBuffer: () => ({}), bindBuffer() {}, bufferData: (_target, data) => calls.buffers.push(data.length),
    createTexture: () => ({}), activeTexture() {}, bindTexture() {}, texParameteri() {},
    texImage2D: () => { calls.uploads += 1; if (calls.uploads === failUploadAt) throw new Error('mock upload failed'); }, deleteTexture: () => { calls.deletedTextures += 1; },
    deleteBuffer: () => { calls.deletedBuffers += 1; },
    getUniformLocation: (_p, name) => name, getAttribLocation: () => 0,
    enableVertexAttribArray() {}, vertexAttribPointer() {}, uniform2f: (name, x, y) => calls[name] = [x, y],
    uniform1i() {}, uniform1f: (name, value) => calls[name] = value, viewport() {}, clearColor() {}, clear() {}, useProgram() {},
    drawArrays: (mode, first, count) => calls.draws.push({ mode, first, count }),
  };
  const canvas = {
    width: 0, height: 0, clientHeight: rectHeight, hidden: false, dataset: {}, attributes: {},
    getContext: (_type, options) => { calls.contextOptions = options; if (throwContext) throw new Error('context denied'); return context ? gl : null; },
    getBoundingClientRect: () => ({ left: 0, top: 0, right: activeRectWidth, bottom: activeRectHeight, width: activeRectWidth, height: activeRectHeight }),
    setAttribute: (name, value) => { canvas.attributes[name] = value; },
    focus: () => { activeElement = canvas; },
    setPointerCapture() {}, addEventListener: (type, fn) => canvasListeners.set(type, fn),
    removeEventListener: (type) => canvasListeners.delete(type),
  };
  const element = () => {
    const listeners = new Map();
    const classes = new Set();
    const node = {
      dataset: {}, style: {}, hidden: false, children: [], attributes: {}, textContent: '',
      classList: { toggle: (name, enabled) => enabled ? classes.add(name) : classes.delete(name), contains: (name) => classes.has(name) },
      setAttribute: (name, value) => { node.attributes[name] = value; },
      focus: () => { activeElement = node; listeners.get('focus')?.({ target: node }); }, blur: () => { if (activeElement === node) { activeElement = null; listeners.get('blur')?.({ target: node }); } },
      getBoundingClientRect: () => node._rect || { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 },
      addEventListener: (type, fn) => listeners.set(type, fn),
      removeEventListener: (type) => listeners.delete(type),
      append: (...children) => node.children.push(...children),
      replaceChildren: (...children) => { node.children = children; },
      querySelector: (selector) => selector === '[data-motion-icon]' ? node.children.find((child) => child.dataset.motionIcon !== undefined) || null : selector === '.marker-label' ? node.children[0] : null,
      _listeners: listeners, _classes: classes,
    };
    Object.defineProperty(node, 'className', { set(value) { for (const name of value.split(/\s+/)) if (name) classes.add(name); } });
    return node;
  };
  const shortcutNodes = ['ANC', 'LAX', 'SNA', 'SFO', 'BOS', 'PVD'].map((code) => { const node = element(); node.dataset.code = code; return node; });
  const markerHost = element(), zoomControls = element();
  const zoomIn = element(), zoomOut = element(), zoomReset = element(), zoomStatus = { textContent: '' };
  const motionToggle = element();
  motionToggle.tagName = 'BUTTON';
  const motionIcon = element();
  motionIcon.dataset.motionIcon = '';
  motionToggle.append(motionIcon);
  const created = [];
  const poster = { hidden: false };
  const status = { textContent: '' };
  const document = {
    readyState: 'complete', querySelector: (selector) => ({
      '#earth-canvas': canvas, '#earth-poster': poster, '#globe-status': status, '#globe-markers': markerHost,
      '#globe-zoom-in': zoomIn, '#globe-zoom-out': zoomOut, '#globe-reset': zoomReset, '#globe-zoom-status': zoomStatus,
      '#globe-zoom-controls': zoomControls, '#globe-motion-toggle': motionToggle,
    }[selector] || null),
    querySelectorAll: (selector) => selector === '#globe-shortcuts [data-code]' ? shortcutNodes : [],
    createElement: (tag) => { const node = element(); node.tagName = tag; created.push(node); return node; },
    get activeElement() { return activeElement; },
    get hidden() { return hidden; },
    addEventListener: (type, fn) => documentListeners.set(type, fn),
    removeEventListener: (type) => documentListeners.delete(type),
  };
  const window = {
    devicePixelRatio: 2,
    innerWidth,
    matchMedia: (query) => ({ matches: query.includes('prefers-reduced-motion') ? reducedMotion : finePointer }),
    addEventListener: (type, fn) => windowListeners.set(type, fn),
    removeEventListener: (type) => windowListeners.delete(type),
    dispatchEvent: (event) => { calls.events.push(event); calls.readyEvents = (calls.readyEvents || 0) + (event.type === 'globerendererready' ? 1 : 0); return true; },
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms, at: now + ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    requestAnimationFrame: (fn) => { const id = ++frameId; frames.set(id, fn); return id; },
    cancelAnimationFrame: (id) => frames.delete(id),
  };
  class ImageMock {
    constructor() { imageInstances.push(this); }
    set src(value) {
      this._src = value;
      calls.urls.push(value);
      if (autoLoad) Promise.resolve().then(() => this.onload && this.onload());
    }
    get src() { return this._src; }
  }
  class CustomEventMock { constructor(type, options) { this.type = type; Object.assign(this, options); } }
  const coordinates = {
    ANC: { lat: 61.17408472, lon: -149.9981375 }, LAX: { lat: 33.94249638, lon: -118.40804861 },
    SNA: { lat: 33.67566194, lon: -117.86823305 }, SFO: { lat: 37.61880555, lon: -122.37541666 },
    BOS: { lat: 42.36294444, lon: -71.00638888 }, PVD: { lat: 41.72233333, lon: -71.42772222 },
  };
  const fetch = async (url, options) => ({ ok: true, json: async () => coordinates, url, options });
  const performance = { now: () => now };
  const sandbox = { window, document, Image: ImageMock, CustomEvent: CustomEventMock, Promise, Float32Array, Math, Error, fetch, performance };
  vm.runInNewContext(source, sandbox, { filename: 'globe.js' });
  return { canvas, poster, status, zoomIn, zoomOut, zoomReset, zoomStatus, zoomControls, motionToggle, motionIcon, gl, calls, timers, frames, imageInstances, windowListeners, canvasListeners, documentListeners, shortcutNodes, markerHost, created, window, document,
    resizeCanvas(width, height) { activeRectWidth = width; activeRectHeight = height; },
    advance(ms) { now += ms; return now; },
    elapse(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } return now; },
    frame() { const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback(now); },
    setHidden(value) { hidden = value; documentListeners.get('visibilitychange')?.(); },
  };
}

async function settle(h) {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  for (const [id, timer] of [...h.timers]) {
    if (timer.ms === 0) { h.timers.delete(id); timer.fn(); }
  }
}

test('WebGL draws the local Earth layers, caps DPR, and schedules one idle frame', async () => {
  const h = harness();
  await settle(h);
  assert.equal(h.canvas.dataset.state, 'ready');
  assert.equal(h.poster.hidden, true);
  assert.deepEqual(h.calls.urls, ['/static/assets/earth-day-2048.webp', '/static/assets/earth-night-2048.webp', '/static/assets/earth-clouds-2048.webp']);
  assert.deepEqual(h.calls.buffers, [12]);
  assert.equal(h.calls.uploads, 3);
  assert.deepEqual(h.calls.draws, [{ mode: 24, first: 0, count: 6 }]);
  assert.equal(h.canvas.width, 1800);
  assert.equal(h.canvas.height, 900);
  assert.equal(h.calls.contextOptions.powerPreference, 'low-power');
  assert.equal(h.calls.contextOptions.alpha, true);
  assert.equal(h.markerHost.children.length, 6);
  assert.equal(h.calls.readyEvents, 1);
  assert.equal(h.zoomIn.disabled, false);
  assert.equal(h.zoomOut.disabled, true);
  assert.equal(h.zoomReset.disabled, true);
  assert.equal(h.timers.size, 0);
  assert.equal(h.frames.size, 1, 'a ready globe schedules one animation frame at a time');
});

test('rotation follows elapsed time and manual pause, reduced motion, visibility, focus, and teardown stop it', async () => {
  const h = harness();
  await settle(h);
  h.frame(); // Establish the first animation timestamp without rotating.
  const initial = h.calls.uCenter[0];
  h.elapse(60_000); h.frame();
  const oneMinuteTurn = (2 * Math.PI) / 15;
  const traveled = ((h.calls.uCenter[0] - initial) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
  assert.ok(Math.abs(traveled - oneMinuteTurn) < 1e-9, 'one minute advances one fifteenth of the fifteen-minute rotation');

  h.motionToggle._listeners.get('click')();
  assert.equal(h.motionToggle.attributes['aria-pressed'], 'true');
  assert.equal(h.motionToggle.attributes['aria-label'], 'Resume Earth motion');
  assert.equal(h.motionToggle.attributes.title, 'Resume Earth motion');
  assert.equal(h.motionIcon.textContent, '▶');
  assert.equal(h.motionToggle.tagName, 'BUTTON', 'motion state updates preserve native button semantics');
  assert.equal(h.frames.size, 0);
  const manuallyPausedAt = h.calls.uCenter[0];
  h.elapse(60_000); h.frame();
  assert.equal(h.calls.uCenter[0], manuallyPausedAt);
  h.motionToggle._listeners.get('click')();
  assert.equal(h.motionToggle.attributes['aria-pressed'], 'false');
  assert.equal(h.motionToggle.attributes['aria-label'], 'Pause Earth motion');
  assert.equal(h.motionToggle.attributes.title, 'Pause Earth motion');
  assert.equal(h.motionIcon.textContent, 'Ⅱ');
  assert.equal(h.motionToggle.tagName, 'BUTTON', 'resuming preserves native button semantics');
  h.frame();
  h.elapse(60_000); h.frame();
  assert.notEqual(h.calls.uCenter[0], manuallyPausedAt);

  const pointerAt = h.calls.uCenter[0];
  h.windowListeners.get('pointerdown')({ button: 0, clientX: 840, clientY: 312 });
  h.windowListeners.get('pointermove')({ clientX: 850, clientY: 312 });
  h.windowListeners.get('pointerup')();
  assert.notEqual(h.calls.uCenter[0], pointerAt, 'manual drag still rotates the globe');
  assert.equal(h.frames.size, 0, 'automatic rotation stays paused after release');
  const releasedAt = h.calls.uCenter[0];
  h.elapse(4_999); h.frame();
  assert.equal(h.calls.uCenter[0], releasedAt, 'movement does not resume before five seconds');
  h.elapse(1); h.frame(); // first resumed frame establishes the new animation timestamp
  h.elapse(60_000); h.frame();
  assert.notEqual(h.calls.uCenter[0], releasedAt, 'automatic movement resumes after the release delay');

  const marker = h.markerHost.children.find(node => !node.hidden);
  marker.focus();
  assert.equal(h.frames.size, 0, 'marker focus pauses automatic movement');
  const focusedAt = h.calls.uCenter[0];
  h.elapse(60_000); h.frame();
  assert.equal(h.calls.uCenter[0], focusedAt);
  marker.blur();
  h.frame();
  h.elapse(60_000); h.frame();
  assert.notEqual(h.calls.uCenter[0], focusedAt);

  h.setHidden(true);
  assert.equal(h.frames.size, 0, 'hidden documents stop the animation loop');
  const hiddenAt = h.calls.uCenter[0];
  h.elapse(60_000); h.frame();
  assert.equal(h.calls.uCenter[0], hiddenAt);
  h.setHidden(false);
  h.frame();
  h.elapse(60_000); h.frame();
  assert.notEqual(h.calls.uCenter[0], hiddenAt);

  const reduced = harness({ reducedMotion: true });
  await settle(reduced);
  assert.equal(reduced.frames.size, 0, 'reduced-motion preference prevents automatic rotation');
  const reducedAt = reduced.calls.uCenter[0];
  reduced.elapse(240_000); reduced.frame();
  assert.equal(reduced.calls.uCenter[0], reducedAt);

  h.windowListeners.get('pagehide')({ persisted: false });
  assert.equal(h.frames.size, 0, 'page teardown cancels the pending frame');
  assert.equal(h.motionToggle._listeners.has('click'), false, 'teardown removes the motion control listener');
});

test('drag rotates on pointer input and resize redraws the same globe surface', async () => {
  const h = harness();
  await settle(h);
  const initial = h.calls.uCenter.slice();
  h.windowListeners.get('pointerdown')({ button: 0, clientX: 840, clientY: 312 });
  h.windowListeners.get('pointermove')({ clientX: 880, clientY: 332 });
  assert.notDeepEqual(h.calls.uCenter, initial);
  const beforeResize = h.calls.draws.length;
  h.windowListeners.get('resize')();
  assert.equal(h.calls.draws.length, beforeResize + 1);
  assert.deepEqual(h.calls.draws.at(-1), { mode: 24, first: 0, count: 6 });
});

test('resizing a focused wide globe into a narrower viewport clamps zoom before redraw', async () => {
  const h = harness({ rectWidth: 2560, rectHeight: 1232 });
  await settle(h);
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX', 'SNA'], focusAirports: ['LAX', 'SNA'] } });
  for (let step = 0; step < 5; step += 1) h.zoomIn._listeners.get('click')();
  assert.equal(h.zoomIn.disabled, true, 'wide focused view reached its zoom ceiling');

  h.resizeCanvas(900, 900);
  h.windowListeners.get('resize')();

  const radius = h.calls.uRadius / 1.5;
  assert.ok(radius <= 900 * .29, 'redraw clamps the sphere to the narrower viewport width');
  assert.ok(900 * .70 - radius >= 0 && 900 * .70 + radius <= 900, 'the sphere remains horizontally inside the canvas');
  assert.equal(h.zoomIn.disabled, true, 'zoom controls reflect the resized viewport ceiling');
});

test('pointer hit testing agrees with the shifted globe center and ignores clicks elsewhere', async () => {
  const h = harness();
  await settle(h);
  const initial = h.calls.uCenter.slice();
  h.windowListeners.get('pointerdown')({ button: 0, clientX: 430, clientY: 300 });
  h.windowListeners.get('pointermove')({ clientX: 440, clientY: 300 });
  h.windowListeners.get('pointerup')();
  assert.deepEqual(h.calls.uCenter, initial, 'a pointer outside the visible sphere does not begin a drag');
  const center = h.calls.uScreenCenter.map(value => value / 1.5);
  h.windowListeners.get('pointerdown')({ button: 0, clientX: center[0], clientY: center[1] });
  h.windowListeners.get('pointermove')({ clientX: center[0] + 10, clientY: center[1] });
  assert.notDeepEqual(h.calls.uCenter, initial, 'the visible sphere center accepts the drag');
});

test('focused canvas arrows rotate the globe while unfocused and modified keys pass through', async () => {
  const h = harness();
  await settle(h);
  assert.equal(h.canvas.attributes.tabindex, '0');
  assert.equal(h.canvas.attributes['aria-describedby'], 'globe-status globe-motion-toggle');
  const key = (options = {}) => {
    let prevented = false;
    h.canvasListeners.get('keydown')({ key: 'ArrowLeft', preventDefault() { prevented = true; }, ...options });
    return prevented;
  };
  const initial = h.calls.uCenter[0];
  assert.equal(key(), false);
  h.canvas.focus();
  assert.equal(key(), true);
  assert.ok(h.calls.uCenter[0] > initial);
  assert.equal(key({ ctrlKey: true }), false);
  assert.equal(key({ key: 'Escape' }), false);
  assert.equal(h.canvasListeners.has('keydown'), true);
  h.canvasListeners.get('webglcontextlost')({ preventDefault() {} });
  assert.equal(h.canvasListeners.has('keydown'), false);
});

test('zoom changes both shader radius and projected marker distance while drag still rotates', async () => {
  const h = harness();
  await settle(h);
  const markers = Object.fromEntries(h.markerHost.children.map((node) => [node.dataset.code, node]));
  const center = h.calls.uScreenCenter.map(value => value / 2);
  const x = parseFloat(markers.BOS.style.left), y = parseFloat(markers.BOS.style.top);
  const radius = h.calls.uRadius;
  h.zoomIn._listeners.get('click')();
  assert.ok(h.calls.uRadius > radius, 'zoom changes the sphere radius sent to the shader');
  const beforeDistance = Math.hypot(x - center[0], y - center[1]);
  const afterDistance = Math.hypot(parseFloat(markers.BOS.style.left) - center[0], parseFloat(markers.BOS.style.top) - center[1]);
  assert.ok(afterDistance > beforeDistance, 'zoom moves projected airport markers farther from the same center');
  const before = h.calls.uCenter[0];
  h.windowListeners.get('pointerdown')({ button: 0, clientX: center[0], clientY: center[1] });
  h.windowListeners.get('pointermove')({ clientX: center[0] + 20, clientY: center[1] });
  assert.notEqual(h.calls.uCenter[0], before);
});

test('wheel zoom scopes valid pixel, line, and page input and passes through modifiers, boundaries, and unsupported pointers', async () => {
  const h = harness();
  await settle(h);
  const startingRadius = h.calls.uRadius;
  const wheel = h.windowListeners.get('wheel');
  const fire = (target, options) => {
    let prevented = false;
    target({ deltaY: -1, deltaMode: 0, clientX: 840, clientY: 312, preventDefault() { prevented = true; }, ...options });
    return prevented;
  };
  assert.equal(fire(wheel, { deltaY: -10, deltaMode: 0 }), true);
  assert.equal(fire(wheel, { deltaY: -1, deltaMode: 1 }), true);
  assert.equal(fire(wheel, { deltaY: -0.01, deltaMode: 2 }), true);
  for (const options of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { deltaMode: 3 }, { deltaY: NaN }, { deltaY: 0 }]) assert.equal(fire(wheel, options), false);
  h.window.innerWidth = 999;
  assert.equal(fire(wheel, {}), false);
  h.window.innerWidth = 1440;
  h.window.matchMedia = () => ({ matches: false });
  assert.equal(fire(wheel, {}), false);
  h.window.matchMedia = () => ({ matches: true });
  for (let i = 0; i < 20; i += 1) fire(wheel, { deltaY: -120 });
  assert.ok(h.calls.uRadius > startingRadius);
  assert.ok(h.calls.uRadius <= startingRadius * 1.5 + 1e-8);
  assert.equal(fire(wheel, { deltaY: -120 }), false);
  assert.equal(fire(wheel, { target: { closest: () => ({}) } }), false, 'wheel events over controls pass through');
});

test('airport markers use the shader center and radius at desktop and mobile sizes', async (t) => {
  for (const [label, width, height] of [['desktop', 1440, 900], ['wide-desktop', 2560, 1232], ['mobile', 390, 390]]) {
    await t.test(label, async () => {
      const h = harness({ rectWidth: width, rectHeight: height });
      await settle(h);
      const markers = Object.fromEntries(h.markerHost.children.map((node) => [node.dataset.code, node]));
      assert.deepEqual(Object.keys(markers).sort(), ['ANC', 'BOS', 'LAX', 'PVD', 'SFO', 'SNA']);
      const coords = {
        ANC: [61.17408472, -149.9981375], LAX: [33.94249638, -118.40804861],
        SNA: [33.67566194, -117.86823305], SFO: [37.61880555, -122.37541666],
        BOS: [42.36294444, -71.00638888], PVD: [41.72233333, -71.42772222],
      };
      const expected = (code) => {
        const [lat0, lon0] = coords[code], lat = lat0 * Math.PI / 180, lon = lon0 * Math.PI / 180;
        const [centerLon, centerLat] = h.calls.uCenter;
        const x = Math.cos(lat) * Math.cos(lon), y = Math.cos(lat) * Math.sin(lon), z = Math.sin(lat);
        const east = -x * Math.sin(centerLon) + y * Math.cos(centerLon);
        const north = -x * Math.sin(centerLat) * Math.cos(centerLon) - y * Math.sin(centerLat) * Math.sin(centerLon) + z * Math.cos(centerLat);
        const facing = x * Math.cos(centerLat) * Math.cos(centerLon) + y * Math.cos(centerLat) * Math.sin(centerLon) + z * Math.sin(centerLat);
        const screenX = h.calls.uScreenCenter[0] / 1.5;
        const screenY = height - h.calls.uScreenCenter[1] / 1.5;
        const r = h.calls.uRadius / 1.5;
        return [screenX + east * r, screenY - north * r, facing];
      };
      let visibleCount = 0;
      for (const code of Object.keys(coords)) {
        const [x, y, facing] = expected(code), marker = markers[code];
        if (facing <= 0.015 || x < 22 || x > width - 22 || y < 22 || y > height - 22) {
          assert.equal(marker.hidden, true);
          continue;
        }
        visibleCount += 1;
        assert.ok(Math.abs(parseFloat(marker.style.left) - x) < 0.01, `${code} horizontal projection`);
        assert.ok(Math.abs(parseFloat(marker.style.top) - y) < 0.01, `${code} vertical projection`);
        assert.equal(marker.hidden, false);
        assert.equal(marker.tabIndex, 0);
      }
      assert.ok(visibleCount > 0, 'at least one mapped airport remains visible');
      assert.ok(Math.abs(h.calls.uScreenCenter[0] - width * 0.70 * 1.5) < 1e-9);
      assert.ok(Math.abs(h.calls.uScreenCenter[1] - height * 0.48 * 1.5) < 1e-9, 'shader center uses WebGL bottom-origin coordinates');
      const expectedRadius = Math.min(width * 0.29, height * 0.39);
      assert.ok(Math.abs(h.calls.uRadius / Math.min(h.window.devicePixelRatio, 1.5) - expectedRadius) < 1e-9, 'shader radius matches the shared idle CSS-pixel radius after capped DPR scaling');
    });
  }
});

test('focused and manual zoom fit the complete globe disk inside wide viewports', async (t) => {
  for (const [label, width, height] of [['2560-wide-canvas', 2560, 1232], ['1440-desktop-canvas', 1440, 900]]) {
    await t.test(label, async () => {
      const h = harness({ rectWidth: width, rectHeight: height });
      await settle(h);
      const assertFits = () => {
        const radius = h.calls.uRadius / 1.5;
        const centerX = width * .70, centerY = height * .52;
        assert.ok(centerX - radius >= 0 && centerX + radius <= width, 'the full sphere fits horizontally');
        assert.ok(centerY - radius >= 0 && centerY + radius <= height, 'the full sphere fits vertically');
        assert.ok(radius * 2 / height >= .70 && radius * 2 / height <= .90 + 1e-6, 'the globe remains large and within the 70–90vh focused target');
      };
      for (const airports of [['ANC'], ['SFO'], ['LAX', 'SNA']]) {
        h.windowListeners.get('analysisresultchange')({ detail: { airports, focusAirports: airports } });
        for (let step = 0; step < 5; step += 1) h.zoomIn._listeners.get('click')();
        assertFits();
        assert.equal(h.zoomIn.disabled, true, 'zoom-in disables at the geometry-derived ceiling');
      }
      const wheel = h.windowListeners.get('wheel');
      wheel({ deltaY: -120, deltaMode: 0, clientX: width * .70, clientY: height * .52, preventDefault() {} });
      assertFits();
    });
  }
});

test('native zoom controls enlarge the globe, stay within bounds, and reset orientation', async () => {
  const loading = harness({ autoLoad: false });
  assert.equal(loading.zoomIn.disabled, true);
  assert.equal(loading.zoomOut.disabled, true);
  assert.equal(loading.zoomReset.disabled, true);
  const h = harness();
  await settle(h);
  const initialRadius = h.calls.uRadius;
  h.zoomIn._listeners.get('click')();
  assert.ok(h.calls.uRadius > initialRadius);
  assert.equal(h.zoomOut.disabled, false);
  assert.equal([...h.timers.values()].filter((timer) => timer.ms === 200).length, 1);
  h.zoomIn._listeners.get('click')();
  h.zoomIn._listeners.get('click')();
  assert.ok(h.calls.uRadius <= initialRadius * 1.5 + 1e-8);
  assert.equal(h.zoomIn.disabled, true);
  assert.equal([...h.timers.values()].filter((timer) => timer.ms === 200).length, 1);
  const [statusId, statusTimer] = [...h.timers].find(([, timer]) => timer.ms === 200);
  h.timers.delete(statusId);
  statusTimer.fn();
  assert.match(h.zoomStatus.textContent, /Globe zoom 115%/);
  h.zoomReset._listeners.get('click')();
  assert.ok(Math.abs(h.calls.uRadius - initialRadius) < 1e-8);
  assert.deepEqual(h.calls.uCenter, [-100 * Math.PI / 180, 25 * Math.PI / 180]);
  assert.equal(h.zoomReset.disabled, true);
  const announcement = [...h.timers.values()].find((timer) => timer.ms === 200);
  announcement.fn();
  assert.match(h.zoomStatus.textContent, /Globe zoom 100%/);
  const failed = harness({ context: false });
  assert.equal(failed.zoomIn.disabled, true);
  assert.equal(failed.zoomOut.disabled, true);
  assert.equal(failed.zoomReset.disabled, true);
});

test('disabling a focused zoom button transfers focus to an available zoom control', async () => {
  const h = harness();
  await settle(h);
  h.zoomIn.focus();
  for (let i = 0; i < 3; i += 1) h.zoomIn._listeners.get('click')();
  assert.equal(h.zoomIn.disabled, true);
  assert.equal(h.zoomOut.disabled, false);
  assert.equal(h.document.activeElement, h.zoomOut);
  assert.equal(h.windowListeners.has('wheel'), true);
  assert.equal(h.windowListeners.has('keydown'), false);
});

test('marker clipping is untabbable, focused markers transfer to shortcuts, and fallback hides projected markers', async () => {
  const h = harness({ rectWidth: 320, rectHeight: 180 });
  await settle(h);
  const markers = Object.fromEntries(h.markerHost.children.map((node) => [node.dataset.code, node]));
  h.zoomIn._listeners.get('click')();
  h.zoomIn._listeners.get('click')();
  h.zoomIn._listeners.get('click')();
  for (const node of Object.values(markers)) if (!node.hidden) {
    assert.ok(parseFloat(node.style.left) >= 22 && parseFloat(node.style.left) <= 298);
    assert.ok(parseFloat(node.style.top) >= 22 && parseFloat(node.style.top) <= 158);
  }
  const target = markers.LAX;
  assert.equal(target.hidden, false);
  target.focus();
  const center = h.calls.uScreenCenter.map(value => value / 1.5);
  h.windowListeners.get('pointerdown')({ button: 0, clientX: center[0], clientY: center[1] });
  h.windowListeners.get('pointermove')({ clientX: center[0] + Math.PI * 1.5 / 0.006, clientY: center[1] });
  h.windowListeners.get('pointerup')();
  assert.equal(target.hidden, true);
  assert.equal(h.document.activeElement, h.canvas, 'a marker that leaves the globe gives focus back to the canvas');
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX'], airportDetails: { LAX: { year: 2024, status: 'partial', previous: false, label: 'Metric', value: '12%' } } } });
  h.zoomIn._listeners.get('click')();
  h.canvasListeners.get('webglcontextlost')({ preventDefault() {} });
  assert.equal(h.markerHost.hidden, true);
  assert.equal(h.zoomStatus.textContent, '');
  assert.equal([...h.timers.values()].some((timer) => timer.ms === 200), false);
  assert.ok(Object.values(markers).every((node) => node.hidden && node.tabIndex === -1));
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX'] } });
  assert.equal(h.markerHost.hidden, true);
  assert.equal(h.shortcutNodes.length, 6);
});

test('result globe markers show airport labels and selection state without duplicating result metrics', async () => {
  const h = harness();
  await settle(h);
  const markers = Object.fromEntries(h.markerHost.children.map((node) => [node.dataset.code, node]));
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX'], previous: true } });
  h.zoomIn._listeners.get('click')();
  h.zoomIn._listeners.get('click')();
  const selected = markers.LAX;
  assert.equal(selected.children.length, 1, 'only the airport label is rendered over the Earth');
  assert.equal(selected.children[0].textContent, 'LAX');
  assert.equal(selected.querySelector('.metric-label'), null, 'result values, year and status stay in the dashboard');
  assert.equal(selected.attributes['aria-label'], 'Select LAX');
  assert.equal(selected.attributes['aria-pressed'], 'false', 'result highlighting does not change the airport-picker selection');
  assert.equal(selected.hidden, false);
  assert.equal(selected.tabIndex, 0, 'visible airport markers remain keyboard accessible');
});

test('camera focuses a changed normalized airport set once and waits for map assets when needed', async () => {
  const deferred = harness({ autoLoad: false });
  deferred.windowListeners.get('analysisresultchange')({ detail: { airports: ['PVD', 'BOS'], focusAirports: ['PVD', 'BOS'] } });
  await settle(deferred);
  for (const image of deferred.imageInstances) image.onload();
  await settle(deferred);
  assert.ok(deferred.calls.uCenter, 'pending focus is applied when globe assets finish loading');
  const focusedCenter = [...deferred.calls.uCenter];
  assert.notDeepEqual(focusedCenter, [-100 * Math.PI / 180, 25 * Math.PI / 180]);
  deferred.windowListeners.get('analysisresultchange')({ detail: { airports: ['BOS', 'PVD'], focusAirports: ['BOS', 'PVD'] } });
  assert.deepEqual(deferred.calls.uCenter, focusedCenter, 'equivalent airport sets in a different order do not refocus');

  const h = harness();
  await settle(h);
  const initialCenter = [...h.calls.uCenter];
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX'], focusAirports: ['LAX'] } });
  const laxCenter = [...h.calls.uCenter];
  assert.notDeepEqual(laxCenter, initialCenter);
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['SFO'], focusAirports: ['SFO'] } });
  assert.notDeepEqual(h.calls.uCenter, laxCenter, 'a changed airport set moves the camera once');
  const sfoCenter = [...h.calls.uCenter];
  h.windowListeners.get('analysisresultchange')({ detail: { airports: [], focusAirports: [] } });
  assert.deepEqual(h.calls.uCenter, sfoCenter, 'clearing result context does not move the globe');
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['LAX'], focusAirports: ['LAX'] } });
  assert.notDeepEqual(h.calls.uCenter, sfoCenter, 'the same airport set can be focused again after context was cleared');
});

test('window state and shortcut clicks stay draft-only and collision preserves selected labels', async () => {
  const h = harness();
  await settle(h);
  const markers = Object.fromEntries(h.markerHost.children.map((node) => [node.dataset.code, node]));
  h.windowListeners.get('airportdraftchange')({ detail: { airports: ['SNA'] } });
  assert.equal(markers.SNA.attributes['aria-pressed'], 'true');
  assert.equal(markers.SNA.children[0].hidden, false);
  assert.equal(markers.LAX.children[0].hidden, true);
  h.windowListeners.get('analysisresultchange')({ detail: { airports: ['BOS'], previous: true } });
  assert.equal(markers.BOS.dataset.previous, 'true');
  const drawsBeforeDraftSelect = h.calls.draws.length;
  const shortcut = h.shortcutNodes.find((node) => node.dataset.code === 'PVD');
  shortcut._listeners.get('click')({ currentTarget: shortcut });
  const selected = h.calls.events.find((event) => event.type === 'airportdraftselect');
  assert.equal(selected.detail.code, 'PVD');
  assert.equal(selected.bubbles, false);
  assert.equal(h.calls.draws.length, drawsBeforeDraftSelect, 'draft shortcut does not move the result camera');
});

test('rear hemisphere markers cannot remain in the tab order', async () => {
  const h = harness();
  await settle(h);
  h.windowListeners.get('pointerdown')({ button: 0, clientX: 840, clientY: 312 });
  h.windowListeners.get('pointermove')({ clientX: 840 - Math.PI / 0.006, clientY: 312 });
  h.windowListeners.get('pointerup')();
  const rear = h.markerHost.children.filter((marker) => marker.hidden);
  assert.ok(rear.length > 0);
  for (const marker of rear) assert.equal(marker.tabIndex, -1);
});

test('eight-second timeout permanently selects poster and ignores late image callbacks', async () => {
  const h = harness({ autoLoad: false });
  const [timer] = [...h.timers.values()];
  assert.equal(timer.ms, 8000);
  timer.fn();
  await settle(h);
  assert.equal(h.canvas.dataset.state, 'fallback');
  assert.equal(h.canvas.hidden, true);
  assert.equal(h.poster.hidden, false);
  assert.equal(h.calls.readyEvents, 1);
  for (const image of h.imageInstances) image.onload();
  await settle(h);
  assert.equal(h.canvas.dataset.state, 'fallback');
  assert.equal(h.calls.draws.length, 0);
  assert.equal(h.calls.readyEvents, 1);
  assert.equal(h.canvasListeners.size, 0);
  assert.equal(h.canvasListeners.has('wheel'), false);
});

test('decode, shader, and context failures fall back, clean listeners, and never revive', async (t) => {
  await t.test('image decode', async () => {
    const h = harness({ autoLoad: false });
    h.imageInstances[0].onerror();
    await settle(h);
    assert.equal(h.canvas.dataset.state, 'fallback');
    assert.equal(h.poster.hidden, false);
    assert.equal(h.canvas.hidden, true);
    assert.equal(h.canvasListeners.size, 0);
  });
  await t.test('shader compile', async () => {
    const h = harness({ shaderOk: false });
    await settle(h);
    assert.equal(h.canvas.dataset.state, 'fallback');
    assert.equal(h.calls.draws.length, 0);
  });
  await t.test('partial texture upload deletes earlier allocations', async () => {
    const h = harness({ failUploadAt: 2 });
    await settle(h);
    assert.equal(h.canvas.dataset.state, 'fallback');
    assert.equal(h.calls.deletedTextures, 2);
    assert.equal(h.calls.deletedBuffers, 1);
    assert.equal(h.calls.deletedPrograms, 1);
  });
  await t.test('context creation throws', () => {
    const h = harness({ throwContext: true });
    assert.equal(h.canvas.dataset.state, 'fallback');
    assert.equal(h.poster.hidden, false);
    assert.equal(h.canvas.hidden, true);
  });
  await t.test('context loss after ready', async () => {
    const h = harness();
    await settle(h);
    const lost = { preventDefault() { this.prevented = true; } };
    h.canvasListeners.get('webglcontextlost')(lost);
    assert.equal(lost.prevented, true);
    assert.equal(h.canvas.dataset.state, 'fallback');
    assert.equal(h.poster.hidden, false);
    assert.equal(h.canvas.hidden, true);
    assert.equal(h.calls.deletedTextures, 3);
    assert.equal(h.calls.readyEvents, 1);
    assert.equal(h.canvasListeners.size, 0);
  });
  await t.test('back-forward cache keeps the working context; final pagehide releases it', async () => {
    const h = harness();
    await settle(h);
    h.windowListeners.get('pagehide')({ persisted: true });
    assert.equal(h.canvas.dataset.state, 'ready');
    assert.equal(h.calls.deletedTextures, 0);
    h.windowListeners.get('pagehide')({ persisted: false });
    assert.equal(h.calls.deletedTextures, 3);
    assert.equal(h.timers.size, 0);
    assert.equal(h.canvasListeners.size, 0);
  });
});
