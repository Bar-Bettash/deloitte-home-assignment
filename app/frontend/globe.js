(() => {
  "use strict";

  const LIMIT_MS = 8000;
  const DPR_LIMIT = 1.5;
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 1.5;
  const ROTATION_MS = 900000;
  const RESUME_DELAY_MS = 5000;
  // Scope-change camera flight: 750 ms, cubic-bezier(.22,.61,.36,1) ease-out.
  const CAMERA_MS = 750;
  const cubicBezier = (x1, y1, x2, y2) => {
    const axis = (a, b, t) => ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t;
    return (x) => {
      let low = 0, high = 1, t = x;
      for (let i = 0; i < 20; i += 1) {
        const estimate = axis(x1, x2, t);
        if (Math.abs(estimate - x) < 1e-5) break;
        if (estimate < x) low = t; else high = t;
        t = (low + high) / 2;
      }
      return axis(y1, y2, t);
    };
  };
  const easeOut = cubicBezier(0.22, 0.61, 0.36, 1);
  // Shortest signed angle, in [-PI, PI).
  const wrapAngle = (angle) => angle - 2 * Math.PI * Math.floor((angle + Math.PI) / (2 * Math.PI));
  const ASSETS = ["earth-day-2048.webp", "earth-night-2048.webp", "earth-clouds-2048.webp"];
  const vertexSource = `attribute vec2 aPosition; varying vec2 vScreen;
    void main(){ vScreen=aPosition; gl_Position=vec4(aPosition,0.0,1.0); }`;
  const fragmentSource = `precision highp float;
    varying vec2 vScreen;
    uniform vec2 uResolution;
    uniform vec2 uScreenCenter;
    uniform float uRadius;
    uniform vec2 uCenter;
    uniform sampler2D uDay;
    uniform sampler2D uNight;
    uniform sampler2D uClouds;
    void main(){
      vec2 p=(gl_FragCoord.xy-uScreenCenter)/uRadius;
      float r2=dot(p,p);
      if(r2>1.0){ gl_FragColor=vec4(0.0); return; }
      float z=sqrt(max(0.0,1.0-r2));
      float sl=sin(uCenter.x), cl=cos(uCenter.x), sa=sin(uCenter.y), ca=cos(uCenter.y);
      vec3 east=vec3(-sl,cl,0.0);
      vec3 north=vec3(-sa*cl,-sa*sl,ca);
      vec3 center=vec3(ca*cl,ca*sl,sa);
      vec3 n=east*p.x+north*p.y+center*z;
      vec2 uv=vec2(atan(n.y,n.x)/6.28318530718+0.5,0.5-asin(clamp(n.z,-1.0,1.0))/3.14159265359);
      vec3 day=texture2D(uDay,uv).rgb;
      vec3 night=texture2D(uNight,uv).rgb;
      float light=dot(n,normalize(vec3(-0.45,0.35,0.82)));
      float daylight=smoothstep(-0.16,0.12,light);
      float city=max(max(night.r,night.g),night.b);
      vec3 lit=day*(0.34+0.72*max(light,0.0));
      vec3 dark=day*0.12+night*1.35*clamp((0.16-light)*2.4,0.0,1.0);
      float cloud=texture2D(uClouds,uv).r;
      vec3 color=mix(dark,lit,daylight);
      color=mix(color,vec3(0.80,0.87,0.96),clamp((cloud-0.18)*0.54,0.0,0.34));
      float rim=pow(1.0-z,3.0);
      color+=vec3(0.04,0.28,0.66)*rim*0.82;
      gl_FragColor=vec4(color,1.0);
    }`;

  function compile(gl, type, source) {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("shader allocation failed");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader) || "shader compile failed";
      gl.deleteShader(shader);
      throw new Error(message);
    }
    return shader;
  }

  function makeProgram(gl) {
    const vertex = compile(gl, gl.VERTEX_SHADER, vertexSource);
    let fragment;
    try { fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentSource); }
    catch (error) { gl.deleteShader(vertex); throw error; }
    const program = gl.createProgram();
    if (!program) {
      gl.deleteShader(vertex);
      gl.deleteShader(fragment);
      throw new Error("program allocation failed");
    }
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const message = gl.getProgramInfoLog(program) || "shader link failed";
      gl.deleteProgram(program);
      throw new Error(message);
    }
    return program;
  }

  function makeTexture(gl, image, unit) {
    const texture = gl.createTexture();
    if (!texture) throw new Error("texture allocation failed");
    try {
      gl.activeTexture(gl.TEXTURE0 + unit);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image);
    } catch (error) { gl.deleteTexture(texture); throw error; }
    return texture;
  }

  function boot() {
    const canvas = document.querySelector("#earth-canvas");
    if (!canvas) return;
    const poster = document.querySelector("#earth-poster");
    const status = document.querySelector("#globe-status");
    const zoomStatus = document.querySelector("#globe-zoom-status");
    const zoomIn = document.querySelector("#globe-zoom-in");
    const zoomOut = document.querySelector("#globe-zoom-out");
    const zoomReset = document.querySelector("#globe-reset");
    const markerLayer = document.querySelector("#globe-markers");
    const zoomControls = document.querySelector("#globe-zoom-controls");
    const motionToggle = document.querySelector("#globe-motion-toggle");
    let gl = null;
    try { gl = canvas.getContext("webgl", { alpha: true, antialias: true, depth: false, stencil: false, powerPreference: "low-power" }); }
    catch (_) { gl = null; }
    let state = "loading";
    let generation = 1;
    let timer = null;
    let zoomStatusTimer = null;
    let readySent = false;
    let program = null;
    let buffer = null;
    let textures = [];
    let centerLon = -100 * Math.PI / 180;
    let centerLat = 25 * Math.PI / 180;
    let zoom = 1;
    let manualPaused = false, movementPaused = false, motionFrame = null, previousMotionTime = null, resumeMotionTimer = null;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)") || { matches: false };
    let drag = null;
    let cameraFlight = null;
    let coordinates = null;
    let markerNodes = [];
    let draftCodes = [];
    let resultCodes = [];
    let lastFocusKey = "";
    let pendingFocusCodes = null;
    let previousResult = false;
    const listeners = [];
    const renderListeners = [];
    let updateMarkers = () => {};
    let occlusionRects = [];
    let occlusionObserver = null;
    const listen = (target, type, fn, options, keepOnFallback = false) => {
      target.addEventListener(type, fn, options);
      (keepOnFallback ? listeners : renderListeners).push(() => target.removeEventListener(type, fn, options));
    };
    const tell = (message) => { if (status) { status.textContent = message; status.hidden = state === "ready"; } };
    const cacheOcclusionRects = () => {
      occlusionRects = [];
      for (const node of document.querySelectorAll(".contact-header, #result-title, #back-to-analysis, .result-scope-heading, .kpi-card, .series-panel, .metric-view > *, .long-haul-result, .ranking-preview, .congestion-bars, .ranking-preview .ranking-row, .key-insights, .product-intro, .prompt-grid, .more-examples, #scope-panel, .conversation-composer, #result .evidence-source-group[open]")) {
        const box = node.getBoundingClientRect();
        if (box.width && box.height) occlusionRects.push(box);
      }
    };
    const stopMotion = () => { if (motionFrame !== null) window.cancelAnimationFrame?.(motionFrame); motionFrame = null; previousMotionTime = null; };
    const cancelCameraFlight = () => {
      if (cameraFlight?.frame != null) window.cancelAnimationFrame?.(cameraFlight.frame);
      cameraFlight = null;
    };
    const canRotate = () => state === "ready" && !cameraFlight && !manualPaused && !movementPaused && !drag && !document.hidden && !reducedMotion.matches && !markerNodes.some((node) => document.activeElement === node);
    const syncMotion = () => {
      if (!canRotate()) { stopMotion(); return; }
      if (motionFrame !== null || !window.requestAnimationFrame) return;
      motionFrame = window.requestAnimationFrame((time) => {
        motionFrame = null;
        if (!canRotate()) { stopMotion(); return; }
        if (previousMotionTime !== null && time > previousMotionTime) {
          centerLon = (centerLon + (time - previousMotionTime) * Math.PI * 2 / ROTATION_MS) % (Math.PI * 2);
          draw();
        }
        previousMotionTime = time;
        syncMotion();
      });
    };
    const pauseForManualMovement = () => {
      movementPaused = true; stopMotion(); cancelCameraFlight();
      if (resumeMotionTimer !== null) window.clearTimeout(resumeMotionTimer);
      resumeMotionTimer = window.setTimeout(() => { resumeMotionTimer = null; movementPaused = false; syncMotion(); }, RESUME_DELAY_MS);
    };
    const updateMotionToggle = () => {
      if (!motionToggle) return;
      motionToggle.setAttribute("aria-pressed", String(manualPaused));
      const label = manualPaused ? "Resume Earth motion" : "Pause Earth motion";
      motionToggle.setAttribute("aria-label", label);
      motionToggle.setAttribute("title", label);
      const icon = motionToggle.querySelector("[data-motion-icon]");
      if (icon) icon.textContent = manualPaused ? "▶" : "Ⅱ";
    };
    if (motionToggle) listen(motionToggle, "click", () => { manualPaused = !manualPaused; updateMotionToggle(); syncMotion(); }, undefined, true);
    if (reducedMotion.addEventListener) {
      reducedMotion.addEventListener("change", syncMotion);
      listeners.push(() => reducedMotion.removeEventListener("change", syncMotion));
    } else if (reducedMotion.addListener) {
      reducedMotion.addListener(syncMotion);
      listeners.push(() => reducedMotion.removeListener(syncMotion));
    }
    const clearZoomStatus = () => { if (zoomStatusTimer !== null) window.clearTimeout(zoomStatusTimer); zoomStatusTimer = null; };
    const maxZoomForViewport = (width, height) => {
      if (!(width > 0 && height > 0)) return MAX_ZOOM;
      const baseRadius = Math.min(width * 0.29, height * 0.39);
      const safeRadius = Math.min(width * 0.29, height * 0.45);
      return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, safeRadius / baseRadius));
    };
    const currentMaxZoom = () => {
      const rect = canvas?.getBoundingClientRect?.();
      return rect ? maxZoomForViewport(rect.width, rect.height) : MAX_ZOOM;
    };
    const syncZoomControls = () => {
      const focused = document.activeElement;
      const unavailable = state !== "ready";
      if (zoomIn) zoomIn.disabled = unavailable || zoom >= currentMaxZoom() - 1e-6;
      if (zoomOut) zoomOut.disabled = unavailable || zoom <= MIN_ZOOM;
      if (zoomReset) zoomReset.disabled = unavailable || (zoom === 1 && centerLon === -100 * Math.PI / 180 && centerLat === 25 * Math.PI / 180);
      if (focused?.disabled) (focused === zoomIn ? [zoomOut, zoomReset] : focused === zoomOut ? [zoomIn, zoomReset] : [zoomIn, zoomOut]).find((button) => button && !button.disabled)?.focus();
    };
    const ready = () => {
      if (readySent) return;
      readySent = true;
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("globerendererready", { bubbles: false })), 0);
    };
    const stopRenderer = () => {
      cancelCameraFlight();
      stopMotion();
      occlusionObserver?.disconnect();
      occlusionObserver = null;
      if (resumeMotionTimer !== null) { window.clearTimeout(resumeMotionTimer); resumeMotionTimer = null; }
      clearZoomStatus();
      if (zoomStatus) zoomStatus.textContent = "";
      generation += 1;
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
      for (const remove of renderListeners.splice(0)) remove();
      if (gl) {
        for (const texture of textures) gl.deleteTexture(texture);
        if (buffer) gl.deleteBuffer(buffer);
        if (program) gl.deleteProgram(program);
      }
      textures = [];
      buffer = null;
      program = null;
    };
    const release = () => {
      stopRenderer();
      stopMotion();
      if (resumeMotionTimer !== null) window.clearTimeout(resumeMotionTimer);
      for (const remove of listeners.splice(0)) remove();
    };
    const fallback = (reason) => {
      if (state === "fallback") return;
      state = "fallback";
      if (motionToggle) motionToggle.hidden = true;
      stopRenderer();
      syncZoomControls();
      for (const node of markerNodes) {
        if (document.activeElement === node) canvas.focus();
        node.hidden = true;
        node.tabIndex = -1;
        node.setAttribute("aria-hidden", "true");
      }
      if (markerLayer) markerLayer.hidden = true;
      if (poster) poster.hidden = false;
      canvas.hidden = true;
      if (document.activeElement === canvas) document.querySelector("#earth-stage")?.focus();
      tell(reason || "Interactive globe unavailable. Showing the Earth image instead.");
      canvas.dataset.state = state;
      ready();
    };

    const emitDraftSelect = (event) => {
      const code = event.currentTarget.dataset.code;
      window.dispatchEvent(new CustomEvent("airportdraftselect", { detail: { code }, bubbles: false }));
    };
    const shortcuts = Array.from(document.querySelectorAll("#globe-shortcuts [data-code]"));
    for (const button of shortcuts) listen(button, "click", emitDraftSelect, undefined, true);
    const syncShortcutState = () => {
      for (const button of shortcuts) {
        button.setAttribute("aria-pressed", String(draftCodes.includes(button.dataset.code)));
        button.dataset.result = String(resultCodes.includes(button.dataset.code));
        button.dataset.previous = String(previousResult && resultCodes.includes(button.dataset.code));
      }
    };
    const draftChange = (event) => { draftCodes = event.detail?.airports || []; cacheOcclusionRects(); syncShortcutState(); updateMarkers(); };
    const resultChange = (event) => {
      resultCodes = event.detail?.airports || [];
      previousResult = Boolean(event.detail?.previous);
      const requestedFocus = Array.isArray(event.detail?.focusAirports) ? event.detail.focusAirports : resultCodes;
      const focusCodes = [...new Set(requestedFocus.map(code => String(code).trim().toUpperCase()).filter(code => ["ANC", "BOS", "LAX", "PVD", "SFO", "SNA"].includes(code)))].sort();
      const focusKey = focusCodes.join(",");
      if (!focusKey) { lastFocusKey = ""; pendingFocusCodes = null; }
      if (focusKey && focusKey !== lastFocusKey) {
        lastFocusKey = focusKey;
        pendingFocusCodes = focusCodes;
        if (state === "ready" && coordinates) applyCameraFocus(focusCodes);
      }
      cacheOcclusionRects(); syncShortcutState(); updateMarkers(); syncMotion();
    };
    listen(window, "airportdraftchange", draftChange, undefined, true);
    listen(window, "analysisresultchange", resultChange, undefined, true);
    const changeZoom = (next, reset = false) => {
      if (state !== "ready") return;
      cancelCameraFlight();
      const bounded = Math.max(MIN_ZOOM, Math.min(currentMaxZoom(), next));
      if (reset) { centerLon = -100 * Math.PI / 180; centerLat = 25 * Math.PI / 180; }
      if (bounded === zoom && !reset) return;
      zoom = bounded;
      clearZoomStatus();
      draw();
      syncZoomControls();
      if (zoomStatus) zoomStatusTimer = window.setTimeout(() => {
        zoomStatusTimer = null;
        zoomStatus.textContent = `Globe zoom ${Math.round(zoom * 100)}%`;
      }, 200);
    };
    if (zoomIn) listen(zoomIn, "click", () => changeZoom(zoom * 1.2), undefined, true);
    if (zoomOut) listen(zoomOut, "click", () => changeZoom(zoom / 1.2), undefined, true);
    if (zoomReset) listen(zoomReset, "click", () => changeZoom(1, true), undefined, true);
    syncZoomControls();
    if (!gl) { fallback(); return; }

    const uniforms = {};
    const sphereRadius = (width, height) => Math.min(width * 0.29, height * 0.39) * zoom;
    const project = (point, width, height) => {
      const sl = Math.sin(centerLon), cl = Math.cos(centerLon);
      const sa = Math.sin(centerLat), ca = Math.cos(centerLat);
      const east = -point.x * sl + point.y * cl;
      const north = -point.x * sa * cl - point.y * sa * sl + point.z * ca;
      const facing = point.x * ca * cl + point.y * ca * sl + point.z * sa;
      const radius = sphereRadius(width, height);
      return { x: width * 0.70 + east * radius, y: height * 0.52 - north * radius, facing };
    };
    const applyCameraFocus = (codes) => {
      if (!coordinates || !codes.length) return;
      const vectors = codes.map(code => {
        const point = coordinates[code];
        if (!point) return null;
        const lat = point.lat * Math.PI / 180, lon = point.lon * Math.PI / 180, cosLat = Math.cos(lat);
        return { x: cosLat * Math.cos(lon), y: cosLat * Math.sin(lon), z: Math.sin(lat) };
      }).filter(Boolean);
      if (!vectors.length) return;
      cancelCameraFlight();
      const from = { lon: centerLon, lat: centerLat, zoom };
      let x = vectors.reduce((sum, point) => sum + point.x, 0);
      let y = vectors.reduce((sum, point) => sum + point.y, 0);
      let z = vectors.reduce((sum, point) => sum + point.z, 0);
      const length = Math.hypot(x, y, z) || 1;
      x /= length; y /= length; z /= length;
      centerLon = Math.atan2(y, x);
      centerLat = Math.asin(Math.max(-1, Math.min(1, z)));
      const rect = canvas.getBoundingClientRect();
      const points = vectors;
      const startingZoom = zoom;
      const fits = candidate => {
        zoom = candidate;
        return points.every(point => {
          const projected = project(point, rect.width, rect.height);
          return projected.facing > 0.05 && Math.hypot(projected.x - rect.width * 0.70, projected.y - rect.height * 0.52) <= sphereRadius(rect.width, rect.height) * 0.88;
        });
      };
      let low = MIN_ZOOM, high = maxZoomForViewport(rect.width, rect.height);
      for (let step = 0; step < 14; step += 1) {
        const middle = (low + high) / 2;
        if (fits(middle)) low = middle; else high = middle;
      }
      zoom = vectors.length === 1 ? Math.min(startingZoom, low) : low;
      pendingFocusCodes = null;
      const to = { lon: centerLon, lat: centerLat, zoom };
      // Reduced motion (or no rAF): jump straight to the region.
      if (reducedMotion.matches || !window.requestAnimationFrame) { draw(); syncZoomControls(); return; }
      // Fly from the current view along the shortest longitude path. Auto-rotation
      // is suspended for the flight; a drag, key, wheel or reset cancels it.
      centerLon = from.lon; centerLat = from.lat; zoom = from.zoom;
      stopMotion();
      const deltaLon = wrapAngle(to.lon - from.lon);
      const flight = { frame: null, last: null, elapsed: 0 };
      cameraFlight = flight;
      const step = (time) => {
        if (cameraFlight !== flight) return;
        // Advance by at most 100ms per frame, so a long frame (the result DOM swap
        // lands in the same frames) slows the flight instead of skipping it.
        if (flight.last !== null) flight.elapsed += Math.min(100, Math.max(0, time - flight.last));
        flight.last = time;
        const progress = Math.min(1, flight.elapsed / CAMERA_MS);
        const eased = easeOut(progress);
        centerLon = from.lon + deltaLon * eased;
        centerLat = from.lat + (to.lat - from.lat) * eased;
        zoom = from.zoom + (to.zoom - from.zoom) * eased;
        draw();
        if (progress < 1) { flight.frame = window.requestAnimationFrame(step); return; }
        cameraFlight = null;
        syncZoomControls();
        syncMotion();
      };
      flight.frame = window.requestAnimationFrame(step);
    };
    updateMarkers = () => {
      if (!coordinates || !canvas || state === "fallback") return;
      markerLayer?.classList?.toggle("has-selection", resultCodes.length > 0);
      syncShortcutState();
      const rect = canvas.getBoundingClientRect();
      const positioned = [];
      for (const node of markerNodes) {
        const point = coordinates[node.dataset.code];
        const lat = point.lat * Math.PI / 180, lon = point.lon * Math.PI / 180;
        const cosLat = Math.cos(lat);
        const p = project({ x: cosLat * Math.cos(lon), y: cosLat * Math.sin(lon), z: Math.sin(lat) }, rect.width, rect.height);
        const screenX = rect.left + p.x, screenY = rect.top + p.y;
        const label = node.querySelector(".marker-label");
        const labelWidth = label ? Math.max(30, label.textContent.length * 8 + 10) : 44;
        const markerBounds = { left: screenX - Math.max(22, labelWidth), right: screenX + Math.max(22, labelWidth), top: screenY - 22, bottom: screenY + 22 };
        const occluded = occlusionRects.some((box) => markerBounds.left <= box.right && markerBounds.right >= box.left && markerBounds.top <= box.bottom && markerBounds.bottom >= box.top);
        const visible = p.facing > 0.015 && p.x >= 22 && p.x <= rect.width - 22 && p.y >= 22 && p.y <= rect.height - 22 && !occluded;
        if (!visible && document.activeElement === node) canvas.focus();
        node.hidden = !visible;
        node.tabIndex = visible ? 0 : -1;
        node.setAttribute("aria-hidden", String(!visible));
        if (!visible) continue;
        node.style.left = `${p.x}px`;
        node.style.top = `${p.y}px`;
        node.dataset.previous = String(previousResult && resultCodes.includes(node.dataset.code));
        node.classList.toggle("is-draft", draftCodes.includes(node.dataset.code));
        node.classList.toggle("is-result", resultCodes.includes(node.dataset.code));
        node.setAttribute("aria-pressed", String(draftCodes.includes(node.dataset.code)));
        const weight = document.activeElement === node ? 4 : draftCodes.includes(node.dataset.code) ? 3 : resultCodes.includes(node.dataset.code) ? 2 : 0;
        positioned.push({ node, label: node.querySelector(".marker-label"), x: p.x, y: p.y, weight });
      }
      positioned.sort((a, b) => b.weight - a.weight || a.node.dataset.code.localeCompare(b.node.dataset.code));
      const kept = [];
      for (const marker of positioned) {
        const collides = kept.some((other) => Math.abs(marker.x - other.x) < 42 && Math.abs(marker.y - other.y) < 24);
        const selected = resultCodes.includes(marker.node.dataset.code);
        const selectedIndex = resultCodes.indexOf(marker.node.dataset.code);
        const hideLabel = collides && !selected;
        marker.node.classList.toggle("label-hidden", hideLabel);
        if (marker.label) {
          marker.label.hidden = hideLabel;
          marker.label.classList.toggle("label-alternate", selected && selectedIndex % 2 === 1);
        }
        if (!collides || selected) kept.push(marker);
      }
    };
    const makeMarkers = () => {
      const host = markerLayer;
      if (!host) return;
      host.hidden = false;
      host.replaceChildren();
      markerNodes = Object.keys(coordinates).map((code) => {
        const button = document.createElement("button");
        const label = document.createElement("span");
        button.type = "button";
        button.className = "globe-marker";
        button.dataset.code = code;
        button.style.position = "absolute";
        button.style.transform = "translate(-50%, -50%)";
        button.style.pointerEvents = "auto";
        button.setAttribute("aria-label", `Select ${code}`);
        button.setAttribute("aria-pressed", "false");
        label.className = "marker-label";
        label.textContent = code;
        button.append(label);
        listen(button, "click", emitDraftSelect, undefined, true);
        listen(button, "focus", () => { updateMarkers(); syncMotion(); }, undefined, true);
        listen(button, "blur", syncMotion, undefined, true);
        host.append(button);
        return button;
      });
      updateMarkers();
    };
    const draw = () => {
      if (state !== "ready") return;
      const rect = canvas.getBoundingClientRect();
      // A viewport can become narrower after focusing/zooming. Re-clamp before
      // drawing so the globe and projected markers cannot spill past the canvas.
      zoom = Math.max(MIN_ZOOM, Math.min(maxZoomForViewport(rect.width, rect.height), zoom));
      const dpr = Math.min(window.devicePixelRatio || 1, DPR_LIMIT);
      const width = Math.max(1, Math.round(rect.width * dpr));
      const height = Math.max(1, Math.round(rect.height * dpr));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      gl.viewport(0, 0, width, height);
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      const position = gl.getAttribLocation(program, "aPosition");
      gl.enableVertexAttribArray(position);
      gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      gl.uniform2f(uniforms.resolution, width, height);
      gl.uniform2f(uniforms.screenCenter, width * 0.70, height * 0.48);
      gl.uniform1f(uniforms.radius, sphereRadius(rect.width, rect.height) * dpr);
      gl.uniform2f(uniforms.center, centerLon, centerLat);
      for (let i = 0; i < textures.length; i += 1) {
        gl.activeTexture(gl.TEXTURE0 + i);
        gl.bindTexture(gl.TEXTURE_2D, textures[i]);
        gl.uniform1i(uniforms.samplers[i], i);
      }
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      updateMarkers();
      syncZoomControls();
    };
    const resize = () => { cacheOcclusionRects(); draw(); };
    const onDown = (event) => {
      if (state !== "ready" || event.button !== 0) return;
      if (event.target?.closest?.("#analyst-rail, #result-panel, #conversation-composer, .contact-header, button, input, select, textarea, summary, a")) return;
      const rect = canvas.getBoundingClientRect();
      const dx = event.clientX - rect.left - rect.width * 0.70, dy = event.clientY - rect.top - rect.height * 0.52;
      const radius = sphereRadius(rect.width, rect.height);
      if (dx * dx + dy * dy > radius * radius) return;
      stopMotion();
      cancelCameraFlight();
      drag = { x: event.clientX, y: event.clientY, moved: false };
    };
    const rotate = (dx, dy) => {
      centerLon -= dx * 0.006 / zoom;
      centerLat = Math.max(-1.48, Math.min(1.48, centerLat + dy * 0.006 / zoom));
      draw();
    };
    const onMove = (event) => {
      if (!drag) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      drag.x = event.clientX; drag.y = event.clientY;
      if (Math.abs(dx) + Math.abs(dy) > 0) drag.moved = true;
      if (drag.moved) pauseForManualMovement();
      rotate(dx, dy);
    };
    const onKeyDown = (event) => {
      if (state !== "ready" || document.activeElement !== canvas || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      const delta = { ArrowLeft: [-24, 0], ArrowRight: [24, 0], ArrowUp: [0, -24], ArrowDown: [0, 24] }[event.key];
      if (!delta) return;
      event.preventDefault();
      pauseForManualMovement();
      rotate(delta[0], delta[1]);
    };
    const onWheel = (event) => {
      if (state !== "ready" || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey ||
          !Number.isFinite(event.deltaY) || ![0, 1, 2].includes(event.deltaMode) || event.deltaY === 0 ||
          window.innerWidth < 1000 || !window.matchMedia("(hover: hover) and (pointer: fine)").matches ||
          event.target?.closest?.("#analyst-rail, #result-panel, #conversation-composer, .contact-header, button, input, select, textarea, summary, a")) return;
      const wheelRect = canvas.getBoundingClientRect();
      const wheelDx = event.clientX - wheelRect.left - wheelRect.width * 0.70;
      const wheelDy = event.clientY - wheelRect.top - wheelRect.height * 0.52;
      const wheelRadius = sphereRadius(wheelRect.width, wheelRect.height);
      if (wheelDx * wheelDx + wheelDy * wheelDy > wheelRadius * wheelRadius) return;
      const normalized = Math.max(-120, Math.min(120, event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? canvas.clientHeight : 1)));
      if (normalized === 0) return;
      const next = Math.max(MIN_ZOOM, Math.min(currentMaxZoom(), zoom * Math.exp(-normalized * 0.0015)));
      if (next === zoom) return;
      event.preventDefault();
      pauseForManualMovement();
      changeZoom(next);
    };
    const onUp = () => { const moved = drag?.moved; drag = null; if (moved) pauseForManualMovement(); else syncMotion(); };
    const onContextLost = (event) => { event.preventDefault(); fallback(); };
    listen(window, "resize", resize, { passive: true });
    listen(window, "scroll", resize, { passive: true, capture: true });
    listen(window, "pointerdown", onDown);
    listen(window, "pointermove", onMove);
    listen(window, "pointerup", onUp);
    listen(window, "pointercancel", onUp);
    listen(window, "blur", () => { drag = null; stopMotion(); });
    listen(window, "focus", () => { cacheOcclusionRects(); draw(); syncMotion(); });
    canvas.setAttribute("tabindex", "0");
    canvas.setAttribute("aria-label", "Interactive Earth globe showing geographic context for supported airports. Drag or use arrow keys to rotate.");
    canvas.setAttribute("aria-describedby", "globe-status globe-motion-toggle");
    listen(canvas, "keydown", onKeyDown);
    listen(window, "wheel", onWheel, { passive: false });
    listen(canvas, "webglcontextlost", onContextLost);
    listen(window, "pagehide", (event) => { if (!event.persisted) release(); }, undefined, true);
    listen(document, "visibilitychange", () => { if (!document.hidden) { cacheOcclusionRects(); draw(); syncMotion(); } else stopMotion(); });
    canvas.hidden = false;
    if (motionToggle) motionToggle.hidden = true;
    // The poster is only the fallback. Its Earth is not the rendered globe's size or
    // position, so showing it while the textures load made the globe jump; the
    // canvas stays empty until its first correctly sized frame.
    if (poster) poster.hidden = true;
    canvas.dataset.state = state;
    tell("Loading interactive Earth…");
    timer = window.setTimeout(() => fallback("Interactive globe timed out. Showing the Earth image instead."), LIMIT_MS);
    const token = generation;
    const coordinateRequest = fetch("/static/assets/airport-coordinates.json", { credentials: "same-origin" }).then((response) => {
      if (!response.ok) throw new Error("Airport positions unavailable");
      return response.json();
    }).then((value) => {
      const codes = ["ANC", "BOS", "LAX", "PVD", "SFO", "SNA"];
      if (Object.keys(value).sort().join(",") !== codes.join(",") || Object.values(value).some((p) =>
        !Number.isFinite(p.lat) || !Number.isFinite(p.lon) || p.lat < -90 || p.lat > 90 || p.lon < -180 || p.lon > 180)) {
        throw new Error("Airport positions are invalid");
      }
      return value;
    });
    Promise.all([Promise.all(ASSETS.map((name) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`Could not load ${name}`));
      image.src = `/static/assets/${name}`;
    }))), coordinateRequest]).then(([images, positions]) => {
      if (token !== generation || state !== "loading") return;
      coordinates = positions;
      makeMarkers();
      program = makeProgram(gl);
      buffer = gl.createBuffer();
      if (!buffer) throw new Error("buffer allocation failed");
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
      for (let i = 0; i < images.length; i += 1) textures.push(makeTexture(gl, images[i], i));
      uniforms.resolution = gl.getUniformLocation(program, "uResolution");
      uniforms.center = gl.getUniformLocation(program, "uCenter");
      uniforms.screenCenter = gl.getUniformLocation(program, "uScreenCenter");
      uniforms.radius = gl.getUniformLocation(program, "uRadius");
      uniforms.samplers = ["uDay", "uNight", "uClouds"].map((name) => gl.getUniformLocation(program, name));
      state = "ready";
      if (pendingFocusCodes?.length) applyCameraFocus(pendingFocusCodes);
      cacheOcclusionRects();
      const observed = ["#result-panel", "#setup-controls", "#conversation-composer", "#analyst-rail"]
        .map((selector) => document.querySelector(selector)).filter(Boolean);
      if (window.MutationObserver) {
        occlusionObserver = new window.MutationObserver(() => { cacheOcclusionRects(); updateMarkers(); });
        observed.forEach((node) => occlusionObserver.observe(node, { attributes: true, childList: true, subtree: true, characterData: true }));
      }
      if (window.ResizeObserver) {
        const resizeObserver = new window.ResizeObserver(() => { cacheOcclusionRects(); updateMarkers(); });
        observed.forEach((node) => resizeObserver.observe(node));
        renderListeners.push(() => resizeObserver.disconnect());
      }
      if (timer !== null) window.clearTimeout(timer);
      canvas.dataset.state = state;
      syncZoomControls();
      if (poster) poster.hidden = true;
      tell("Interactive Earth ready. Drag to rotate.");
      if (motionToggle) motionToggle.hidden = false;
      draw();
      updateMotionToggle();
      syncMotion();
      ready();
    }).catch(() => { if (token === generation) fallback(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
