const state = {
  items: [],
  index: 0,
  annotation: null,
  activeRole: "tackler",
  clickMode: null,
  drawStart: null,
  previewBox: null,
  busy: false,
  maskCache: new Map(),
};

const $ = (id) => document.getElementById(id);
const els = {
  outputPath: $("outputPath"), progressText: $("progressText"), itemList: $("itemList"),
  title: $("title"), meta: $("meta"), prevBtn: $("prevBtn"), nextBtn: $("nextBtn"),
  video: $("video"), overlay: $("overlay"), playBtn: $("playBtn"), backBtn: $("backBtn"),
  forwardBtn: $("forwardBtn"), backSecondBtn: $("backSecondBtn"), forwardSecondBtn: $("forwardSecondBtn"),
  timeText: $("timeText"), timeline: $("timeline"),
  windowSecondsInput: $("windowSecondsInput"), eventBtn: $("eventBtn"),
  gotoEventBtn: $("gotoEventBtn"), eventText: $("eventText"),
  tacklerBtn: $("tacklerBtn"), carrierBtn: $("carrierBtn"), selectionText: $("selectionText"),
  gotoBoxBtn: $("gotoBoxBtn"), clearBoxBtn: $("clearBoxBtn"),
  positiveBtn: $("positiveBtn"), negativeBtn: $("negativeBtn"), undoClickBtn: $("undoClickBtn"),
  opacityInput: $("opacityInput"), correctionText: $("correctionText"),
  propagateBtn: $("propagateBtn"), rerunBtn: $("rerunBtn"),
  reviewVideoBtn: $("reviewVideoBtn"), reviewVideoLink: $("reviewVideoLink"), jobText: $("jobText"),
};

function current() { return state.items[state.index]; }
function fps() { return Number(state.annotation?.fps || 30); }
function frameIndex() { return Math.max(0, Math.round(els.video.currentTime * fps())); }
function frameCount() { return Math.max(1, Math.round((els.video.duration || 0) * fps())); }
function emptyAnnotation(item) {
  return {
    schema_version: 2,
    id: item.id,
    video_path: item.clip_path,
    fps: Number(item.normalized_media?.frame_rate?.split("/")[0] || 30),
    event_frame: null,
    event_window: null,
    initialization: { frame: null, tackler: {}, ball_carrier: {} },
    corrections: [],
    status: "draft",
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed: ${response.status}`);
  return payload;
}

function setBusy(value, message = "") {
  state.busy = value;
  [els.eventBtn, els.propagateBtn, els.rerunBtn, els.reviewVideoBtn].forEach((button) => { button.disabled = value; });
  if (message) els.jobText.textContent = message;
}

function renderList() {
  els.itemList.innerHTML = "";
  state.items.forEach((item, index) => {
    const button = document.createElement("button");
    button.className = `item ${index === state.index ? "active" : ""}`;
    const saved = Boolean(item.tracking_annotation);
    button.innerHTML = `<span>${item.id.replace("individual_tackle_", "Tackle ")}</span><span class="dot ${saved ? "saved" : ""}"></span>`;
    button.onclick = () => loadItem(index);
    els.itemList.appendChild(button);
  });
  const saved = state.items.filter((item) => item.tracking_annotation).length;
  els.progressText.textContent = `${saved} / ${state.items.length} saved`;
}

function loadItem(index) {
  state.index = Math.max(0, Math.min(index, state.items.length - 1));
  const item = current();
  state.annotation = structuredClone(item.tracking_annotation || emptyAnnotation(item));
  if (state.annotation.event_frame == null && state.annotation.contact_frame != null) {
    state.annotation.event_frame = state.annotation.contact_frame;
  }
  state.annotation.fps = Number(state.annotation.fps || 30);
  state.clickMode = null;
  state.drawStart = null;
  state.previewBox = null;
  state.maskCache.clear();
  els.video.pause();
  els.video.src = item.media_url;
  els.title.textContent = item.id;
  const scores = Object.entries(item.scores || {}).map(([key, value]) => `${key}:${value}`).join(" · ");
  els.meta.textContent = scores;
  els.windowSecondsInput.value = String(state.annotation.event_window?.seconds_each_side || 2);
  els.jobText.textContent = state.annotation.status === "masks_generated" ? "Masks loaded." : "Ready.";
  els.reviewVideoLink.hidden = true;
  renderList();
  renderControls();
}

function seekFrame(frame) {
  const start = state.annotation?.event_window?.start_frame ?? 0;
  const end = state.annotation?.event_window?.end_frame ?? (frameCount() - 1);
  const bounded = Math.max(start, Math.min(frame, end));
  els.video.currentTime = bounded / fps();
}

function renderControls() {
  const init = state.annotation.initialization;
  const tackler = init.tackler?.bbox_xyxy;
  const carrier = init.ball_carrier?.bbox_xyxy;
  els.selectionText.textContent = `Frame ${init.frame ?? "—"} · Tackler ${tackler ? "selected" : "missing"} · Ball carrier ${carrier ? "selected" : "missing"}`;
  const window = state.annotation.event_window;
  els.eventText.textContent = state.annotation.event_frame == null || !window
    ? "Not set"
    : `Conflict frame ${state.annotation.event_frame} · window ${window.start_frame}–${window.end_frame} (${window.seconds_each_side}s each side)`;
  const corrections = state.annotation.corrections.filter((row) => row.frame === frameIndex());
  const points = corrections.reduce((total, row) => total + (row.positive_points?.length || 0) + (row.negative_points?.length || 0), 0);
  els.correctionText.textContent = `${points} correction click${points === 1 ? "" : "s"} on this frame.`;
  els.tacklerBtn.classList.toggle("active", state.activeRole === "tackler");
  els.carrierBtn.classList.toggle("active", state.activeRole === "ball_carrier");
  els.positiveBtn.classList.toggle("active", state.clickMode === "positive");
  els.negativeBtn.classList.toggle("active", state.clickMode === "negative");
  drawOverlay();
}

function setEventWindow() {
  els.video.pause();
  const eventFrame = frameIndex();
  const seconds = Math.max(0.25, Math.min(15, Number(els.windowSecondsInput.value) || 2));
  const radius = Math.round(seconds * fps());
  const start = Math.max(0, eventFrame - radius);
  const end = Math.min(frameCount() - 1, eventFrame + radius);
  state.annotation.event_frame = eventFrame;
  state.annotation.event_window = { start_frame: start, end_frame: end, seconds_each_side: seconds };
  state.annotation.initialization = { frame: null, tackler: {}, ball_carrier: {} };
  state.annotation.corrections = [];
  state.annotation.status = "draft";
  state.maskCache.clear();
  els.timeline.min = String(start);
  els.timeline.max = String(end);
  els.timeline.value = String(eventFrame);
  els.jobText.textContent = `Event window set to frames ${start}–${end}. Now draw both player boxes.`;
  renderControls();
}

function gotoEventFrame() {
  if (state.annotation.event_frame == null) {
    els.jobText.textContent = "Set the conflict frame first.";
    return;
  }
  seekFrame(state.annotation.event_frame);
}

function resizeCanvas() {
  const rect = els.video.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  els.overlay.width = Math.max(1, Math.round(rect.width * ratio));
  els.overlay.height = Math.max(1, Math.round(rect.height * ratio));
  drawOverlay();
}

function canvasPoint(event) {
  const rect = els.overlay.getBoundingClientRect();
  const nx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const ny = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
  return { nx, ny, x: nx * els.video.videoWidth, y: ny * els.video.videoHeight };
}

function drawBox(ctx, box, color, label, width, height) {
  const sx = width / els.video.videoWidth;
  const sy = height / els.video.videoHeight;
  ctx.strokeStyle = color;
  ctx.lineWidth = 3;
  ctx.strokeRect(box[0] * sx, box[1] * sy, (box[2] - box[0]) * sx, (box[3] - box[1]) * sy);
  ctx.fillStyle = color;
  ctx.font = "bold 14px system-ui";
  ctx.fillText(label, box[0] * sx + 4, Math.max(16, box[1] * sy - 5));
}

async function drawMask(role, color, frame, generation) {
  if (!["masks_generated", "masks_stale"].includes(state.annotation.status)) return;
  const key = `${current().id}:${role}:${frame}`;
  let image = state.maskCache.get(key);
  if (!image) {
    image = new Image();
    image.src = `/mask?id=${encodeURIComponent(current().id)}&role=${role}&frame=${frame}&v=${Date.now()}`;
    try { await image.decode(); } catch { return; }
    state.maskCache.set(key, image);
    if (state.maskCache.size > 80) state.maskCache.delete(state.maskCache.keys().next().value);
  }
  if (generation !== state.drawGeneration) return;
  const offscreen = document.createElement("canvas");
  offscreen.width = els.overlay.width;
  offscreen.height = els.overlay.height;
  const context = offscreen.getContext("2d");
  context.drawImage(image, 0, 0, offscreen.width, offscreen.height);
  const pixels = context.getImageData(0, 0, offscreen.width, offscreen.height);
  const rgb = color.match(/[a-f\d]{2}/gi).map((value) => parseInt(value, 16));
  const opacity = Number(els.opacityInput.value);
  for (let index = 0; index < pixels.data.length; index += 4) {
    const maskValue = pixels.data[index];
    pixels.data[index] = rgb[0];
    pixels.data[index + 1] = rgb[1];
    pixels.data[index + 2] = rgb[2];
    pixels.data[index + 3] = Math.round(maskValue * opacity);
  }
  context.putImageData(pixels, 0, 0);
  els.overlay.getContext("2d").drawImage(offscreen, 0, 0);
}

function drawOverlay() {
  const ctx = els.overlay.getContext("2d");
  const width = els.overlay.width;
  const height = els.overlay.height;
  ctx.clearRect(0, 0, width, height);
  if (!els.video.videoWidth) return;
  state.drawGeneration = (state.drawGeneration || 0) + 1;
  const generation = state.drawGeneration;
  const frame = frameIndex();
  drawMask("tackler", "#ff3648", frame, generation);
  drawMask("ball_carrier", "#238dff", frame, generation);

  const init = state.annotation.initialization;
  if (init.frame === frame) {
    if (init.tackler?.bbox_xyxy) drawBox(ctx, init.tackler.bbox_xyxy, "#ff5c68", "tackler", width, height);
    if (init.ball_carrier?.bbox_xyxy) drawBox(ctx, init.ball_carrier.bbox_xyxy, "#52a8ff", "ball carrier", width, height);
  }
  if (state.previewBox) {
    const color = state.activeRole === "tackler" ? "#ff5c68" : "#52a8ff";
    const label = state.activeRole === "tackler" ? "tackler" : "ball carrier";
    drawBox(ctx, state.previewBox, color, label, width, height);
  }
  const sx = width / els.video.videoWidth;
  const sy = height / els.video.videoHeight;
  for (const correction of state.annotation.corrections.filter((row) => row.frame === frame)) {
    const color = correction.role === "tackler" ? "#ff5c68" : "#52a8ff";
    for (const [x, y] of correction.positive_points || []) {
      ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(x * sx, y * sy, 7, 0, Math.PI * 2); ctx.stroke();
    }
    for (const [x, y] of correction.negative_points || []) {
      ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(x * sx - 7, y * sy - 7); ctx.lineTo(x * sx + 7, y * sy + 7); ctx.moveTo(x * sx + 7, y * sy - 7); ctx.lineTo(x * sx - 7, y * sy + 7); ctx.stroke();
    }
  }
}

function overlayPointerDown(event) {
  if (state.busy) return;
  const point = canvasPoint(event);
  const frame = frameIndex();
  if (state.clickMode) {
    let correction = state.annotation.corrections.find((row) => row.frame === frame && row.role === state.activeRole);
    if (!correction) {
      correction = { frame, role: state.activeRole, positive_points: [], negative_points: [] };
      state.annotation.corrections.push(correction);
    }
    const key = state.clickMode === "positive" ? "positive_points" : "negative_points";
    correction[key].push([Math.round(point.x), Math.round(point.y)]);
    if (state.annotation.status === "masks_generated") state.annotation.status = "masks_stale";
    els.jobText.textContent = "Correction added. Run SAM 2 tracking again to update the masks.";
    renderControls();
    return;
  }
  els.video.pause();
  state.drawStart = { x: point.x, y: point.y, frame };
  state.previewBox = [point.x, point.y, point.x, point.y];
  els.overlay.setPointerCapture(event.pointerId);
}

function overlayPointerMove(event) {
  if (!state.drawStart) return;
  const point = canvasPoint(event);
  state.previewBox = [
    Math.min(state.drawStart.x, point.x), Math.min(state.drawStart.y, point.y),
    Math.max(state.drawStart.x, point.x), Math.max(state.drawStart.y, point.y),
  ];
  drawOverlay();
}

function overlayPointerUp(event) {
  if (!state.drawStart) return;
  const frame = state.drawStart.frame;
  const box = state.previewBox?.map((value) => Math.round(value));
  state.drawStart = null;
  state.previewBox = null;
  if (!box || box[2] - box[0] < 5 || box[3] - box[1] < 5) { drawOverlay(); return; }
  const init = state.annotation.initialization;
  if (init.frame !== frame) {
    init.frame = frame;
    init.tackler = {};
    init.ball_carrier = {};
  }
  init[state.activeRole] = { bbox_xyxy: box };
  state.annotation.status = "draft";
  els.jobText.textContent = `${state.activeRole === "tackler" ? "Tackler" : "Ball carrier"} box set on frame ${frame}.`;
  renderControls();
}

function clearSelectedBox() {
  state.annotation.initialization[state.activeRole] = {};
  state.annotation.status = "draft";
  els.jobText.textContent = `${state.activeRole === "tackler" ? "Tackler" : "Ball carrier"} box cleared.`;
  renderControls();
}

function gotoBoxFrame() {
  const frame = state.annotation.initialization.frame;
  if (frame == null) {
    els.jobText.textContent = "Draw the player boxes first; no box frame has been set.";
    return;
  }
  seekFrame(frame);
  els.jobText.textContent = `Moved to box frame ${frame}.`;
}

function undoClick() {
  const frame = frameIndex();
  const correction = state.annotation.corrections.find((row) => row.frame === frame && row.role === state.activeRole);
  if (!correction) return;
  if (state.clickMode === "negative" && correction.negative_points.length) correction.negative_points.pop();
  else if (correction.positive_points.length) correction.positive_points.pop();
  else if (correction.negative_points.length) correction.negative_points.pop();
  if (!correction.positive_points.length && !correction.negative_points.length) {
    state.annotation.corrections = state.annotation.corrections.filter((row) => row !== correction);
  }
  renderControls();
}

async function propagate() {
  if (state.annotation.event_frame == null || !state.annotation.event_window) { els.jobText.textContent = "Complete Step 1: set the conflict frame first."; return; }
  if (!state.annotation.initialization.tackler?.bbox_xyxy || !state.annotation.initialization.ball_carrier?.bbox_xyxy) {
    els.jobText.textContent = "Complete Step 2: draw both player boxes first."; return;
  }
  setBusy(true, "SAM 2 is propagating both masks. Keep this tab open…");
  try {
    const result = await api("/api/propagate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(state.annotation) });
    state.annotation.status = "masks_generated";
    state.annotation.frame_count = result.frame_count;
    state.annotation.event_window.start_frame = result.start_frame;
    state.annotation.event_window.end_frame = result.end_frame;
    current().tracking_annotation = structuredClone(state.annotation);
    state.maskCache.clear();
    els.jobText.textContent = `Tracking complete: ${result.frame_count} frames exported.`;
    renderList(); drawOverlay();
  } catch (error) { els.jobText.textContent = error.message; }
  finally { setBusy(false); }
}

async function generateReviewVideo() {
  if (state.annotation.status !== "masks_generated") {
    els.jobText.textContent = "Complete SAM 2 tracking before generating a review video.";
    return;
  }
  setBusy(true, "Generating the event review video with mask overlays…");
  try {
    const result = await api("/api/export-review", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: current().id, opacity: Number(els.opacityInput.value) }),
    });
    els.reviewVideoLink.href = `${result.url}&v=${Date.now()}`;
    els.reviewVideoLink.hidden = false;
    els.jobText.textContent = `Review video generated: ${result.frame_count} event frames.`;
  } catch (error) {
    els.jobText.textContent = `Review video failed: ${error.message}`;
  } finally {
    setBusy(false);
  }
}

els.video.addEventListener("loadedmetadata", () => {
  const window = state.annotation.event_window;
  els.timeline.min = String(window?.start_frame ?? 0);
  els.timeline.max = String(window?.end_frame ?? (frameCount() - 1));
  const target = state.annotation.initialization.frame ?? state.annotation.event_frame ?? 0;
  seekFrame(target);
  resizeCanvas();
});
els.video.addEventListener("timeupdate", () => {
  const frame = frameIndex();
  const window = state.annotation.event_window;
  if (window && frame > window.end_frame) { els.video.pause(); seekFrame(window.end_frame); return; }
  els.timeline.value = String(frame);
  els.timeText.textContent = `Frame ${frame} · ${els.video.currentTime.toFixed(2)}s / ${(els.video.duration || 0).toFixed(2)}s`;
  renderControls();
});
els.video.addEventListener("seeked", () => { renderControls(); });
els.video.addEventListener("play", () => { els.playBtn.textContent = "Pause"; });
els.video.addEventListener("pause", () => { els.playBtn.textContent = "Play"; });
els.timeline.oninput = () => seekFrame(Number(els.timeline.value));
els.playBtn.onclick = () => els.video.paused ? els.video.play() : els.video.pause();
els.backBtn.onclick = () => seekFrame(frameIndex() - 1);
els.forwardBtn.onclick = () => seekFrame(frameIndex() + 1);
els.backSecondBtn.onclick = () => seekFrame(frameIndex() - Math.round(fps()));
els.forwardSecondBtn.onclick = () => seekFrame(frameIndex() + Math.round(fps()));
els.prevBtn.onclick = () => loadItem(state.index - 1);
els.nextBtn.onclick = () => loadItem(state.index + 1);
els.eventBtn.onclick = setEventWindow;
els.gotoEventBtn.onclick = gotoEventFrame;
els.tacklerBtn.onclick = () => { state.activeRole = "tackler"; state.clickMode = null; renderControls(); };
els.carrierBtn.onclick = () => { state.activeRole = "ball_carrier"; state.clickMode = null; renderControls(); };
els.gotoBoxBtn.onclick = gotoBoxFrame;
els.clearBoxBtn.onclick = clearSelectedBox;
els.positiveBtn.onclick = () => { state.clickMode = state.clickMode === "positive" ? null : "positive"; renderControls(); };
els.negativeBtn.onclick = () => { state.clickMode = state.clickMode === "negative" ? null : "negative"; renderControls(); };
els.undoClickBtn.onclick = undoClick;
els.propagateBtn.onclick = propagate;
els.rerunBtn.onclick = propagate;
els.reviewVideoBtn.onclick = generateReviewVideo;
els.overlay.onpointerdown = overlayPointerDown;
els.overlay.onpointermove = overlayPointerMove;
els.overlay.onpointerup = overlayPointerUp;
els.opacityInput.oninput = drawOverlay;
window.addEventListener("resize", resizeCanvas);
document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") { event.preventDefault(); seekFrame(frameIndex() - 1); }
  if (event.key === "ArrowRight") { event.preventDefault(); seekFrame(frameIndex() + 1); }
  if (event.key === " " && event.target.tagName !== "INPUT") { event.preventDefault(); els.video.paused ? els.video.play() : els.video.pause(); }
});

api("/api/items").then((payload) => {
  state.items = payload.items;
  els.outputPath.textContent = payload.output_dir;
  if (!state.items.length) {
    els.jobText.textContent = "No videos found. Run python scripts/prepare_videos.py /path/to/videos, then refresh.";
    return;
  }
  loadItem(0);
}).catch((error) => { els.jobText.textContent = error.message; });
