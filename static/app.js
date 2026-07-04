const MODE_META = {
  handheld: {
    label: "手持检测",
    description: "不接滑台，手持 OV9281 实时移动观察 PCB，YOLO 实时标注。",
    previewTitle: "实时检测视频",
    previewSource: "OV9281 + YOLO 标注流",
    motionNote: "手持模式不移动滑台，不要求三轴回零，也不进行 XY 坐标定位。",
  },
  simulation: {
    label: "模拟模式",
    description: "导入 PCB 图片，按网格裁剪视野并模拟滑台走停检测。",
    previewTitle: "模拟图预览",
    previewSource: "导入 PCB 图片",
    motionNote: "当前模式不使用相机和滑台，会从导入图片中裁剪每个网格。",
  },
  real: {
    label: "真实模式",
    description: "真实滑台走停、OV9281 抓拍、YOLO 推理和结果回传。",
    previewTitle: "实时视频",
    previewSource: "OV9281 相机",
    motionNote: "当前模式会调用滑台驱动，启动前请确认已完成软回原点。",
  },
  video: {
    label: "视频检测",
    description: "滑台连续蛇形扫描，OV9281 视频帧实时进入 YOLO 推理。",
    previewTitle: "实时检测视频",
    previewSource: "OV9281 + YOLO 标注流",
    motionNote: "当前模式会连续移动滑台；请先手动到物理原点并执行三轴回零。",
  },
  phone: {
    label: "手机拍照",
    description: "同一局域网手机拍照上传 PCB 图片，电脑端选择照片后执行整图 YOLO 检测。",
    previewTitle: "手机上传图预览",
    previewSource: "手机上传照片",
    motionNote: "手机拍照模式不使用相机和滑台；手机只上传，电脑端人工选择后整图检测。",
  },
};

const state = {
  ws: null,
  plan: null,
  currentCell: null,
  results: new Map(),
  cellArtifacts: new Map(),
  scanning: false,
  mode: null,
  simulationImagePath: "",
  simulationImages: [],
  phoneUploads: [],
  selectedPhoneUploadId: "",
  phoneResult: null,
  lastStatus: null,
  backendRunning: false,
  previewOverride: null,
  videoPosition: null,
  videoTrail: [],
  imageCache: new Map(),
  mosaic: null,
};

function ensureModeCards() {
  const grid = document.querySelector(".mode-grid");
  if (!grid) return;
  const handheldCard = grid.querySelector('[data-mode="virtual"], [data-mode="handheld"]');
  if (!handheldCard) return;
  handheldCard.dataset.mode = "handheld";
  handheldCard.innerHTML = `
    <span class="mode-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M5 7h14v10H5zM9 7l1.4-2h3.2L15 7M8 20h8M12 10.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z" /></svg>
    </span>
    <strong>手持检测</strong>
    <span>不接滑台，手持 OV9281 移动观察 PCB，右侧视频实时显示 YOLO 标注结果。</span>
    <em>相机 + 实时 YOLO</em>
  `;
}

function ensureCommandHint() {
  const commandSection = document.querySelector(".command-section");
  if (!commandSection || document.getElementById("commandHint")) return;
  const hint = document.createElement("p");
  hint.className = "command-hint";
  hint.id = "commandHint";
  hint.textContent = "等待运行条件检查";
  commandSection.appendChild(hint);
}

ensureModeCards();
ensureCommandHint();

const el = {
  modeScreen: document.getElementById("modeScreen"),
  appShell: document.getElementById("appShell"),
  modeCards: document.querySelectorAll(".mode-card"),
  modeWsDot: document.getElementById("modeWsDot"),
  modeWsText: document.getElementById("modeWsText"),
  wsDot: document.getElementById("wsDot"),
  wsText: document.getElementById("wsText"),
  pingBtn: document.getElementById("pingBtn"),
  stopBtn: document.getElementById("stopBtn"),
  startBtn: document.getElementById("startBtn"),
  commandHint: document.getElementById("commandHint"),
  homeBtn: document.getElementById("homeBtn"),
  zUpBtn: document.getElementById("zUpBtn"),
  zDownBtn: document.getElementById("zDownBtn"),
  backToModesBtn: document.getElementById("backToModesBtn"),
  activeModeBadge: document.getElementById("activeModeBadge"),
  modeDescription: document.getElementById("modeDescription"),
  motionModeNote: document.getElementById("motionModeNote"),
  pcbLength: document.getElementById("pcbLength"),
  pcbWidth: document.getElementById("pcbWidth"),
  originX: document.getElementById("originX"),
  originY: document.getElementById("originY"),
  imagePath: document.getElementById("imagePath"),
  simulationPanel: document.getElementById("simulationPanel"),
  phonePanel: document.getElementById("phonePanel"),
  phoneUploadUrls: document.getElementById("phoneUploadUrls"),
  phoneImageFile: document.getElementById("phoneImageFile"),
  uploadPhoneBtn: document.getElementById("uploadPhoneBtn"),
  phoneUploadStatus: document.getElementById("phoneUploadStatus"),
  refreshPhoneUploadsBtn: document.getElementById("refreshPhoneUploadsBtn"),
  deletePhoneUploadBtn: document.getElementById("deletePhoneUploadBtn"),
  phoneUploadSelect: document.getElementById("phoneUploadSelect"),
  videoPanel: document.getElementById("videoPanel"),
  videoSpeed: document.getElementById("videoSpeed"),
  videoInferenceFps: document.getElementById("videoInferenceFps"),
  videoOverlap: document.getElementById("videoOverlap"),
  videoPanelNote: document.querySelector("#videoPanel .inline-note"),
  simulationImageFile: document.getElementById("simulationImageFile"),
  uploadSimulationBtn: document.getElementById("uploadSimulationBtn"),
  simulationUploadStatus: document.getElementById("simulationUploadStatus"),
  originalPcbPreview: document.getElementById("originalPcbPreview"),
  originalPcbStatus: document.getElementById("originalPcbStatus"),
  originalPcbName: document.getElementById("originalPcbName"),
  cameraPreview: document.getElementById("cameraPreview"),
  cameraPreviewStatus: document.getElementById("cameraPreviewStatus"),
  previewTitle: document.getElementById("previewTitle"),
  previewSourceText: document.getElementById("previewSourceText"),
  simulationSourceSelect: document.getElementById("simulationSourceSelect"),
  reloadCameraBtn: document.getElementById("reloadCameraBtn"),
  refreshStatusBtn: document.getElementById("refreshStatusBtn"),
  motorStatus: document.getElementById("motorStatus"),
  captureStatus: document.getElementById("captureStatus"),
  weightsStatus: document.getElementById("weightsStatus"),
  scriptStatus: document.getElementById("scriptStatus"),
  lanUrls: document.getElementById("lanUrls"),
  zDist: document.getElementById("zDist"),
  rowsValue: document.getElementById("rowsValue"),
  colsValue: document.getElementById("colsValue"),
  tilesValue: document.getElementById("tilesValue"),
  gridMeta: document.getElementById("gridMeta"),
  xyReadout: document.getElementById("xyReadout"),
  phaseValue: document.getElementById("phaseValue"),
  debounceValue: document.getElementById("debounceValue"),
  progressBar: document.getElementById("progressBar"),
  eventLog: document.getElementById("eventLog"),
  defectList: document.getElementById("defectList"),
  defectCount: document.getElementById("defectCount"),
  eventFlowText: document.getElementById("eventFlowText"),
  canvas: document.getElementById("scanCanvas"),
  railButtons: document.querySelectorAll(".rail-btn"),
};

function requestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  if (window.crypto && typeof window.crypto.getRandomValues === "function") {
    const values = new Uint32Array(4);
    window.crypto.getRandomValues(values);
    return Array.prototype.map.call(values, (value) => value.toString(16).padStart(8, "0")).join("-");
  }
  return `${Date.now()}-${Math.floor(Math.random() * 1000000)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function artifactUrl(path) {
  if (!path) return "";
  return `/api/artifact?path=${encodeURIComponent(path)}`;
}

function cachedArtifactImage(path) {
  if (!path) return null;
  const key = artifactUrl(path);
  let record = state.imageCache.get(key);
  if (!record) {
    const image = new Image();
    record = { image, loaded: false, error: false };
    image.addEventListener("load", () => {
      record.loaded = true;
      drawCanvas();
    });
    image.addEventListener("error", () => {
      record.error = true;
      drawCanvas();
    });
    image.src = key;
    state.imageCache.set(key, record);
  }
  return record.loaded && !record.error ? record.image : null;
}

function cellKey(row, col) {
  return `${row}:${col}`;
}

function logEvent(text) {
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
  el.eventLog.prepend(item);
  while (el.eventLog.children.length > 100) {
    el.eventLog.lastElementChild.remove();
  }
}

function setWsOnline(online) {
  for (const dot of [el.wsDot, el.modeWsDot]) {
    dot.classList.toggle("online", online);
  }
  el.wsText.textContent = online ? "WebSocket 在线" : "WebSocket 离线";
  el.modeWsText.textContent = online ? "WebSocket 在线" : "WebSocket 离线";
}

function send(type, payload = {}) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    logEvent("WebSocket 未连接，指令未发送");
    setCommandHint("WebSocket 未连接，指令未发送", "bad");
    return false;
  }
  logEvent(`发送指令 ${type}`);
  state.ws.send(JSON.stringify({ type, request_id: requestId(), payload }));
  return true;
}

function setCommandHint(message, tone = "neutral") {
  if (!el.commandHint) return;
  el.commandHint.textContent = message || "";
  el.commandHint.classList.remove("is-good", "is-warn", "is-bad", "is-neutral");
  el.commandHint.classList.add(`is-${tone}`);
}

function connectWs() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/control`);
  state.ws = ws;

  ws.addEventListener("open", () => {
    setWsOnline(true);
    logEvent("WebSocket connected");
  });

  ws.addEventListener("close", () => {
    setWsOnline(false);
    state.scanning = false;
    setScanButtons(false);
    logEvent("WebSocket closed, retrying");
    setTimeout(connectWs, 1400);
  });

  ws.addEventListener("error", () => {
    setWsOnline(false);
    logEvent("WebSocket error");
  });

  ws.addEventListener("message", (event) => {
    const packet = JSON.parse(event.data);
    handlePacket(packet);
  });
}

function chooseMode(mode) {
  if (!MODE_META[mode]) return;
  state.mode = mode;
  document.body.dataset.mode = mode;
  el.modeScreen.classList.add("is-hidden");
  el.appShell.classList.remove("is-hidden");
  updateModeUi();
  resetScanUi();
  updatePreviewSource();
  refreshSystemStatus({ log: true }).then(() => previewGrid());
}

function returnToModeScreen() {
  if (state.scanning) {
    logEvent("扫描运行中，先停止后再切换模式");
    return;
  }
  el.appShell.classList.add("is-hidden");
  el.modeScreen.classList.remove("is-hidden");
}

function updateModeUi() {
  const meta = MODE_META[state.mode] || MODE_META.handheld;
  el.activeModeBadge.textContent = meta.label;
  el.modeDescription.textContent = meta.description;
  el.previewTitle.textContent = meta.previewTitle;
  el.previewSourceText.textContent = meta.previewSource;
  el.motionModeNote.textContent = meta.motionNote;

  Array.prototype.forEach.call(el.modeCards, (card) => {
    card.classList.toggle("selected", card.dataset.mode === state.mode);
  });
  el.simulationPanel.classList.toggle("is-hidden", state.mode !== "simulation");
  if (el.phonePanel) {
    el.phonePanel.classList.toggle("is-hidden", state.mode !== "phone");
  }
  if (el.simulationSourceSelect) {
    el.simulationSourceSelect.classList.toggle("is-hidden", state.mode !== "simulation");
  }
  el.videoPanel.classList.toggle("is-hidden", !(state.mode === "video" || state.mode === "handheld"));
  const videoMode = state.mode === "video";
  const handheldMode = state.mode === "handheld";
  const phoneMode = state.mode === "phone";
  for (const input of [el.pcbLength, el.pcbWidth, el.originX, el.originY]) {
    input.disabled = videoMode || handheldMode || phoneMode;
  }
  if (el.videoSpeed) el.videoSpeed.disabled = handheldMode;
  if (el.videoOverlap) el.videoOverlap.disabled = handheldMode;
  if (el.videoPanelNote) {
    el.videoPanelNote.textContent = handheldMode
      ? "手持检测只使用 YOLO FPS，不移动滑台；画板不提供 XY/FOV 坐标定位。"
      : "默认扫描完整软件行程；启动前请手动移动到物理原点并三轴回零。";
  }
  el.eventFlowText.textContent = handheldMode
    ? "camera_frame → detect_inference → handheld_detection"
    : phoneMode
      ? "phone_upload → phone_detecting → phone_result"
      : videoMode
        ? "continuous_move → video_position → detect_inference → video_detection"
        : "move_xy → debounce → capture_frame → detect_inference → result";
  if (!state.scanning) {
    el.startBtn.textContent = handheldMode ? "开始手持检测" : phoneMode ? "开始整图检测" : videoMode ? "开始视频检测" : "开始检测";
  }
  setPhysicalControlsEnabled((state.mode === "real" || state.mode === "video") && !state.scanning);
  setCommandHint(handheldMode ? "手持模式可直接启动，不要求滑台回零" : phoneMode ? "请选择手机上传照片后开始整图检测" : "等待运行条件检查", "neutral");
  if (phoneMode) {
    refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
  }
}

function setPhysicalControlsEnabled(enabled) {
  el.homeBtn.disabled = !enabled;
  el.zUpBtn.disabled = !enabled;
  el.zDownBtn.disabled = !enabled;
}

function simulationImageLabel(item) {
  if (!item) return "未选择 Source 图片";
  const size = item.size_bytes ? ` · ${formatBytes(item.size_bytes)}` : "";
  return `${item.name || item.path}${size}`;
}

function currentSimulationImage() {
  return state.simulationImages.find((item) => item.path === state.simulationImagePath) || null;
}

function updateOriginalPcbPreview(item = currentSimulationImage()) {
  if (!el.originalPcbPreview || !el.originalPcbStatus || !el.originalPcbName) return;
  if (!item) {
    el.originalPcbPreview.removeAttribute("src");
    el.originalPcbName.textContent = "未选择 Source";
    el.originalPcbStatus.textContent = "导入图片后显示完整原图";
    return;
  }

  el.originalPcbName.textContent = item.name || item.path;
  el.originalPcbStatus.textContent = `完整原图：${item.name || item.path}`;
  el.originalPcbPreview.src = artifactUrl(item.path);
}

function renderSimulationSources() {
  if (!el.simulationSourceSelect) return;
  el.simulationSourceSelect.innerHTML = "";
  if (!state.simulationImages.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "尚未导入 Source 图片";
    el.simulationSourceSelect.append(option);
    el.simulationSourceSelect.disabled = true;
    return;
  }

  el.simulationSourceSelect.disabled = false;
  for (const item of state.simulationImages) {
    const option = document.createElement("option");
    option.value = item.path;
    option.textContent = simulationImageLabel(item);
    el.simulationSourceSelect.append(option);
  }
  el.simulationSourceSelect.value = state.simulationImagePath || state.simulationImages[0].path;
}

function setSimulationSource(path, options = {}) {
  const item = state.simulationImages.find((entry) => entry.path === path) || null;
  if (!item) {
    state.simulationImagePath = "";
    el.imagePath.value = "";
    renderSimulationSources();
    updateOriginalPcbPreview(null);
    updatePreviewSource();
    return false;
  }

  state.simulationImagePath = item.path;
  el.imagePath.value = item.path;
  renderSimulationSources();
  updateOriginalPcbPreview(item);
  if (state.mode === "simulation") {
    el.previewTitle.textContent = MODE_META.simulation.previewTitle;
    el.previewSourceText.textContent = `Source: ${item.name || item.path}`;
    el.cameraPreviewStatus.textContent = `已选择 ${item.name || item.path}`;
    el.cameraPreview.src = artifactUrl(item.path);
    if (options.log !== false) {
      logEvent(`模拟 Source 已切换 ${item.path}`);
    }
  }
  return true;
}

function phoneUploadLabel(item) {
  if (!item) return "未选择手机照片";
  const size = item.size_bytes ? ` · ${formatBytes(item.size_bytes)}` : "";
  const status = item.status ? ` · ${item.status}` : "";
  return `${item.name || item.upload_id}${size}${status}`;
}

function currentPhoneUpload() {
  return state.phoneUploads.find((item) => item.upload_id === state.selectedPhoneUploadId) || null;
}

function mergePhoneUpload(item) {
  if (!item || !item.upload_id) return null;
  const index = state.phoneUploads.findIndex((entry) => entry.upload_id === item.upload_id);
  if (index >= 0) {
    state.phoneUploads[index] = Object.assign({}, state.phoneUploads[index], item);
  } else {
    state.phoneUploads.unshift(item);
  }
  renderPhoneUploads();
  return state.phoneUploads.find((entry) => entry.upload_id === item.upload_id) || item;
}

function buildPhonePlan(item = currentPhoneUpload()) {
  return {
    scan_type: "phone",
    rows: 1,
    cols: 1,
    total_cells: 1,
    image_name: item ? item.name || item.upload_id : "",
    image_width: item && item.width ? item.width : 0,
    image_height: item && item.height ? item.height : 0,
  };
}

function renderPhoneUploadUrls(urls = []) {
  if (!el.phoneUploadUrls) return;
  el.phoneUploadUrls.innerHTML = "";
  const values = urls.length ? urls : ["/phone"];
  for (const url of values) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = url;
    el.phoneUploadUrls.append(link);
  }
}

function renderPhoneUploads() {
  if (!el.phoneUploadSelect) return;
  el.phoneUploadSelect.innerHTML = "";
  if (!state.phoneUploads.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "暂无手机上传照片";
    el.phoneUploadSelect.append(option);
    el.phoneUploadSelect.disabled = true;
    if (el.deletePhoneUploadBtn) el.deletePhoneUploadBtn.disabled = true;
    return;
  }

  el.phoneUploadSelect.disabled = false;
  if (el.deletePhoneUploadBtn) el.deletePhoneUploadBtn.disabled = false;
  for (const item of state.phoneUploads) {
    const option = document.createElement("option");
    option.value = item.upload_id;
    option.textContent = phoneUploadLabel(item);
    el.phoneUploadSelect.append(option);
  }
  if (!state.selectedPhoneUploadId || !currentPhoneUpload()) {
    state.selectedPhoneUploadId = state.phoneUploads[0].upload_id;
  }
  el.phoneUploadSelect.value = state.selectedPhoneUploadId;
}

function setPhoneUpload(uploadId, options = {}) {
  const item = state.phoneUploads.find((entry) => entry.upload_id === uploadId) || null;
  state.selectedPhoneUploadId = item ? item.upload_id : "";
  state.phoneResult = item && item.annotated_image ? item : null;
  renderPhoneUploads();
  if (state.mode === "phone") {
    state.plan = buildPhonePlan(item);
    state.results.clear();
    if (item && item.defects) {
      state.results.set("phone", decorateDefects(item.defects || [], item));
    }
    updatePlanUi();
    renderDefects([], 0, 0);
    updatePreviewSource();
    drawCanvas();
  }
  if (item && options.log !== false) {
    logEvent(`手机照片已选择 ${item.name || item.upload_id}`);
  }
  return Boolean(item);
}

async function refreshPhoneUploads(options = {}) {
  if (!el.phoneUploadStatus) return [];
  try {
    const response = await fetch("/api/phone/uploads");
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `phone uploads ${response.status}`);
    }
    const payload = await response.json();
    state.phoneUploads = Array.isArray(payload.uploads) ? payload.uploads : [];
    const previous = options.preserveSelection ? state.selectedPhoneUploadId : "";
    if (previous && state.phoneUploads.some((item) => item.upload_id === previous)) {
      state.selectedPhoneUploadId = previous;
    } else if (options.selectLatest !== false && state.phoneUploads.length) {
      state.selectedPhoneUploadId = state.phoneUploads[0].upload_id;
    } else if (!state.phoneUploads.length) {
      state.selectedPhoneUploadId = "";
    }
    renderPhoneUploads();
    const item = currentPhoneUpload();
    if (state.mode === "phone") {
      state.plan = buildPhonePlan(item);
      if (item && item.defects) {
        state.results.set("phone", decorateDefects(item.defects || [], item));
      } else {
        state.results.delete("phone");
      }
      updatePlanUi();
      renderDefects([], 0, 0);
      updatePreviewSource();
      drawCanvas();
    }
    el.phoneUploadStatus.textContent = state.phoneUploads.length
      ? `已加载 ${state.phoneUploads.length} 张手机照片`
      : "等待手机拍照上传";
    return state.phoneUploads;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `上传队列读取失败：${message}`;
    logEvent(`phone uploads failed: ${message}`);
    return [];
  }
}

function imageStem(filename) {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(0, dot) : filename || "phone-pcb";
}

function loadImageElement(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("图片无法在浏览器端解码"));
    };
    image.src = url;
  });
}

function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), type, quality);
  });
}

function convertCanvasToGrayscale(ctx, width, height) {
  const imageData = ctx.getImageData(0, 0, width, height);
  const data = imageData.data;
  for (let index = 0; index < data.length; index += 4) {
    const gray = Math.round(data[index] * 0.299 + data[index + 1] * 0.587 + data[index + 2] * 0.114);
    data[index] = gray;
    data[index + 1] = gray;
    data[index + 2] = gray;
  }
  ctx.putImageData(imageData, 0, 0);
}

async function compressImageForUpload(file) {
  const maxDimension = 1600;
  const quality = 0.82;
  if (!file.type || !file.type.startsWith("image/")) {
    return { blob: file, filename: file.name, compressed: false, grayscale: false, originalSize: file.size, uploadSize: file.size };
  }

  const image = await loadImageElement(file);
  const sourceWidth = image.naturalWidth || image.width;
  const sourceHeight = image.naturalHeight || image.height;
  const scale = Math.min(1, maxDimension / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d", { alpha: false, willReadFrequently: true });
  ctx.drawImage(image, 0, 0, width, height);
  convertCanvasToGrayscale(ctx, width, height);
  const blob = await canvasToBlob(canvas, "image/jpeg", quality);
  if (!blob) {
    return { blob: file, filename: file.name, compressed: false, grayscale: false, originalSize: file.size, uploadSize: file.size };
  }
  return {
    blob,
    filename: `${imageStem(file.name)}.jpg`,
    compressed: blob.size < file.size,
    grayscale: true,
    originalSize: file.size,
    uploadSize: blob.size,
  };
}

async function uploadPhoneImages() {
  const files = Array.from((el.phoneImageFile && el.phoneImageFile.files) || []);
  if (!files.length) {
    el.phoneUploadStatus.textContent = "请先选择一张或多张手机照片";
    return;
  }

  el.uploadPhoneBtn.disabled = true;
  const uploaded = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      el.phoneUploadStatus.textContent = `正在转灰度并压缩 ${index + 1}/${files.length}：${file.name}`;
      const prepared = await compressImageForUpload(file);
      el.phoneUploadStatus.textContent = prepared.grayscale
        ? `已转灰度 ${formatBytes(prepared.originalSize)} → ${formatBytes(prepared.uploadSize)}，正在上传 ${index + 1}/${files.length}`
        : `正在上传 ${index + 1}/${files.length}：${file.name}`;
      const response = await fetch(`/api/phone/upload?filename=${encodeURIComponent(prepared.filename)}`, {
        method: "POST",
        headers: { "Content-Type": prepared.blob.type || file.type || "application/octet-stream" },
        body: prepared.blob,
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `upload ${response.status}`);
      }
      uploaded.push(await response.json());
    }
    await refreshPhoneUploads({ selectLatest: true });
    if (uploaded.length) {
      setPhoneUpload(uploaded[0].upload_id, { log: true });
    }
    el.phoneUploadStatus.textContent = `已上传 ${uploaded.length} 张手机照片`;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `上传失败：${message}`;
    logEvent(`phone upload failed: ${message}`);
  } finally {
    el.uploadPhoneBtn.disabled = false;
    if (el.phoneImageFile) el.phoneImageFile.value = "";
  }
}

async function deleteSelectedPhoneUpload() {
  const uploadId = state.selectedPhoneUploadId;
  if (!uploadId) {
    el.phoneUploadStatus.textContent = "请先选择要删除的手机照片";
    return;
  }

  el.deletePhoneUploadBtn.disabled = true;
  try {
    const response = await fetch(`/api/phone/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `delete ${response.status}`);
    }
    state.results.delete("phone");
    state.phoneResult = null;
    await refreshPhoneUploads({ selectLatest: true });
    el.phoneUploadStatus.textContent = "已删除选中手机照片";
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `删除失败：${message}`;
    logEvent(`phone delete failed: ${message}`);
  } finally {
    el.deletePhoneUploadBtn.disabled = false;
  }
}

function updatePreviewSource() {
  state.previewOverride = null;
  if (state.mode === "simulation") {
    el.reloadCameraBtn.textContent = "刷新";
    el.previewTitle.textContent = MODE_META.simulation.previewTitle;
    renderSimulationSources();
    if (state.simulationImagePath) {
      const item = currentSimulationImage();
      el.previewSourceText.textContent = item ? `Source: ${item.name || item.path}` : MODE_META.simulation.previewSource;
      el.cameraPreviewStatus.textContent = item ? `已选择 ${item.name || item.path}` : "已导入 PCB 图片";
      el.cameraPreview.src = artifactUrl(state.simulationImagePath);
      updateOriginalPcbPreview(item);
    } else {
      el.previewSourceText.textContent = MODE_META.simulation.previewSource;
      el.cameraPreview.removeAttribute("src");
      el.cameraPreviewStatus.textContent = "等待导入 PCB 图片";
      updateOriginalPcbPreview(null);
    }
    return;
  }
  if (state.mode === "phone") {
    const item = currentPhoneUpload();
    el.reloadCameraBtn.textContent = "刷新";
    el.previewTitle.textContent = MODE_META.phone.previewTitle;
    el.previewSourceText.textContent = item ? `手机照片：${item.name || item.upload_id}` : MODE_META.phone.previewSource;
    if (item) {
      const imagePath = item.annotated_image || item.raw_image;
      el.cameraPreviewStatus.textContent = item.annotated_image ? "已显示手机检测标注图" : "已显示手机上传原图";
      el.cameraPreview.src = artifactUrl(imagePath);
    } else {
      el.cameraPreview.removeAttribute("src");
      el.cameraPreviewStatus.textContent = "等待手机上传或本机选择图片";
    }
    return;
  }
  if (state.mode === "video" || state.mode === "handheld") {
    startVideoPreview();
    return;
  }
  startCameraPreview();
}

function startCameraPreview() {
  state.previewOverride = null;
  const meta = MODE_META[state.mode] || MODE_META.handheld;
  el.previewTitle.textContent = meta.previewTitle;
  el.previewSourceText.textContent = meta.previewSource;
  el.reloadCameraBtn.textContent = "重连";
  el.cameraPreviewStatus.textContent = "连接相机流...";
  el.cameraPreview.src = `/api/camera/stream.mjpg?ts=${Date.now()}`;
}

function startVideoPreview() {
  state.previewOverride = null;
  const meta = MODE_META[state.mode] || MODE_META.video;
  el.previewTitle.textContent = meta.previewTitle;
  el.previewSourceText.textContent = meta.previewSource;
  el.reloadCameraBtn.textContent = "重连";
  el.cameraPreviewStatus.textContent = "等待视频检测流...";
  el.cameraPreview.src = `/api/video/stream.mjpg?ts=${Date.now()}`;
}

async function uploadSimulationImages() {
  const files = Array.from((el.simulationImageFile.files || []));
  if (!files.length) {
    el.simulationUploadStatus.textContent = "请先选择一张或多张 PCB 图片";
    return;
  }

  el.uploadSimulationBtn.disabled = true;
  const uploaded = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      el.simulationUploadStatus.textContent = `正在导入 ${index + 1}/${files.length}：${file.name}`;
      const response = await fetch(`/api/simulation/upload?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": file.type || "application/octet-stream" },
        body: file,
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.detail || payload.message || `upload failed: ${file.name}`);
      }
      uploaded.push(payload);
      const existingIndex = state.simulationImages.findIndex((item) => item.path === payload.path);
      const imageItem = {
        name: payload.name || file.name,
        path: payload.path,
        size_bytes: payload.size_bytes,
      };
      if (existingIndex >= 0) {
        state.simulationImages.splice(existingIndex, 1, imageItem);
      } else {
        state.simulationImages.push(imageItem);
      }
      logEvent(`模拟图片已导入 ${payload.path}`);
    }

    if (uploaded.length) {
      setSimulationSource(uploaded[0].path, { log: false });
      el.simulationUploadStatus.textContent = `已导入 ${uploaded.length} 张图片，可在 Source 区选择`;
      updatePreviewSource();
    }
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.simulationUploadStatus.textContent = `导入失败：${message}`;
    logEvent(`模拟图片导入失败: ${message}`);
  } finally {
    el.uploadSimulationBtn.disabled = false;
  }
}

function buildVideoPlanFromStatus(status) {
  const motor = status && status.motor ? status.motor : {};
  const axes = motor.axes || {};
  const xMax = Number(axes.x && axes.x.max_mm ? axes.x.max_mm : 135);
  const yMax = Number(axes.y && axes.y.max_mm ? axes.y.max_mm : 135);
  const overlap = Math.max(0, Math.min(0.8, Number(el.videoOverlap.value || 20) / 100));
  const linePitch = 30 * (1 - overlap);
  const rowPositions = [];
  for (let y = 0; y < yMax; y += linePitch) {
    rowPositions.push(Number(y.toFixed(4)));
  }
  if (!rowPositions.length || Math.abs(rowPositions[rowPositions.length - 1] - yMax) > 0.001) {
    rowPositions.push(Number(yMax.toFixed(4)));
  }
  const homed = {
    x: Boolean(axes.x && axes.x.homed),
    y: Boolean(axes.y && axes.y.homed),
    z: Boolean(axes.z && axes.z.homed),
  };
  return {
    scan_type: "continuous_video",
    operation_mode: "video",
    rows: rowPositions.length,
    cols: 1,
    total_cells: rowPositions.length,
    row_positions_mm: rowPositions,
    fov_mm: { width: 30, height: 30 },
    travel_mm: { x_max: xMax, y_max: yMax },
    origin_mm: { x: 0, y: 0 },
    line_pitch_mm: Number(linePitch.toFixed(4)),
    overlap,
    speed_mm_s: Number(el.videoSpeed.value || 2),
    inference_fps: Number(el.videoInferenceFps.value || 5),
    preview_fps: 8,
    homed,
    requires_soft_home: !(homed.x && homed.y && homed.z),
  };
}

function buildHandheldPlan() {
  return {
    scan_type: "handheld",
    operation_mode: "handheld",
    rows: "-",
    cols: "-",
    total_cells: "-",
    coordinate_mode: "unmapped",
  };
}

async function previewGrid() {
  if (state.mode === "handheld") {
    state.plan = buildHandheldPlan();
    updatePlanUi();
    drawCanvas();
    return;
  }

  if (state.mode === "phone") {
    state.plan = buildPhonePlan(currentPhoneUpload());
    updatePlanUi();
    drawCanvas();
    return;
  }

  if (state.mode === "video") {
    const status = state.lastStatus || await refreshSystemStatus();
    state.plan = buildVideoPlanFromStatus(status);
    updatePlanUi();
    drawCanvas();
    return;
  }

  const params = new URLSearchParams({
    pcb_length_mm: el.pcbLength.value,
    pcb_width_mm: el.pcbWidth.value,
    origin_x_mm: el.originX.value,
    origin_y_mm: el.originY.value,
  });
  try {
    const response = await fetch(`/api/grid/preview?${params}`);
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || "grid preview failed");
    }
    state.plan = await response.json();
    state.results.clear();
    updatePlanUi();
    drawCanvas();
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.gridMeta.textContent = message;
    logEvent(`网格预览失败: ${message}`);
  }
}

async function refreshSystemStatus(options = {}) {
  try {
    const response = await fetch("/api/system/status");
    if (!response.ok) {
      throw new Error(`system status ${response.status}`);
    }
    const status = await response.json();
    state.lastStatus = status;
    renderSystemStatus(status);
    if (options.log) {
      logEvent("系统状态已刷新");
    }
    return status;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    setStatusText(el.motorStatus, "状态接口异常", "bad");
    setStatusText(el.captureStatus, "状态接口异常", "bad");
    setStatusText(el.weightsStatus, "状态接口异常", "bad");
    setStatusText(el.scriptStatus, "状态接口异常", "bad");
    logEvent(`系统状态刷新失败: ${message}`);
    return null;
  }
}

function renderSystemStatus(status) {
  const motor = status.motor || {};
  const camera = status.camera || {};
  const algorithm = status.algorithm || {};
  const network = status.network || {};
  const runtime = camera.runtime || {};
  const weights = algorithm.weights || {};
  const config = algorithm.config || {};
  const script = algorithm.detect_script || {};
  const scan = status.scan || {};
  state.backendRunning = Boolean(scan.running);

  const motorText = motor.connected
    ? "已连接"
    : motor.driver_mode === "dry_run"
      ? "Dry-run"
      : "未连接";
  setStatusText(el.motorStatus, motorText, motor.connected ? "ok" : "warn");

  const cameraText = runtime.opened
    ? `已打开 ${runtime.source || ""} ${runtime.width || "-"}x${runtime.height || "-"}`
    : `不可用${runtime.last_error ? `：${runtime.last_error}` : ""}`;
  setStatusText(el.captureStatus, cameraText, runtime.opened ? "ok" : "bad");
  if (runtime.source && state.mode !== "simulation" && state.mode !== "phone" && !state.previewOverride) {
    const origin = runtime.source_origin ? ` · ${runtime.source_origin}` : "";
    el.previewSourceText.textContent = `OV9281 ${runtime.source}${origin}`;
  }

  setStatusText(el.weightsStatus, weights.exists ? `已找到 ${formatBytes(weights.size_bytes)}` : "未找到 best.pt", weights.exists ? "ok" : "bad");
  setStatusText(el.scriptStatus, script.exists && config.exists ? "脚本/配置可解析" : "脚本或配置缺失", script.exists && config.exists ? "ok" : "bad");

  if (state.backendRunning && !state.scanning) {
    setCommandHint("已有检测任务运行，请先停止", "warn");
  }
  setScanButtons(state.scanning);

  if (state.mode === "video" || state.mode === "handheld") {
    const video = scan.video ? scan.video : {};
    const label = state.mode === "handheld" ? "手持检测" : "视频检测";
    el.cameraPreviewStatus.textContent = video.running
      ? `${label} ${video.fps || 0} FPS · frame ${video.frame_id || 0}`
      : `${label}流待启动`;
  } else if (state.mode !== "simulation") {
    if (runtime.opened) {
      el.cameraPreviewStatus.textContent = `OV9281 ${runtime.width || "-"}x${runtime.height || "-"} 在线`;
    } else if (runtime.last_error) {
      el.cameraPreviewStatus.textContent = runtime.last_error;
    }
  }
  renderLanUrls(network.lan_urls || []);
  renderPhoneUploadUrls(network.phone_urls || []);
}

function renderLanUrls(urls) {
  el.lanUrls.innerHTML = "";
  if (!urls.length) {
    el.lanUrls.textContent = "未检测到局域网 IPv4";
    return;
  }
  for (const url of urls) {
    const link = document.createElement("a");
    link.href = url;
    link.textContent = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    el.lanUrls.appendChild(link);
  }
}

function setStatusText(node, text, tone) {
  node.textContent = text;
  node.classList.remove("status-ok", "status-warn", "status-bad");
  node.classList.add(`status-${tone}`);
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "-";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function updatePlanUi() {
  if (!state.plan) return;
  if (state.plan.scan_type === "handheld") {
    el.rowsValue.textContent = "-";
    el.colsValue.textContent = "-";
    el.tilesValue.textContent = "-";
    el.gridMeta.textContent = "手持检测 · 无滑台坐标定位 · 缺陷位置以实时视频画面为准";
    el.xyReadout.textContent = "无坐标";
    return;
  }
  if (state.plan.scan_type === "phone") {
    el.rowsValue.textContent = "-";
    el.colsValue.textContent = "-";
    el.tilesValue.textContent = state.plan.image_width && state.plan.image_height
      ? `${state.plan.image_width}x${state.plan.image_height}`
      : "-";
    el.gridMeta.textContent = state.plan.image_name
      ? `手机拍照 · 整图 YOLO · ${state.plan.image_name}`
      : "手机拍照 · 等待上传照片";
    el.xyReadout.textContent = "无滑台";
    return;
  }
  if (state.plan.scan_type === "continuous_video") {
    el.rowsValue.textContent = state.plan.rows;
    el.colsValue.textContent = "连续";
    el.tilesValue.textContent = `${state.plan.row_positions_mm.length} 行`;
    const travel = state.plan.travel_mm;
    const overlap = Math.round((state.plan.overlap || 0) * 100);
    el.gridMeta.textContent = `连续视频检测 · 行程 ${travel.x_max}mm x ${travel.y_max}mm · 行距 ${state.plan.line_pitch_mm}mm · 重叠 ${overlap}%`;
    return;
  }
  el.rowsValue.textContent = state.plan.rows;
  el.colsValue.textContent = state.plan.cols;
  el.tilesValue.textContent = state.plan.total_cells;
  const boundary = state.plan.logical_boundary;
  const fov = state.plan.fov_mm || { width: 30, height: 30 };
  const step = state.plan.step_mm || { x: fov.width, y: fov.height };
  const covered = state.plan.covered_mm || { length: state.plan.cols * fov.width, width: state.plan.rows * fov.height };
  const overlap = Math.round((state.plan.overlap || 0) * 100);
  el.gridMeta.textContent = `FOV ${fov.width}mm x ${fov.height}mm · 步距 ${step.x}mm x ${step.y}mm · 重叠 ${overlap}% · 覆盖 ${covered.length}mm x ${covered.width}mm · 溢出 ${boundary.overflow_right_mm}mm / ${boundary.overflow_bottom_mm}mm`;
}

function resizeCanvasForDisplay() {
  const rect = el.canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(360, Math.floor(rect.width * ratio));
  const height = Math.max(280, Math.floor(rect.height * ratio));
  if (el.canvas.width !== width || el.canvas.height !== height) {
    el.canvas.width = width;
    el.canvas.height = height;
  }
  return { width, height };
}

function steppedCanvasMetrics(width, height) {
  const pad = 38;
  const fov = state.plan.fov_mm || { width: 30, height: 30 };
  const covered = state.plan.covered_mm || {
    length: Math.max(1, state.plan.cols || 1) * fov.width,
    width: Math.max(1, state.plan.rows || 1) * fov.height,
  };
  const step = state.plan.step_mm || { x: fov.width, y: fov.height };
  const origin = state.plan.origin_mm || { x: 0, y: 0 };
  const logicalW = Math.max(fov.width, Number(covered.length) || fov.width);
  const logicalH = Math.max(fov.height, Number(covered.width) || fov.height);
  const scale = Math.min((width - pad * 2) / logicalW, (height - pad * 2) / logicalH);
  const boardW = logicalW * scale;
  const boardH = logicalH * scale;
  const ox = (width - boardW) / 2;
  const oy = (height - boardH) / 2;
  return { scale, ox, oy, boardW, boardH, fov, step, origin, covered: { length: logicalW, width: logicalH } };
}

function steppedCellRect(metrics, row, col) {
  const xMm = metrics.origin.x + col * metrics.step.x;
  const yMm = metrics.origin.y + row * metrics.step.y;
  return {
    x: metrics.ox + (xMm - metrics.origin.x) * metrics.scale,
    y: metrics.oy + (yMm - metrics.origin.y) * metrics.scale,
    w: metrics.fov.width * metrics.scale,
    h: metrics.fov.height * metrics.scale,
    x_mm: xMm,
    y_mm: yMm,
  };
}

function drawDetectionOverlay(ctx, defects, rect, image, tone = "#c43c3c") {
  if (!defects || !defects.length) return;
  const imageW = image && image.naturalWidth ? image.naturalWidth : 1280;
  const imageH = image && image.naturalHeight ? image.naturalHeight : 800;
  ctx.save();
  ctx.lineWidth = 2;
  ctx.font = "12px Microsoft YaHei, Segoe UI, sans-serif";
  for (const defect of defects) {
    const bbox = defect.bbox || [];
    if (bbox.length < 4) continue;
    const x1 = Number(bbox[0]);
    const y1 = Number(bbox[1]);
    const x2 = Number(bbox[2]);
    const y2 = Number(bbox[3]);
    if (![x1, y1, x2, y2].every(Number.isFinite)) continue;
    const bx = rect.x + (x1 / imageW) * rect.w;
    const by = rect.y + (y1 / imageH) * rect.h;
    const bw = Math.max(2, ((x2 - x1) / imageW) * rect.w);
    const bh = Math.max(2, ((y2 - y1) / imageH) * rect.h);
    ctx.strokeStyle = tone;
    ctx.strokeRect(bx, by, bw, bh);
    const label = `${defect.label || defect.type || "defect"} ${Math.round((defect.confidence || 0) * 100)}%`;
    const labelW = ctx.measureText(label).width + 10;
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.fillRect(bx, Math.max(rect.y, by - 18), labelW, 18);
    ctx.fillStyle = tone;
    ctx.fillText(label, bx + 5, Math.max(rect.y + 13, by - 5));
  }
  ctx.restore();
}

function drawMosaicDetections(ctx, metrics, mosaicImage) {
  if (!state.mosaic || !state.mosaic.defects || !state.mosaic.defects.length || !mosaicImage) return;
  drawDetectionOverlay(
    ctx,
    state.mosaic.defects,
    { x: metrics.ox, y: metrics.oy, w: metrics.boardW, h: metrics.boardH },
    mosaicImage,
    "#b42318",
  );
}

function drawSteppedCanvas(ctx, width, height) {
  const metrics = steppedCanvasMetrics(width, height);
  const { ox, oy, boardW, boardH } = metrics;
  const cols = state.plan.cols;
  const rows = state.plan.rows;
  const boundary = state.plan.logical_boundary || {};
  const mosaicImage = state.mosaic && state.mosaic.mosaic_raw ? cachedArtifactImage(state.mosaic.mosaic_raw) : null;

  ctx.fillStyle = "#e8f3ef";
  ctx.fillRect(ox, oy, boardW, boardH);

  if (mosaicImage) {
    ctx.drawImage(mosaicImage, ox, oy, boardW, boardH);
  } else {
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        const key = `${row}:${col}`;
        const artifact = state.cellArtifacts.get(key);
        const rect = steppedCellRect(metrics, row, col);
        const image = artifact && artifact.raw_image ? cachedArtifactImage(artifact.raw_image) : null;
        const cellDefects = state.results.get(key) || (artifact && artifact.defects) || [];
        const hasDefect = cellDefects && cellDefects.length > 0;

        if (image) {
          ctx.drawImage(image, rect.x, rect.y, rect.w, rect.h);
          ctx.fillStyle = "rgba(255, 255, 255, 0.06)";
          ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
        } else {
          ctx.fillStyle = hasDefect
            ? "rgba(209, 67, 67, 0.18)"
            : artifact
              ? "rgba(33, 128, 87, 0.16)"
              : "rgba(33, 128, 87, 0.07)";
          ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
        }

        if (hasDefect) {
          ctx.fillStyle = "rgba(209, 67, 67, 0.12)";
          ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
          drawDetectionOverlay(ctx, cellDefects, rect, image);
        }
      }
    }
  }

  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, boardW, boardH);

  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const rect = steppedCellRect(metrics, row, col);
      const key = `${row}:${col}`;
      const artifact = state.cellArtifacts.get(key);
      const cellDefects = state.results.get(key) || (artifact && artifact.defects) || [];
      const isCurrent = state.currentCell && state.currentCell.row === row && state.currentCell.col === col;

      if (col === cols - 1 && boundary.last_col_visible_ratio < 1) {
        const visibleW = rect.w * boundary.last_col_visible_ratio;
        drawClippedZone(ctx, rect.x + visibleW, rect.y, rect.w - visibleW, rect.h);
      }
      if (row === rows - 1 && boundary.last_row_visible_ratio < 1) {
        const visibleH = rect.h * boundary.last_row_visible_ratio;
        drawClippedZone(ctx, rect.x, rect.y + visibleH, rect.w, rect.h - visibleH);
      }

      ctx.strokeStyle = isCurrent ? "#1769aa" : cellDefects.length ? "rgba(196, 60, 60, 0.85)" : "rgba(71, 84, 103, 0.62)";
      ctx.lineWidth = isCurrent ? 3 : 1;
      ctx.strokeRect(rect.x + 0.5, rect.y + 0.5, rect.w - 1, rect.h - 1);

      ctx.fillStyle = "rgba(255, 255, 255, 0.86)";
      ctx.fillRect(rect.x + 6, rect.y + 6, Math.max(38, Math.min(62, rect.w * 0.34)), 18);
      ctx.fillStyle = "#17202a";
      ctx.font = `${Math.max(10, Math.min(13, rect.w * 0.12))}px Segoe UI, sans-serif`;
      ctx.fillText(`${row},${col}`, rect.x + 10, rect.y + 20);
    }
  }

  if (mosaicImage) {
    drawMosaicDetections(ctx, metrics, mosaicImage);
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.fillRect(ox + 10, oy + 10, 138, 22);
    ctx.fillStyle = "#17202a";
    ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("整板拼接复核图", ox + 18, oy + 26);
  }
}

function drawCanvas() {
  const { width, height } = resizeCanvasForDisplay();
  const ctx = el.canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#f8fafc";
  ctx.fillRect(0, 0, width, height);

  ctx.strokeStyle = "#d9e2ec";
  ctx.lineWidth = 1;
  for (let x = 0; x < width; x += 28) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
  for (let y = 0; y < height; y += 28) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  if (!state.plan) {
    ctx.fillStyle = "#667085";
    ctx.font = "24px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText(state.mode === "handheld" ? "手持模式无坐标定位" : state.mode === "phone" ? "等待手机上传照片" : state.mode === "video" ? "等待滑台行程状态" : "等待 PCB 长宽参数", 36, 58);
    return;
  }

  if (state.plan.scan_type === "handheld") {
    drawHandheldCanvas(ctx, width, height);
    return;
  }

  if (state.plan.scan_type === "phone") {
    drawPhoneCanvas(ctx, width, height);
    return;
  }

  if (state.plan.scan_type === "continuous_video") {
    drawVideoCanvas(ctx, width, height);
    return;
  }

  drawSteppedCanvas(ctx, width, height);
}

function drawPhoneCanvas(ctx, width, height) {
  const pad = 42;
  const item = currentPhoneUpload();
  const sourcePath = item ? item.raw_image : "";
  const image = sourcePath ? cachedArtifactImage(sourcePath) : null;
  const defects = state.results.get("phone") || (item && item.defects ? decorateDefects(item.defects || [], item) : []);
  const naturalW = image && image.naturalWidth ? image.naturalWidth : Math.max(1, state.plan.image_width || 16);
  const naturalH = image && image.naturalHeight ? image.naturalHeight : Math.max(1, state.plan.image_height || 10);
  const scale = Math.min((width - pad * 2) / naturalW, (height - pad * 2) / naturalH);
  const boardW = naturalW * scale;
  const boardH = naturalH * scale;
  const ox = (width - boardW) / 2;
  const oy = (height - boardH) / 2;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(ox, oy, boardW, boardH);
  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, boardW, boardH);

  if (image) {
    ctx.drawImage(image, ox, oy, boardW, boardH);
    drawDetectionOverlay(ctx, defects, { x: ox, y: oy, w: boardW, h: boardH }, image, "#b42318");
  } else {
    ctx.fillStyle = "#667085";
    ctx.font = "22px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("等待手机上传或选择照片", ox + 28, oy + 58);
    ctx.font = "14px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("手机访问 /phone 上传后，电脑端选择照片并开始整图检测。", ox + 28, oy + 92);
  }

  ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
  ctx.fillRect(ox + 10, oy + 10, Math.max(80, Math.min(260, boardW - 20)), 28);
  ctx.fillStyle = "#17202a";
  ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
  const label = item ? `手机照片 · ${item.name || item.upload_id}` : "手机拍照模式";
  ctx.fillText(label.slice(0, 32), ox + 20, oy + 29);
}

function drawHandheldCanvas(ctx, width, height) {
  const pad = 42;
  const boardW = width - pad * 2;
  const boardH = height - pad * 2;
  const ox = pad;
  const oy = pad;

  ctx.fillStyle = "#edf7f4";
  ctx.fillRect(ox, oy, boardW, boardH);
  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, boardW, boardH);

  ctx.strokeStyle = "rgba(23, 105, 170, 0.18)";
  ctx.lineWidth = 1;
  for (let x = ox + 40; x < ox + boardW; x += 40) {
    ctx.beginPath();
    ctx.moveTo(x, oy);
    ctx.lineTo(x, oy + boardH);
    ctx.stroke();
  }
  for (let y = oy + 40; y < oy + boardH; y += 40) {
    ctx.beginPath();
    ctx.moveTo(ox, y);
    ctx.lineTo(ox + boardW, y);
    ctx.stroke();
  }

  ctx.fillStyle = "#17202a";
  ctx.font = "24px Microsoft YaHei, Segoe UI, sans-serif";
  ctx.fillText("手持模式无坐标定位", ox + 28, oy + 58);
  ctx.fillStyle = "#667085";
  ctx.font = "15px Microsoft YaHei, Segoe UI, sans-serif";
  ctx.fillText("请观察右侧实时标注视频；PCB 画板不显示滑台 FOV 位置。", ox + 28, oy + 92);

  ctx.fillStyle = state.scanning ? "#218057" : "#8a97a8";
  ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
  ctx.fillText(state.scanning ? "实时推理中" : "等待开始手持检测", ox + 28, oy + 126);
}

function videoCanvasMetrics(width, height) {
  const pad = 42;
  const travel = state.plan.travel_mm;
  const fov = state.plan.fov_mm;
  const logicalW = travel.x_max + fov.width;
  const logicalH = travel.y_max + fov.height;
  const scale = Math.min((width - pad * 2) / logicalW, (height - pad * 2) / logicalH);
  const boardW = travel.x_max * scale;
  const boardH = travel.y_max * scale;
  const ox = (width - boardW) / 2;
  const oy = (height - boardH) / 2;
  return { scale, ox, oy, boardW, boardH };
}

function drawVideoCanvas(ctx, width, height) {
  const metrics = videoCanvasMetrics(width, height);
  const { scale, ox, oy, boardW, boardH } = metrics;
  const travel = state.plan.travel_mm;
  const fov = state.plan.fov_mm;

  ctx.fillStyle = "#eaf3f8";
  ctx.fillRect(ox, oy, boardW, boardH);
  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, boardW, boardH);

  ctx.fillStyle = "#475467";
  ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
  ctx.fillText(`软件行程 ${travel.x_max}mm x ${travel.y_max}mm`, ox, Math.max(22, oy - 12));

  ctx.strokeStyle = "rgba(23, 105, 170, 0.35)";
  ctx.lineWidth = 1;
  ctx.setLineDash([8, 8]);
  for (const y of state.plan.row_positions_mm || []) {
    const py = oy + y * scale;
    ctx.beginPath();
    ctx.moveTo(ox, py);
    ctx.lineTo(ox + boardW, py);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  if (state.videoTrail.length > 1) {
    ctx.strokeStyle = "rgba(33, 128, 87, 0.85)";
    ctx.lineWidth = 2;
    ctx.beginPath();
    state.videoTrail.forEach((point, index) => {
      const px = ox + point.x_mm * scale;
      const py = oy + point.y_mm * scale;
      if (index === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    });
    ctx.stroke();
  }

  const position = state.videoPosition || { x_mm: 0, y_mm: 0, area_mm: { x: 0, y: 0, width: fov.width, height: fov.height } };
  const rectX = ox + position.area_mm.x * scale;
  const rectY = oy + position.area_mm.y * scale;
  const rectW = position.area_mm.width * scale;
  const rectH = position.area_mm.height * scale;

  ctx.fillStyle = "rgba(23, 105, 170, 0.16)";
  ctx.fillRect(rectX, rectY, rectW, rectH);
  ctx.strokeStyle = "#c43c3c";
  ctx.lineWidth = 3;
  ctx.strokeRect(rectX, rectY, rectW, rectH);

  if (position.area_mm.clipped_right_mm || position.area_mm.clipped_bottom_mm) {
    ctx.fillStyle = "rgba(183, 121, 31, 0.18)";
    ctx.fillRect(
      Math.max(rectX, ox + boardW),
      rectY,
      Math.max(0, rectX + rectW - (ox + boardW)),
      rectH,
    );
  }

  const segment = position.segment || {};
  const lineText = segment.row !== undefined && segment.row !== null ? `row ${segment.row}` : "ready";
  const label = `FOV ${position.x_mm.toFixed(1)}, ${position.y_mm.toFixed(1)} · ${lineText}`;
  ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
  const labelW = ctx.measureText(label).width + 14;
  const labelX = rectX + 7;
  const labelY = rectY + 21;
  ctx.fillStyle = "rgba(255, 255, 255, 0.88)";
  ctx.fillRect(labelX - 5, labelY - 15, labelW, 20);
  ctx.strokeStyle = "rgba(185, 199, 214, 0.9)";
  ctx.lineWidth = 1;
  ctx.strokeRect(labelX - 5, labelY - 15, labelW, 20);
  ctx.fillStyle = "#17202a";
  ctx.fillText(label, labelX, labelY);
}

function drawClippedZone(ctx, x, y, w, h) {
  if (w <= 0 || h <= 0) return;
  ctx.save();
  ctx.fillStyle = "rgba(224, 148, 26, 0.18)";
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = "rgba(224, 148, 26, 0.65)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = -h; i < w; i += 10) {
    ctx.moveTo(x + i, y + h);
    ctx.lineTo(x + i + h, y);
  }
  ctx.stroke();
  ctx.restore();
}

function decorateDefects(defects, payload) {
  return (defects || []).map((defect) => Object.assign({}, defect, {
    raw_image: payload.raw_image,
    annotated_image: payload.annotated_image,
    capture_ms: payload.capture_ms,
    inference_ms: payload.inference_ms,
  }));
}

function renderDefects(defects, row, col) {
  if (defects.length) {
    state.results.set(cellKey(row, col), defects);
  }
  const all = [];
  state.results.forEach((items, cell) => {
    for (const item of items) {
      all.push(Object.assign({ cell }, item));
    }
  });
  el.defectCount.textContent = `${all.length} items`;
  el.defectList.innerHTML = "";

  if (!all.length) {
    const empty = document.createElement("div");
    empty.className = "defect-item empty";
    empty.innerHTML = `<div class="thumb"></div><div><strong>暂无缺陷</strong><br><span>等待检测结果回传</span></div><span>OK</span>`;
    el.defectList.append(empty);
    return;
  }

  for (const defect of all.slice(-40).reverse()) {
    const item = document.createElement("div");
    item.className = "defect-item";
    const image = defect.annotated_image ? artifactUrl(defect.annotated_image) : "";
    const timing = defect.inference_ms ? ` · ${Math.round(defect.inference_ms)}ms` : "";
    item.innerHTML = `
      <div class="thumb"${image ? ` style="background-image:url('${image}')"` : ""}></div>
      <div><strong>${escapeHtml(defect.label || defect.type || "unknown")}</strong><br><span>cell ${escapeHtml(defect.cell)} · bbox ${escapeHtml(JSON.stringify(defect.bbox || []))}${timing}</span></div>
      <span>${Math.round((defect.confidence || 0) * 100)}%</span>
    `;
    el.defectList.append(item);
  }
}

function rememberCellArtifact(payload) {
  if (payload.row === undefined || payload.col === undefined) return;
  const key = cellKey(payload.row, payload.col);
  const existing = state.cellArtifacts.get(key) || {};
  const next = Object.assign({}, existing, {
    row: payload.row,
    col: payload.col,
  });
  for (const field of ["raw_image", "annotated_image", "result_json", "latest_image", "source_image", "simulation_crop"]) {
    if (payload[field]) next[field] = payload[field];
  }
  for (const field of ["capture_ms", "inference_ms", "status"]) {
    if (payload[field] !== undefined) next[field] = payload[field];
  }
  if (payload.defects) next.defects = payload.defects;
  state.cellArtifacts.set(
    key,
    next,
  );
  if (next.raw_image) cachedArtifactImage(next.raw_image);
  if (next.annotated_image) cachedArtifactImage(next.annotated_image);
}

function showPreviewImage(row, col, imagePath, sourceText, setOverride = true) {
  if (!imagePath) return false;
  if (setOverride) {
    state.previewOverride = { row, col, imagePath };
  }
  el.previewTitle.textContent = `区域 ${row},${col}`;
  el.previewSourceText.textContent = sourceText;
  el.cameraPreviewStatus.textContent = `正在加载区域 ${row},${col}`;
  el.cameraPreview.src = artifactUrl(imagePath);
  return true;
}

function showCellArtifact(row, col) {
  const artifact = state.cellArtifacts.get(cellKey(row, col));
  if (!artifact) {
    el.cameraPreviewStatus.textContent = state.scanning
      ? `区域 ${row},${col} 尚未完成检测`
      : `区域 ${row},${col} 暂无检测图片`;
    return;
  }

  const imagePath = artifact.annotated_image || artifact.raw_image;
  if (!imagePath) {
    el.cameraPreviewStatus.textContent = `区域 ${row},${col} 没有图片路径`;
    return;
  }

  showPreviewImage(row, col, imagePath, artifact.annotated_image ? "检测标注图" : "原始图");
}

function updateProgress(progress) {
  if (!progress) return;
  const ratio = progress.total ? (progress.done / progress.total) * 100 : 0;
  el.progressBar.style.width = `${Math.max(0, Math.min(100, ratio))}%`;
}

function resetScanUi() {
  state.currentCell = null;
  state.videoPosition = null;
  state.videoTrail = [];
  state.mosaic = null;
  state.phoneResult = null;
  state.results.clear();
  state.cellArtifacts.clear();
  state.previewOverride = null;
  el.defectList.innerHTML = "";
  el.defectCount.textContent = "0 items";
  el.progressBar.style.width = "0%";
  el.phaseValue.textContent = "Idle";
  el.debounceValue.textContent = "Ready";
  renderDefects([], 0, 0);
  drawCanvas();
}

function handlePacket(packet) {
  const payload = packet.payload || {};
  switch (packet.type) {
    case "ack":
      logEvent(payload.message || "ack");
      break;
    case "grid_plan":
      state.plan = payload;
      state.mosaic = null;
      state.results.clear();
      state.cellArtifacts.clear();
      state.imageCache.clear();
      updatePlanUi();
      drawCanvas();
      logEvent(`grid_plan rows=${payload.rows} cols=${payload.cols}`);
      break;
    case "scan_phase":
      el.phaseValue.textContent = payload.phase || "-";
      if (payload.phase === "debounce") el.debounceValue.textContent = `${payload.duration_ms}ms`;
      if (payload.x_mm !== undefined) {
        el.xyReadout.textContent = `${payload.x_mm.toFixed(1)}, ${payload.y_mm.toFixed(1)}`;
      }
      if (payload.row !== undefined) {
        state.currentCell = { row: payload.row, col: payload.col };
        drawCanvas();
      }
      if (payload.phase === "capture_frame" && payload.raw_image && payload.row !== undefined) {
        rememberCellArtifact(payload);
        drawCanvas();
        showPreviewImage(payload.row, payload.col, payload.raw_image, "当前拍摄原图");
      }
      if (payload.phase === "detect_inference" && payload.raw_image && payload.row !== undefined) {
        showPreviewImage(payload.row, payload.col, payload.raw_image, "当前原图");
      }
      logPhase(payload);
      break;
    case "scan_result":
      state.currentCell = { row: payload.row, col: payload.col };
      rememberCellArtifact(payload);
      updateProgress(payload.progress);
      renderDefects(decorateDefects(payload.defects || [], payload), payload.row, payload.col);
      drawCanvas();
      showCellArtifact(payload.row, payload.col);
      logEvent(`result row=${payload.row} col=${payload.col} defects=${(payload.defects || []).length} infer=${payload.inference_ms ? Math.round(payload.inference_ms) : "-"}ms`);
      break;
    case "mosaic_result":
      state.mosaic = payload;
      if (payload.mosaic_raw) cachedArtifactImage(payload.mosaic_raw);
      if (payload.mosaic_annotated) cachedArtifactImage(payload.mosaic_annotated);
      if (payload.defects && payload.defects.length) {
        state.results.set("mosaic", decorateDefects(payload.defects, {
          raw_image: payload.mosaic_raw,
          annotated_image: payload.mosaic_annotated,
          inference_ms: payload.inference_ms,
        }));
      }
      renderDefects([], 0, 0);
      drawCanvas();
      showPreviewImage("整板", "复核", payload.mosaic_annotated || payload.mosaic_raw, "整板拼接复核图");
      logEvent(`mosaic_result cells=${payload.cell_count || 0} defects=${(payload.defects || []).length} stitch=${payload.stitch_ms ? Math.round(payload.stitch_ms) : "-"}ms`);
      break;
    case "mosaic_error":
      setCommandHint(payload.message || payload.error || "整板拼接失败", "warn");
      logEvent(`MOSAIC ERROR: ${payload.message || payload.error || "mosaic_error"}`);
      break;
    case "scan_error":
      if (!payload.request_id || payload.error) {
        state.scanning = false;
        state.backendRunning = false;
        setScanButtons(false);
      }
      el.phaseValue.textContent = "Error";
      setCommandHint(payload.message || payload.error || "检测启动失败", "bad");
      logEvent(`ERROR: ${payload.message || payload.error || "scan_error"}`);
      break;
    case "scan_complete":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      updateProgress(payload.progress);
      if (payload.mosaic && !state.mosaic) {
        state.mosaic = payload.mosaic;
        if (state.mosaic.mosaic_raw) cachedArtifactImage(state.mosaic.mosaic_raw);
        if (state.mosaic.mosaic_annotated) cachedArtifactImage(state.mosaic.mosaic_annotated);
        drawCanvas();
      }
      el.phaseValue.textContent = "Complete";
      setCommandHint("检测完成", "good");
      logEvent(`scan_complete · return_origin=${payload.return_motion || "done"}`);
      break;
    case "scan_stopped":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      updateProgress(payload.progress);
      el.phaseValue.textContent = "Stopped";
      setCommandHint("检测已停止", "neutral");
      logEvent(payload.message || "scan_stopped");
      break;
    case "video_plan":
      state.plan = payload;
      state.results.clear();
      state.cellArtifacts.clear();
      state.mosaic = null;
      state.videoTrail = [];
      updatePlanUi();
      drawCanvas();
      logEvent(`video_plan rows=${payload.rows} speed=${payload.speed_mm_s}mm/s`);
      break;
    case "video_phase":
      el.phaseValue.textContent = payload.phase || "-";
      el.debounceValue.textContent = payload.phase === "continuous_move" ? `${payload.speed_mm_s || "-"}mm/s` : "Video";
      logVideoPhase(payload);
      break;
    case "video_position":
      state.videoPosition = payload;
      state.videoTrail.push({ x_mm: payload.x_mm || 0, y_mm: payload.y_mm || 0 });
      if (state.videoTrail.length > 220) state.videoTrail.shift();
      el.xyReadout.textContent = `${Number(payload.x_mm || 0).toFixed(1)}, ${Number(payload.y_mm || 0).toFixed(1)}`;
      updateProgress(payload.progress);
      cameraPreviewVideoStatus(payload);
      drawCanvas();
      break;
    case "video_detection":
      rememberVideoDetections(payload);
      renderDefects([], 0, 0);
      el.cameraPreviewStatus.textContent = `YOLO ${payload.fps || 0} FPS · frame ${payload.frame_id || 0} · ${Math.round(payload.inference_ms || 0)}ms`;
      logEvent(`video_detection frame=${payload.frame_id} defects=${(payload.defects || []).length} fps=${payload.fps || "-"}`);
      break;
    case "video_error":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Video Error";
      setCommandHint(payload.message || payload.error || "视频检测启动失败", "bad");
      logEvent(`VIDEO ERROR: ${payload.message || payload.error || "video_error"}`);
      break;
    case "video_complete":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Video Complete";
      el.progressBar.style.width = "100%";
      setCommandHint("视频检测完成", "good");
      logEvent(payload.message || "video_complete");
      break;
    case "video_stopped":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Video Stopped";
      setCommandHint("视频检测已停止", "neutral");
      logEvent(payload.message || "video_stopped");
      break;
    case "handheld_phase":
      el.phaseValue.textContent = payload.phase || "-";
      el.debounceValue.textContent = "Handheld";
      setCommandHint(payload.message || "手持检测运行中", "good");
      logEvent(`handheld_${payload.phase || "phase"}`);
      break;
    case "handheld_detection":
      rememberVideoDetections(payload);
      renderDefects([], 0, 0);
      drawCanvas();
      el.cameraPreviewStatus.textContent = `手持 YOLO ${payload.fps || 0} FPS · frame ${payload.frame_id || 0} · ${Math.round(payload.inference_ms || 0)}ms`;
      logEvent(`handheld_detection frame=${payload.frame_id} defects=${(payload.defects || []).length} fps=${payload.fps || "-"}`);
      break;
    case "handheld_error":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Handheld Error";
      setCommandHint(payload.message || payload.error || "手持检测启动失败", "bad");
      logEvent(`HANDHELD ERROR: ${payload.message || payload.error || "handheld_error"}`);
      break;
    case "handheld_stopped":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Handheld Stopped";
      setCommandHint("手持检测已停止", "neutral");
      logEvent(payload.message || "handheld_stopped");
      break;
    case "phone_upload_selected":
      mergePhoneUpload(payload);
      state.selectedPhoneUploadId = payload.upload_id || state.selectedPhoneUploadId;
      state.plan = buildPhonePlan(currentPhoneUpload());
      updatePlanUi();
      updatePreviewSource();
      drawCanvas();
      logEvent(`phone_upload_selected ${payload.upload_id || ""}`);
      break;
    case "phone_detecting":
      el.phaseValue.textContent = "Phone Detecting";
      el.debounceValue.textContent = "Whole image";
      setCommandHint(payload.message || "手机照片整图 YOLO 推理中", "good");
      el.progressBar.style.width = "45%";
      logEvent(`phone_detecting ${payload.upload_id || ""}`);
      break;
    case "phone_result":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      mergePhoneUpload(payload);
      state.selectedPhoneUploadId = payload.upload_id || state.selectedPhoneUploadId;
      state.phoneResult = payload;
      state.plan = buildPhonePlan(Object.assign({}, currentPhoneUpload() || {}, payload));
      state.results.set("phone", decorateDefects(payload.defects || [], payload));
      updatePlanUi();
      renderDefects([], 0, 0);
      el.progressBar.style.width = "100%";
      el.phaseValue.textContent = "Phone Complete";
      setCommandHint("手机照片检测完成", "good");
      showPreviewImage("手机", "整图", payload.annotated_image || payload.raw_image, "手机整图检测结果");
      drawCanvas();
      refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
      logEvent(`phone_result defects=${(payload.defects || []).length} infer=${payload.inference_ms ? Math.round(payload.inference_ms) : "-"}ms`);
      break;
    case "phone_error":
      state.scanning = false;
      state.backendRunning = false;
      setScanButtons(false);
      el.phaseValue.textContent = "Phone Error";
      setCommandHint(payload.message || payload.error || "手机照片检测失败", "bad");
      logEvent(`PHONE ERROR: ${payload.message || payload.error || "phone_error"}`);
      break;
    default:
      logEvent(`packet ${packet.type}`);
  }
}

function logPhase(payload) {
  const rowText = payload.row !== undefined && payload.row !== null ? payload.row : "-";
  const colText = payload.col !== undefined && payload.col !== null ? payload.col : "-";
  const motion = payload.motion ? ` ${payload.motion}` : "";
  const source = payload.source ? ` ${payload.source}` : "";
  const timing = payload.capture_ms ? ` capture=${Math.round(payload.capture_ms)}ms` : "";
  logEvent(`${payload.phase} row=${rowText} col=${colText}${motion}${source}${timing}`);
}

function logVideoPhase(payload) {
  const rowText = payload.row !== undefined && payload.row !== null ? ` row=${payload.row}` : "";
  const target = payload.target_x_mm !== undefined ? ` target_x=${Number(payload.target_x_mm).toFixed(1)}` : "";
  const yText = payload.y_mm !== undefined ? ` y=${Number(payload.y_mm).toFixed(1)}` : "";
  logEvent(`${payload.phase}${rowText}${target}${yText}`);
}

function cameraPreviewVideoStatus(payload) {
  const segment = payload.segment || {};
  if (segment.type === "raster_line") {
    el.cameraPreviewStatus.textContent = `连续扫描 row ${segment.row} · X=${Number(payload.x_mm || 0).toFixed(1)} Y=${Number(payload.y_mm || 0).toFixed(1)}`;
  }
}

function rememberVideoDetections(payload) {
  const defects = decorateDefects(payload.defects || [], payload);
  if (!defects.length) return;
  state.results.set(`frame:${payload.frame_id}`, defects.map((defect) => Object.assign({}, defect, {
    frame_id: payload.frame_id,
    x_mm: payload.x_mm,
    y_mm: payload.y_mm,
  })));
  while (state.results.size > 80) {
    const firstKey = state.results.keys().next().value;
    state.results.delete(firstKey);
  }
}

function setScanButtons(scanning) {
  state.scanning = scanning;
  const busy = scanning || state.backendRunning;
  const videoMode = state.mode === "video";
  const handheldMode = state.mode === "handheld";
  const phoneMode = state.mode === "phone";
  const idleText = handheldMode ? "开始手持检测" : phoneMode ? "开始整图检测" : videoMode ? "开始视频检测" : "开始检测";
  const busyText = handheldMode ? "手持检测中" : phoneMode ? "整图检测中" : videoMode ? "视频检测中" : "检测中";
  el.startBtn.disabled = busy;
  el.startBtn.textContent = busy ? busyText : idleText;
  el.stopBtn.classList.toggle("armed", busy);
  setPhysicalControlsEnabled((state.mode === "real" || state.mode === "video") && !busy);
}

function modePreflight(status) {
  if (!state.mode) {
    return { ok: false, message: "请先选择运行模式" };
  }
  const algorithm = status && status.algorithm ? status.algorithm : {};
  const camera = status && status.camera ? status.camera : {};
  const motor = status && status.motor ? status.motor : {};
  const scan = status && status.scan ? status.scan : {};
  const cameraReady = camera.runtime && camera.runtime.opened;
  const weightsReady = algorithm.weights && algorithm.weights.exists;
  const scriptReady = algorithm.detect_script && algorithm.detect_script.exists;
  const configReady = algorithm.config && algorithm.config.exists;

  if (scan.running && !state.scanning) {
    return { ok: false, message: "已有检测任务运行，请先停止" };
  }
  if (!weightsReady || !scriptReady || !configReady) {
    return { ok: false, message: "YOLO 权重、检测脚本或类别配置未就绪" };
  }
  if ((state.mode === "handheld" || state.mode === "real" || state.mode === "video") && !cameraReady) {
    return { ok: false, message: "相机未打开，无法进入相机抓拍流程" };
  }
  if (state.mode === "simulation" && !state.simulationImagePath) {
    return { ok: false, message: "请先导入一张 PCB 图片" };
  }
  if (state.mode === "phone" && !state.selectedPhoneUploadId) {
    return { ok: false, message: "请先选择一张手机上传照片" };
  }
  if (state.mode === "video") {
    const axes = motor.axes || {};
    const homed = Boolean(axes.x && axes.x.homed && axes.y && axes.y.homed && axes.z && axes.z.homed);
    if (!homed) {
      return { ok: false, message: "视频检测需要先手动移动到物理原点并执行三轴回零" };
    }
  }
  if (state.mode === "real" && !motor.connected) {
    logEvent("真实模式提示：当前滑台状态不是 connected，请确认驱动配置或 dry-run 环境。");
  }
  return { ok: true };
}

async function startScan() {
  const status = await refreshSystemStatus({ log: true });
  const preflight = modePreflight(status);
  if (!preflight.ok) {
    el.phaseValue.textContent = "Preflight";
    setCommandHint(preflight.message, "bad");
    logEvent(`启动被拦截：${preflight.message}`);
    return;
  }

  setScanButtons(true);
  setCommandHint("启动指令已发送，等待后端确认", "good");
  state.results.clear();
  state.cellArtifacts.clear();
  state.mosaic = null;
  state.imageCache.clear();
  state.videoTrail = [];
  state.videoPosition = null;
  state.phoneResult = null;
  el.defectList.innerHTML = "";
  el.progressBar.style.width = "0%";

  if (state.mode === "handheld") {
    state.plan = buildHandheldPlan();
    updatePlanUi();
    drawCanvas();
    updatePreviewSource();
    const sent = send("start_handheld_detection", {
      inference_fps: Number(el.videoInferenceFps.value || 5),
      preview_fps: 12,
    });
    if (!sent) {
      state.backendRunning = false;
      setScanButtons(false);
    }
    return;
  }

  if (state.mode === "phone") {
    const item = currentPhoneUpload();
    state.plan = buildPhonePlan(item);
    updatePlanUi();
    drawCanvas();
    updatePreviewSource();
    const sent = send("start_phone_detection", {
      upload_id: state.selectedPhoneUploadId,
    });
    if (!sent) {
      state.backendRunning = false;
      setScanButtons(false);
    }
    return;
  }

  if (state.mode === "video") {
    updatePreviewSource();
    const sent = send("start_video_scan", {
      speed_mm_s: Number(el.videoSpeed.value || 2),
      inference_fps: Number(el.videoInferenceFps.value || 5),
      preview_fps: 8,
      overlap: Number(el.videoOverlap.value || 20) / 100,
    });
    if (!sent) {
      state.backendRunning = false;
      setScanButtons(false);
    }
    return;
  }

  const imagePath = state.mode === "simulation" ? state.simulationImagePath : el.imagePath.value.trim();
  const sent = send("start_scan", {
    operation_mode: state.mode,
    capture_mode: "real",
    pcb_length_mm: Number(el.pcbLength.value),
    pcb_width_mm: Number(el.pcbWidth.value),
    origin_x_mm: Number(el.originX.value),
    origin_y_mm: Number(el.originY.value),
    image_path: imagePath,
  });
  if (!sent) {
    state.backendRunning = false;
    setScanButtons(false);
  }
}

function selectCellFromCanvas(event) {
  if (!state.plan) return;
  if (state.plan.scan_type === "handheld") {
    el.xyReadout.textContent = "无坐标";
    setCommandHint("手持模式无滑台坐标定位，请观察右侧实时视频", "neutral");
    logEvent("手持模式画板无 XY/FOV 坐标定位");
    return;
  }
  if (state.plan.scan_type === "phone") {
    const item = currentPhoneUpload();
    el.xyReadout.textContent = "无滑台";
    if (item) {
      showPreviewImage("手机", "整图", item.annotated_image || item.raw_image, "手机整图预览");
    }
    setCommandHint("手机拍照模式显示整张照片的检测结果", "neutral");
    return;
  }
  if (state.plan.scan_type === "continuous_video") {
    const rect = el.canvas.getBoundingClientRect();
    const ratioX = el.canvas.width / rect.width;
    const ratioY = el.canvas.height / rect.height;
    const px = (event.clientX - rect.left) * ratioX;
    const py = (event.clientY - rect.top) * ratioY;
    const metrics = videoCanvasMetrics(el.canvas.width, el.canvas.height);
    if (px < metrics.ox || py < metrics.oy || px > metrics.ox + metrics.boardW || py > metrics.oy + metrics.boardH) return;
    const x = (px - metrics.ox) / metrics.scale;
    const y = (py - metrics.oy) / metrics.scale;
    el.xyReadout.textContent = `${x.toFixed(1)}, ${y.toFixed(1)}`;
    logEvent(`查看行程坐标 X=${x.toFixed(1)} Y=${y.toFixed(1)}`);
    return;
  }
  const rect = el.canvas.getBoundingClientRect();
  const ratioX = el.canvas.width / rect.width;
  const ratioY = el.canvas.height / rect.height;
  const px = (event.clientX - rect.left) * ratioX;
  const py = (event.clientY - rect.top) * ratioY;
  const cols = state.plan.cols;
  const rows = state.plan.rows;
  const metrics = steppedCanvasMetrics(el.canvas.width, el.canvas.height);
  if (px < metrics.ox || py < metrics.oy || px > metrics.ox + metrics.boardW || py > metrics.oy + metrics.boardH) return;
  let selected = null;
  for (let rowIndex = rows - 1; rowIndex >= 0; rowIndex -= 1) {
    for (let colIndex = cols - 1; colIndex >= 0; colIndex -= 1) {
      const cellRect = steppedCellRect(metrics, rowIndex, colIndex);
      if (px >= cellRect.x && py >= cellRect.y && px <= cellRect.x + cellRect.w && py <= cellRect.y + cellRect.h) {
        selected = { row: rowIndex, col: colIndex, rect: cellRect };
        break;
      }
    }
    if (selected) break;
  }
  if (!selected) return;
  const { row, col } = selected;
  state.currentCell = { row, col };
  const x = selected.rect.x_mm;
  const y = selected.rect.y_mm;
  el.xyReadout.textContent = `${x.toFixed(1)}, ${y.toFixed(1)}`;
  showCellArtifact(row, col);
  logEvent(`选中区域 row=${row} col=${col} X=${x.toFixed(1)} Y=${y.toFixed(1)}`);
  drawCanvas();
}

function navigateToPanel(button) {
  const targetId = button.getAttribute("data-target");
  const target = document.getElementById(targetId);
  if (!target) return;
  Array.prototype.forEach.call(el.railButtons, (item) => item.classList.remove("active"));
  button.classList.add("active");
  Array.prototype.forEach.call(document.querySelectorAll(".panel-focus"), (item) => {
    item.classList.remove("panel-focus");
  });
  target.classList.add("panel-focus");
  window.setTimeout(() => target.classList.remove("panel-focus"), 1200);
  target.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
}

window.addEventListener("error", (event) => {
  logEvent(`前端脚本错误: ${event.message}`);
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason);
  logEvent(`前端异步错误: ${reason}`);
});

for (const input of [el.pcbLength, el.pcbWidth, el.originX, el.originY]) {
  input.addEventListener("change", previewGrid);
  input.addEventListener("input", () => window.clearTimeout(input._timer));
  input.addEventListener("input", () => {
    input._timer = window.setTimeout(previewGrid, 220);
  });
}

for (const input of [el.videoSpeed, el.videoInferenceFps, el.videoOverlap]) {
  input.addEventListener("change", previewGrid);
  input.addEventListener("input", () => window.clearTimeout(input._timer));
  input.addEventListener("input", () => {
    input._timer = window.setTimeout(previewGrid, 220);
  });
}

Array.prototype.forEach.call(el.modeCards, (card) => {
  card.addEventListener("click", () => chooseMode(card.dataset.mode));
});

el.backToModesBtn.addEventListener("click", returnToModeScreen);
el.pingBtn.addEventListener("click", () => send("ping"));
el.stopBtn.addEventListener("click", () => send("stop_scan"));
el.refreshStatusBtn.addEventListener("click", () => refreshSystemStatus({ log: true }));
el.reloadCameraBtn.addEventListener("click", () => {
  if (state.mode === "phone") {
    refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
  }
  updatePreviewSource();
  refreshSystemStatus({ log: true });
});
el.uploadSimulationBtn.addEventListener("click", uploadSimulationImages);
if (el.uploadPhoneBtn) {
  el.uploadPhoneBtn.addEventListener("click", uploadPhoneImages);
}
if (el.refreshPhoneUploadsBtn) {
  el.refreshPhoneUploadsBtn.addEventListener("click", () => refreshPhoneUploads({ preserveSelection: true, selectLatest: false }));
}
if (el.deletePhoneUploadBtn) {
  el.deletePhoneUploadBtn.addEventListener("click", deleteSelectedPhoneUpload);
}
if (el.phoneUploadSelect) {
  el.phoneUploadSelect.addEventListener("change", () => {
    setPhoneUpload(el.phoneUploadSelect.value);
  });
}
if (el.phoneImageFile) {
  el.phoneImageFile.addEventListener("change", () => {
    const files = Array.from(el.phoneImageFile.files || []);
    el.phoneUploadStatus.textContent = files.length
      ? `已选择 ${files.length} 张手机照片`
      : "等待手机拍照上传";
  });
}
el.simulationImageFile.addEventListener("change", () => {
  const files = Array.from(el.simulationImageFile.files || []);
  el.simulationUploadStatus.textContent = files.length
    ? `已选择 ${files.length} 张图片`
    : "尚未导入图片";
});
if (el.simulationSourceSelect) {
  el.simulationSourceSelect.addEventListener("change", () => {
    setSimulationSource(el.simulationSourceSelect.value);
  });
}
if (el.originalPcbPreview) {
  el.originalPcbPreview.addEventListener("load", () => {
    const item = currentSimulationImage();
    if (item && el.originalPcbStatus) {
      el.originalPcbStatus.textContent = `完整原图已加载：${item.name || item.path}`;
    }
  });
  el.originalPcbPreview.addEventListener("error", () => {
    if (el.originalPcbStatus) {
      el.originalPcbStatus.textContent = "原 PCB 图加载失败";
    }
  });
}
el.cameraPreview.addEventListener("load", () => {
  if (state.previewOverride) {
    el.cameraPreviewStatus.textContent = `区域 ${state.previewOverride.row},${state.previewOverride.col} 图片已加载`;
    return;
  }
  if (state.mode === "simulation") {
    const item = currentSimulationImage();
    el.cameraPreviewStatus.textContent = item ? `模拟图已加载：${item.name || item.path}` : "等待导入 PCB 图片";
  } else if (state.mode === "phone") {
    const item = currentPhoneUpload();
    el.cameraPreviewStatus.textContent = item ? `手机照片已加载：${item.name || item.upload_id}` : "等待手机上传照片";
  } else if (state.mode === "video" || state.mode === "handheld") {
    el.cameraPreviewStatus.textContent = state.mode === "handheld" ? "手持检测流已连接" : "视频检测流已连接";
  } else {
    el.cameraPreviewStatus.textContent = "相机流已连接";
  }
});
el.cameraPreview.addEventListener("error", () => {
  el.cameraPreviewStatus.textContent = state.mode === "simulation"
    ? "模拟图加载失败"
    : state.mode === "phone"
      ? "手机照片加载失败"
    : state.mode === "video" || state.mode === "handheld"
      ? "实时检测流等待启动或连接失败"
      : "相机流连接失败";
});
el.homeBtn.addEventListener("click", () => send("home_all_axes"));
el.startBtn.addEventListener("click", startScan);
el.zUpBtn.addEventListener("click", () => send("z_jog", { direction: "up", dist: Number(el.zDist.value) }));
el.zDownBtn.addEventListener("click", () => send("z_jog", { direction: "down", dist: Number(el.zDist.value) }));
el.canvas.addEventListener("click", selectCellFromCanvas);
Array.prototype.forEach.call(el.railButtons, (button) => {
  button.addEventListener("click", () => navigateToPanel(button));
});
window.addEventListener("resize", drawCanvas);

connectWs();
refreshSystemStatus();
renderDefects([], 0, 0);
