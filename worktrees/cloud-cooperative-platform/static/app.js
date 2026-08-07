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
    description: "真实滑台固定 16 点走停、OV9281 抓拍、逐格推理并自动拼接扫描窗口。",
    previewTitle: "实时视频",
    previewSource: "OV9281 相机",
    motionNote: "当前模式不要求输入 PCB 长宽；请先调好 Z 轴焦距并保持固定，再执行软回原点。",
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

const LLM_MODELS = [
  { id: "qwen3.7-plus", label: "Qwen3.7 Plus" },
  { id: "qwen3.6-flash", label: "Qwen3.6 Flash" },
  { id: "qwen3.7-max", label: "Qwen3.7 Max" },
];

function storedLlmModel() {
  const stored = window.localStorage.getItem("aoiLlmModel") || "qwen3.7-plus";
  return LLM_MODELS.some((item) => item.id === stored) ? stored : "qwen3.7-plus";
}

const state = {
  ws: null,
  plan: null,
  currentCell: null,
  selectedCell: null,
  selectedDefectKey: "",
  selectedDefectKeys: new Set(),
  results: new Map(),
  cellArtifacts: new Map(),
  scanning: false,
  mode: null,
  simulationImagePath: "",
  simulationImages: [],
  phoneUploads: [],
  phoneBatches: [],
  currentPhoneBatchId: window.localStorage.getItem("aoiCurrentPhoneBatchId") || "",
  phoneBatchPollTimer: null,
  selectedPhoneUploadId: "",
  selectedPhoneUploadIds: new Set(),
  phoneDetectionProfile: "precision",
  phoneExperimentProfile: "local_link_sim",
  phoneDetectionStartedAt: 0,
  phoneElapsedMs: null,
  phoneViewport: { scale: 1, offsetX: 0, offsetY: 0 },
  phoneViewportBaseRect: null,
  phonePointer: null,
  suppressCanvasClick: false,
  detectionPlatform: "local_link_sim",
  detectionPlatformsStatus: null,
  detectionQueue: { pending: 0, captured: 0, completed: 0, total: 0, errors: 0 },
  cameraInputMode: "backend",
  browserCameraStream: null,
  browserCameraReady: false,
  phoneWorkerFrame: null,
  phoneResult: null,
  lastStatus: null,
  backendRunning: false,
  previewOverride: null,
  previewPinned: false,
  videoPosition: null,
  videoTrail: [],
  imageCache: new Map(),
  mosaic: null,
  llmReportConfigured: false,
  reportTarget: null,
  reportBusy: false,
  reportMessage: "",
  lastReport: null,
  reportsByTarget: new Map(),
  llmThinkingEnabled: window.localStorage.getItem("aoiLlmThinkingEnabled") === "true",
  llmModel: storedLlmModel(),
  workspaceView: "balanced",
  progressPercent: 0,
  finalDefectCount: 0,
  statusRequestFailed: false,
};

let realSpeedUpdateTimer = null;
let phoneElapsedTimer = null;
let wsRetryTimer = null;
let wsConnectTimer = null;
const diagnosisWorkspaceSubscribers = new Set();

function notifyDiagnosisWorkspace() {
  for (const subscriber of diagnosisWorkspaceSubscribers) {
    try {
      subscriber();
    } catch (error) {
      console.error("diagnosis workspace subscriber failed", error);
    }
  }
}

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
  workbench: document.querySelector(".workbench"),
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
  boardParametersPanel: document.getElementById("boardParametersPanel"),
  imagePath: document.getElementById("imagePath"),
  cameraSourcePanel: document.getElementById("cameraSourcePanel"),
  cameraSourceInput: document.getElementById("cameraSourceInput"),
  cameraSourceStatus: document.getElementById("cameraSourceStatus"),
  applyCameraSourceBtn: document.getElementById("applyCameraSourceBtn"),
  probeCameraSourceBtn: document.getElementById("probeCameraSourceBtn"),
  cameraInputModeInputs: document.querySelectorAll('input[name="cameraInputMode"]'),
  openBrowserCameraBtn: document.getElementById("openBrowserCameraBtn"),
  captureBrowserFrameBtn: document.getElementById("captureBrowserFrameBtn"),
  simulationPanel: document.getElementById("simulationPanel"),
  phonePanel: document.getElementById("phonePanel"),
  phoneUploadUrls: document.getElementById("phoneUploadUrls"),
  phoneUploadQr: document.getElementById("phoneUploadQr"),
  phoneImageFile: document.getElementById("phoneImageFile"),
  uploadPhoneBtn: document.getElementById("uploadPhoneBtn"),
  phoneUploadStatus: document.getElementById("phoneUploadStatus"),
  refreshPhoneUploadsBtn: document.getElementById("refreshPhoneUploadsBtn"),
  deletePhoneUploadBtn: document.getElementById("deletePhoneUploadBtn"),
  phoneUploadHistoryList: document.getElementById("phoneUploadHistoryList"),
  phoneHistorySelectionCount: document.getElementById("phoneHistorySelectionCount"),
  selectAllPhoneHistoryBtn: document.getElementById("selectAllPhoneHistoryBtn"),
  clearPhoneHistorySelectionBtn: document.getElementById("clearPhoneHistorySelectionBtn"),
  detectSelectedPhoneUploadsBtn: document.getElementById("detectSelectedPhoneUploadsBtn"),
  deleteSelectedPhoneUploadsBtn: document.getElementById("deleteSelectedPhoneUploadsBtn"),
  phoneCurrentBatch: document.getElementById("phoneCurrentBatch"),
  detectPhoneBatchBtn: document.getElementById("detectPhoneBatchBtn"),
  deletePhoneBatchBtn: document.getElementById("deletePhoneBatchBtn"),
  phoneProfileInputs: document.querySelectorAll('input[name="phoneDetectionProfile"]'),
  phoneProfileHint: document.getElementById("phoneProfileHint"),
  phoneElapsedValue: document.getElementById("phoneElapsedValue"),
  platformInputs: document.querySelectorAll('input[name="detectionPlatform"]'),
  platformSwitchTitle: document.getElementById("platformSwitchTitle"),
  cloudPlatformLabel: document.getElementById("cloudPlatformLabel"),
  phonePlatformLabel: document.getElementById("phonePlatformLabel"),
  localPlatformLabel: document.getElementById("localPlatformLabel"),
  videoPanel: document.getElementById("videoPanel"),
  videoSpeed: document.getElementById("videoSpeed"),
  videoInferenceFps: document.getElementById("videoInferenceFps"),
  videoOverlap: document.getElementById("videoOverlap"),
  realMotionSpeed: document.getElementById("realMotionSpeed"),
  realSettleSeconds: document.getElementById("realSettleSeconds"),
  videoPanelNote: document.querySelector("#videoPanel .inline-note"),
  simulationImageFile: document.getElementById("simulationImageFile"),
  uploadSimulationBtn: document.getElementById("uploadSimulationBtn"),
  simulationUploadStatus: document.getElementById("simulationUploadStatus"),
  originalPcbPreview: document.getElementById("originalPcbPreview"),
  originalPcbStatus: document.getElementById("originalPcbStatus"),
  originalPcbName: document.getElementById("originalPcbName"),
  cameraPreview: document.getElementById("cameraPreview"),
  cameraPreviewStatus: document.getElementById("cameraPreviewStatus"),
  liveCameraPanel: document.getElementById("liveCameraPanel"),
  liveCameraPreview: document.getElementById("liveCameraPreview"),
  browserCameraPreview: document.getElementById("browserCameraPreview"),
  liveCameraStatus: document.getElementById("liveCameraStatus"),
  previewTitle: document.getElementById("previewTitle"),
  previewSourceText: document.getElementById("previewSourceText"),
  simulationSourceSelect: document.getElementById("simulationSourceSelect"),
  reloadCameraBtn: document.getElementById("reloadCameraBtn"),
  refreshStatusBtn: document.getElementById("refreshStatusBtn"),
  motorStatus: document.getElementById("motorStatus"),
  captureStatus: document.getElementById("captureStatus"),
  weightsStatus: document.getElementById("weightsStatus"),
  scriptStatus: document.getElementById("scriptStatus"),
  cloudInferenceStatus: document.getElementById("cloudInferenceStatus"),
  phoneInferenceStatus: document.getElementById("phoneInferenceStatus"),
  localDnnStatus: document.getElementById("localDnnStatus"),
  llmReportStatus: document.getElementById("llmReportStatus"),
  lanUrls: document.getElementById("lanUrls"),
  zDist: document.getElementById("zDist"),
  rowsValue: document.getElementById("rowsValue"),
  colsValue: document.getElementById("colsValue"),
  tilesValue: document.getElementById("tilesValue"),
  gridMeta: document.getElementById("gridMeta"),
  xyReadout: document.getElementById("xyReadout"),
  phaseValue: document.getElementById("phaseValue"),
  debounceValue: document.getElementById("debounceValue"),
  detectQueueValue: document.getElementById("detectQueueValue"),
  progressBar: document.getElementById("progressBar"),
  eventLog: document.getElementById("eventLog"),
  defectList: document.getElementById("defectList"),
  defectCount: document.getElementById("defectCount"),
  generateReportBtn: document.getElementById("generateReportBtn"),
  downloadReportBtn: document.getElementById("downloadReportBtn"),
  llmThinkingToggle: document.getElementById("llmThinkingToggle"),
  llmModelSelect: document.getElementById("llmModelSelect"),
  selectMosaicReportBtn: document.getElementById("selectMosaicReportBtn"),
  downloadRawFramesBtn: document.getElementById("downloadRawFramesBtn"),
  reportTargetText: document.getElementById("reportTargetText"),
  reportStatus: document.getElementById("reportStatus"),
  diagnosisReport: document.getElementById("diagnosisReport"),
  eventFlowText: document.getElementById("eventFlowText"),
  canvas: document.getElementById("scanCanvas"),
  phoneCanvasTools: document.getElementById("phoneCanvasTools"),
  phoneZoomOutBtn: document.getElementById("phoneZoomOutBtn"),
  phoneZoomInBtn: document.getElementById("phoneZoomInBtn"),
  phoneZoomResetBtn: document.getElementById("phoneZoomResetBtn"),
  phoneZoomValue: document.getElementById("phoneZoomValue"),
  railButtons: document.querySelectorAll(".rail-btn"),
  railControlStatus: document.getElementById("railControlStatus"),
  railScanProgress: document.getElementById("railScanProgress"),
  railDefectCount: document.getElementById("railDefectCount"),
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

function phoneArtifactUrl(item, path) {
  const url = artifactUrl(path);
  if (!url || !item) return url;
  const version = item.detected_at || item.uploaded_at || "";
  return version ? `${url}&v=${encodeURIComponent(version)}` : url;
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

function setWsState(connectionState, detail = "") {
  const online = connectionState === "online";
  const connecting = connectionState === "connecting";
  for (const dot of [el.wsDot, el.modeWsDot]) {
    if (!dot) continue;
    dot.classList.toggle("online", online);
    dot.classList.toggle("connecting", connecting);
  }
  const label = online
    ? "WebSocket 在线"
    : connecting
      ? "WebSocket 连接中…"
      : detail || "WebSocket 连接失败，正在重试";
  if (el.wsText) el.wsText.textContent = label;
  if (el.modeWsText) el.modeWsText.textContent = label;
  refreshRailBadges();
}

function setWsOnline(online) {
  setWsState(online ? "online" : "offline");
}

function currentModeRequirementsReady(status = state.lastStatus) {
  if (!status || !state.mode) return false;
  const platforms = status.detection_platforms || {};
  const cloud = platforms.huawei_cloud || status.cloud_detection || {};
  const platformReady = Boolean(cloud.enabled && cloud.configured);

  if (state.mode !== "real") return platformReady;
  const motor = status.motor || {};
  const cameraRuntime = status.camera && status.camera.runtime ? status.camera.runtime : {};
  const cameraReady = currentCameraInputMode() === "browser"
    ? state.browserCameraReady
    : Boolean(cameraRuntime.opened);
  return Boolean(platformReady && motor.connected && cameraReady);
}

function refreshRailBadges() {
  const websocketOnline = Boolean(state.ws && state.ws.readyState === 1);
  const tone = window.AoiUiHelpers.workspaceStatusTone({
    websocketOnline,
    statusRequestFailed: state.statusRequestFailed,
    statusAvailable: Boolean(state.lastStatus),
    requirementsReady: currentModeRequirementsReady(),
  });
  if (el.railControlStatus) {
    el.railControlStatus.classList.remove("is-good", "is-warn", "is-bad");
    el.railControlStatus.classList.add(`is-${tone}`);
    const labels = {
      good: "当前模式设备与检测平台可用",
      warn: "服务在线，当前模式仍有未就绪项",
      bad: "WebSocket 离线或系统状态请求失败",
    };
    el.railControlStatus.setAttribute("aria-label", labels[tone]);
  }
  if (el.railScanProgress) el.railScanProgress.textContent = `${state.progressPercent}%`;
  if (el.railDefectCount) el.railDefectCount.textContent = String(state.finalDefectCount);
}

function setWorkspaceView(view, options = {}) {
  const requested = window.AoiUiHelpers.normalizeWorkspaceView(view);
  const next = options.toggle
    ? window.AoiUiHelpers.nextWorkspaceView(state.workspaceView, requested)
    : requested;
  state.workspaceView = next;
  if (el.workbench) el.workbench.dataset.workspaceView = next;
  Array.prototype.forEach.call(el.railButtons, (button) => {
    const active = button.dataset.workspaceView === next;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  if (options.redraw === false) return;
  window.requestAnimationFrame(() => drawCanvas());
  window.setTimeout(drawCanvas, 220);
}

function setProgressPercent(value) {
  state.progressPercent = window.AoiUiHelpers.normalizeProgressPercent(value);
  if (el.progressBar) el.progressBar.style.width = `${state.progressPercent}%`;
  refreshRailBadges();
}

function setFinalDefectCount(value) {
  state.finalDefectCount = Math.max(0, Number.isFinite(Number(value)) ? Math.round(Number(value)) : 0);
  if (el.defectCount) el.defectCount.textContent = `${state.finalDefectCount} items`;
  refreshRailBadges();
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
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  if (wsRetryTimer) {
    window.clearTimeout(wsRetryTimer);
    wsRetryTimer = null;
  }
  setWsState("connecting");
  const socketUrl = new URL("/ws/control", window.location.href);
  socketUrl.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  let ws;
  try {
    ws = new WebSocket(socketUrl.href);
  } catch (error) {
    setWsState("offline", "WebSocket 地址无效，正在重试");
    wsRetryTimer = window.setTimeout(connectWs, 1800);
    return;
  }
  state.ws = ws;
  wsConnectTimer = window.setTimeout(() => {
    if (ws.readyState === WebSocket.CONNECTING) ws.close(4000, "connect_timeout");
  }, 7000);

  ws.addEventListener("open", () => {
    if (wsConnectTimer) window.clearTimeout(wsConnectTimer);
    wsConnectTimer = null;
    setWsOnline(true);
    logEvent("WebSocket connected");
  });

  ws.addEventListener("close", () => {
    if (wsConnectTimer) window.clearTimeout(wsConnectTimer);
    wsConnectTimer = null;
    if (state.ws === ws) state.ws = null;
    setWsState("offline");
    state.scanning = false;
    setScanButtons(false);
    logEvent("WebSocket closed, retrying");
    if (!wsRetryTimer) {
      wsRetryTimer = window.setTimeout(() => {
        wsRetryTimer = null;
        connectWs();
      }, 1800);
    }
  });

  ws.addEventListener("error", () => {
    setWsState("offline", "WebSocket 连接异常，正在重试");
    logEvent("WebSocket error");
  });

  ws.addEventListener("message", (event) => {
    const packet = JSON.parse(event.data);
    handlePacket(packet);
  });
}

function chooseMode(mode) {
  if (!MODE_META[mode]) return;
  setWorkspaceView("balanced", { redraw: false });
  state.mode = mode;
  state.detectionPlatform = "local_link_sim";
  state.phoneExperimentProfile = "local_link_sim";
  state.phoneElapsedMs = null;
  resetPhoneViewport(false);
  syncDetectionPlatformUi();
  document.body.dataset.mode = mode;
  el.modeScreen.classList.add("is-hidden");
  el.appShell.classList.remove("is-hidden");
  window.scrollTo({ top: 0, left: 0, behavior: "auto" });
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
  setWorkspaceView("balanced", { redraw: false });
  el.appShell.classList.add("is-hidden");
  el.modeScreen.classList.remove("is-hidden");
}

function updateModeUi() {
  const meta = MODE_META[state.mode] || MODE_META.real;
  el.activeModeBadge.textContent = meta.label;
  el.modeDescription.textContent = meta.description;
  el.previewTitle.textContent = meta.previewTitle;
  el.previewSourceText.textContent = meta.previewSource;
  el.motionModeNote.textContent = meta.motionNote;

  Array.prototype.forEach.call(el.modeCards, (card) => {
    card.classList.toggle("selected", card.dataset.mode === state.mode);
  });
  if (el.simulationPanel) el.simulationPanel.classList.toggle("is-hidden", true);
  if (el.boardParametersPanel) el.boardParametersPanel.classList.toggle("is-hidden", true);
  if (el.cameraSourcePanel) {
    el.cameraSourcePanel.classList.toggle("is-hidden", state.mode !== "real");
  }
  if (el.phonePanel) {
    el.phonePanel.classList.toggle("is-hidden", state.mode !== "phone");
  }
  if (el.simulationSourceSelect) {
    el.simulationSourceSelect.classList.toggle("is-hidden", state.mode !== "simulation");
  }
  el.videoPanel.classList.toggle("is-hidden", true);
  const videoMode = false;
  const handheldMode = false;
  const phoneMode = state.mode === "phone";
  if (el.platformSwitchTitle) el.platformSwitchTitle.textContent = "检测实验档位";
  const realMode = state.mode === "real";
  if (el.pcbLength) el.pcbLength.disabled = true;
  if (el.pcbWidth) el.pcbWidth.disabled = true;
  if (el.originX) el.originX.disabled = true;
  if (el.originY) el.originY.disabled = true;
  if (el.videoSpeed) el.videoSpeed.disabled = handheldMode;
  if (el.videoOverlap) el.videoOverlap.disabled = handheldMode;
  if (el.videoPanelNote) {
    el.videoPanelNote.textContent = handheldMode
      ? "手持检测只使用 YOLO FPS，不移动滑台；画板不提供 XY/FOV 坐标定位。"
      : "默认扫描完整软件行程；启动前请手动移动到物理原点并三轴回零。";
  }
  if (el.phoneCanvasTools) el.phoneCanvasTools.classList.toggle("is-hidden", !phoneMode);
  el.canvas.classList.toggle("phone-interactive", phoneMode);
  if (!phoneMode) el.canvas.classList.remove("is-dragging");
  updatePhoneElapsedDisplay();
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
  setPhysicalControlsEnabled(state.mode === "real" && !state.scanning);
  syncCameraInputModeUi();
  setCommandHint(
    handheldMode
      ? "手持模式可直接启动，不要求滑台回零"
      : phoneMode
        ? "请选择手机上传照片后开始整图检测"
        : realMode
          ? "真实模式将按固定 16 点扫描；请保持 Z 轴固定"
          : "等待运行条件检查",
    "neutral",
  );
  if (phoneMode) {
    updatePhoneProfileUi();
    refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
  }
  refreshRailBadges();
}

function setPhysicalControlsEnabled(enabled) {
  el.homeBtn.disabled = !enabled;
  el.zUpBtn.disabled = !enabled;
  el.zDownBtn.disabled = !enabled;
}

function simulationImageLabel(item) {
  if (!item) return "未选择 Source 图片";
  const size = item.size_bytes ? ` · ${formatBytes(item.size_bytes)}` : "";
  const dimensions = item.width && item.height ? ` · ${item.width}x${item.height}` : "";
  const resized = item.resized ? " · 已缩放" : "";
  return `${item.name || item.path}${dimensions}${size}${resized}`;
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
  const dimensions = item.width && item.height ? ` · ${item.width}x${item.height}` : "";
  const resized = item.resized ? " · 已按板端安全尺寸缩放" : "";
  el.originalPcbStatus.textContent = `完整原图：${item.name || item.path}${dimensions}${resized}`;
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
    el.cameraPreviewStatus.textContent = `已选择 ${simulationImageLabel(item)}`;
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
  const profile = item.detection_profile_label ? ` · ${item.detection_profile_label}` : "";
  const inference = inferenceLocationLabel(item);
  const inferenceText = inference ? ` · ${inference}` : "";
  return `${item.name || item.upload_id}${size}${status}${profile}${inferenceText}`;
}

function currentPhoneUpload() {
  return state.phoneUploads.find((item) => item.upload_id === state.selectedPhoneUploadId) || null;
}

function phoneDetectionProfileLabel(profile = state.phoneDetectionProfile) {
  return profile === "precision" ? "高精模式" : "快速模式";
}

function phoneDetectionProfileHint(profile = state.phoneDetectionProfile) {
  return profile === "precision"
    ? "高精模式会运行多组 tile/imgsz 融合，适合复核，速度较慢。"
    : "快速模式只运行轻量切片，适合需要更快响应时手动选择。";
}

function inferenceLocationLabel(payload = {}) {
  if (payload.detection_platform === "huawei_cloud" || payload.inference_location === "huawei_cloud") {
    return "华为云";
  }
  if (payload.detection_platform === "local_dnn" || payload.inference_location === "local_dnn") {
    return "本地 DNN";
  }
  if (payload.inference_location === "local_fallback" || payload.local_fallback) {
    return "本机兜底";
  }
  if (payload.inference_location === "phone_browser") {
    return "手机推理";
  }
  return payload.inference_location ? String(payload.inference_location) : "";
}

function currentDetectionPlatform() {
  const checked = Array.prototype.find.call(el.platformInputs || [], (input) => input.checked);
  state.detectionPlatform = checked ? checked.value : state.detectionPlatform || "local_link_sim";
  return state.detectionPlatform;
}

function detectionPlatformLabel(platform = currentDetectionPlatform()) {
  return phoneExperimentProfileLabel(phoneExperimentProfileFromPlatform(platform));
}

function syncDetectionPlatformUi() {
  Array.prototype.forEach.call(el.platformInputs || [], (input) => {
    input.checked = input.value === state.detectionPlatform;
  });
  if (el.cloudPlatformLabel) el.cloudPlatformLabel.textContent = "华为云";
  if (el.phonePlatformLabel) el.phonePlatformLabel.textContent = "手机";
  if (el.localPlatformLabel) el.localPlatformLabel.textContent = "本地（龙芯）";
  refreshRailBadges();
}

function phoneExperimentProfileFromPlatform(platform = currentDetectionPlatform()) {
  return window.AoiUiHelpers.phoneExperimentProfileFromPlatform(platform);
}

function currentPhoneExperimentProfile() {
  state.phoneExperimentProfile = phoneExperimentProfileFromPlatform(currentDetectionPlatform());
  return state.phoneExperimentProfile;
}

function phoneExperimentProfileLabel(profile = currentPhoneExperimentProfile()) {
  if (profile === "phone_link_sim") return "手机";
  if (profile === "local_link_sim") return "本地（龙芯）";
  return "华为云";
}

function formatElapsedMs(milliseconds) {
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) return "等待检测";
  return `${(value / 1000).toFixed(2)}s`;
}

function updatePhoneElapsedDisplay(milliseconds = state.phoneElapsedMs) {
  if (!el.phoneElapsedValue) return;
  el.phoneElapsedValue.textContent = formatElapsedMs(milliseconds);
}

function startPhoneElapsedTimer() {
  if (phoneElapsedTimer) window.clearInterval(phoneElapsedTimer);
  state.phoneDetectionStartedAt = window.performance.now();
  state.phoneElapsedMs = 0;
  updatePhoneElapsedDisplay(0);
  phoneElapsedTimer = window.setInterval(() => {
    state.phoneElapsedMs = window.performance.now() - state.phoneDetectionStartedAt;
    updatePhoneElapsedDisplay();
  }, 100);
}

function stopPhoneElapsedTimer(fallbackMilliseconds = null) {
  if (phoneElapsedTimer) {
    window.clearInterval(phoneElapsedTimer);
    phoneElapsedTimer = null;
  }
  if (state.phoneDetectionStartedAt > 0) {
    state.phoneElapsedMs = window.performance.now() - state.phoneDetectionStartedAt;
  } else if (Number.isFinite(Number(fallbackMilliseconds))) {
    state.phoneElapsedMs = Number(fallbackMilliseconds);
  }
  state.phoneDetectionStartedAt = 0;
  updatePhoneElapsedDisplay();
  return state.phoneElapsedMs;
}

function resetPhoneViewport(redraw = true) {
  state.phoneViewport = { scale: 1, offsetX: 0, offsetY: 0 };
  state.phoneViewportBaseRect = null;
  state.phonePointer = null;
  state.suppressCanvasClick = false;
  el.canvas.classList.remove("is-dragging");
  if (el.phoneZoomValue) el.phoneZoomValue.textContent = "100%";
  if (redraw && state.mode === "phone") drawCanvas();
}

function maybeStartPhoneWorkerForSelectedPlatform() {
  return null;
}

function currentCameraInputMode() {
  const checked = Array.prototype.find.call(el.cameraInputModeInputs || [], (input) => input.checked);
  state.cameraInputMode = checked ? checked.value : state.cameraInputMode || "backend";
  return state.cameraInputMode;
}

function syncCameraInputModeUi() {
  const mode = currentCameraInputMode();
  const browserMode = mode === "browser";
  if (el.cameraSourceInput) el.cameraSourceInput.disabled = browserMode;
  if (el.applyCameraSourceBtn) el.applyCameraSourceBtn.disabled = browserMode;
  if (el.probeCameraSourceBtn) el.probeCameraSourceBtn.disabled = browserMode;
  if (el.openBrowserCameraBtn) el.openBrowserCameraBtn.disabled = !browserMode;
  if (el.captureBrowserFrameBtn) el.captureBrowserFrameBtn.disabled = !browserMode || !state.browserCameraReady;
  updateLiveCameraPreview();
  refreshRailBadges();
}

function ensurePhoneWorkerFrame() {
  return null;
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForPhoneWorkerReady(timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  let status = await refreshSystemStatus({ log: false });
  while (Date.now() < deadline) {
    const phone = status && (status.phone_inference || (status.detection_platforms && status.detection_platforms.phone_browser));
    if (phone && phone.ready) return status;
    await delay(500);
    status = await refreshSystemStatus({ log: false });
  }
  return status;
}

function currentPhoneDetectionProfile() {
  const checked = Array.prototype.find.call(el.phoneProfileInputs || [], (input) => input.checked);
  state.phoneDetectionProfile = checked ? checked.value : state.phoneDetectionProfile || "precision";
  return state.phoneDetectionProfile;
}

function updatePhoneProfileUi() {
  const profile = currentPhoneDetectionProfile();
  if (el.phoneProfileHint) {
    el.phoneProfileHint.textContent = phoneDetectionProfileHint(profile);
  }
  if (state.mode === "phone") {
    setCommandHint(`当前为${phoneDetectionProfileLabel(profile)}，请选择手机上传照片后开始整图检测`, "neutral");
  }
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
  const values = urls.length ? urls : [`${window.location.origin}/phone`];
  for (const url of values) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = url;
    el.phoneUploadUrls.append(link);
  }
  renderPhoneUploadQr(values);
}

function renderPhoneUploadQr(urls = []) {
  if (!el.phoneUploadQr) return;
  el.phoneUploadQr.innerHTML = "";
  const targetUrl = window.AoiUiHelpers.preferredPhoneUploadUrl(urls, window.location.origin);
  if (!targetUrl || typeof window.qrcode !== "function") {
    const message = document.createElement("span");
    message.textContent = "二维码暂不可用，请点击上方网址";
    el.phoneUploadQr.append(message);
    return;
  }
  try {
    const qr = window.qrcode(0, "M");
    qr.addData(targetUrl);
    qr.make();
    el.phoneUploadQr.innerHTML = qr.createSvgTag(4, 4, "手机上传二维码", "扫码打开手机上传页");
    el.phoneUploadQr.dataset.url = targetUrl;
    el.phoneUploadQr.setAttribute("aria-label", `扫码打开 ${targetUrl}`);
  } catch (_error) {
    const message = document.createElement("span");
    message.textContent = "二维码生成失败，请点击上方网址";
    el.phoneUploadQr.append(message);
  }
}

function renderPhoneUploads() {
  const groups = window.AoiUiHelpers.partitionPhoneUploads(state.phoneUploads, state.currentPhoneBatchId);
  const validUploadIds = new Set(state.phoneUploads.map((item) => item.upload_id));
  state.selectedPhoneUploadIds = new Set(
    Array.from(state.selectedPhoneUploadIds).filter((uploadId) => validUploadIds.has(uploadId)),
  );
  renderPhoneUploadHistory(groups.history);
  if (el.deletePhoneUploadBtn) el.deletePhoneUploadBtn.disabled = !currentPhoneUpload();
  renderPhoneCurrentBatch();
  notifyDiagnosisWorkspace();
}

function selectedPhoneUploadIds() {
  const validUploadIds = new Set(state.phoneUploads.map((item) => item.upload_id));
  return window.AoiUiHelpers.normalizePhoneUploadSelection(Array.from(state.selectedPhoneUploadIds))
    .filter((uploadId) => validUploadIds.has(uploadId));
}

function renderPhoneUploadHistory(historyItems) {
  const items = Array.isArray(historyItems) ? historyItems : [];
  const selectedIds = new Set(selectedPhoneUploadIds());
  if (el.phoneUploadHistoryList) {
    el.phoneUploadHistoryList.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "inline-note";
      empty.textContent = "暂无历史上传照片";
      el.phoneUploadHistoryList.append(empty);
    }
    for (const item of items) {
      const row = document.createElement("div");
      row.className = `phone-history-card${item.upload_id === state.selectedPhoneUploadId ? " active" : ""}`;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selectedIds.has(item.upload_id);
      checkbox.setAttribute("aria-label", `选择 ${item.name || item.upload_id}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) state.selectedPhoneUploadIds.add(item.upload_id);
        else state.selectedPhoneUploadIds.delete(item.upload_id);
        renderPhoneUploads();
      });
      const preview = document.createElement("button");
      preview.type = "button";
      preview.className = "phone-history-preview";
      preview.addEventListener("click", () => setPhoneUpload(item.upload_id));
      const image = document.createElement("img");
      image.alt = item.name || item.upload_id;
      if (item.annotated_image || item.raw_image) {
        image.src = phoneArtifactUrl(item, item.annotated_image || item.raw_image);
      }
      const body = document.createElement("span");
      body.className = "phone-batch-card-body";
      const title = document.createElement("strong");
      title.textContent = item.name || item.upload_id;
      const status = document.createElement("small");
      status.textContent = phoneUploadLabel(item);
      body.append(title, status);
      preview.append(image, body);
      row.append(checkbox, preview);
      el.phoneUploadHistoryList.append(row);
    }
  }
  const selectedCount = selectedIds.size;
  const detectionError = window.AoiUiHelpers.phoneBatchSelectionError(selectedCount);
  if (el.phoneHistorySelectionCount) {
    el.phoneHistorySelectionCount.textContent = selectedCount > 9
      ? `已选 ${selectedCount} 张 · 批量检测最多 9 张`
      : `已选 ${selectedCount} 张`;
  }
  if (el.selectAllPhoneHistoryBtn) el.selectAllPhoneHistoryBtn.disabled = !items.length || selectedCount === items.length;
  if (el.clearPhoneHistorySelectionBtn) el.clearPhoneHistorySelectionBtn.disabled = selectedCount < 1;
  if (el.detectSelectedPhoneUploadsBtn) el.detectSelectedPhoneUploadsBtn.disabled = Boolean(detectionError);
  if (el.deleteSelectedPhoneUploadsBtn) el.deleteSelectedPhoneUploadsBtn.disabled = selectedCount < 1;
}

function currentPhoneBatch() {
  return state.phoneBatches.find((batch) => batch.batch_id === state.currentPhoneBatchId) || null;
}

function phoneBatchStatusLabel(status) {
  const labels = {
    uploading: "上传中",
    ready: "待检测",
    queued: "排队中",
    detecting: "检测中",
    done: "已完成",
    partial_failed: "部分失败",
    interrupted: "已中断",
    uploaded: "待检测",
    detected: "已完成",
    detection_error: "检测失败",
    upload_error: "上传失败",
  };
  return labels[String(status || "")] || String(status || "待上传");
}

function renderPhoneCurrentBatch() {
  if (!el.phoneCurrentBatch) return;
  const batch = currentPhoneBatch();
  el.phoneCurrentBatch.innerHTML = "";
  const running = Boolean(batch && ["queued", "detecting"].includes(batch.status));
  const items = batch && Array.isArray(batch.items) ? batch.items : [];
  if (el.detectPhoneBatchBtn) {
    el.detectPhoneBatchBtn.disabled = !items.length || running;
    el.detectPhoneBatchBtn.textContent = running ? "批次检测中" : "检测本次全部";
  }
  if (el.deletePhoneBatchBtn) el.deletePhoneBatchBtn.disabled = !batch || running;
  if (!batch) {
    const empty = document.createElement("p");
    empty.className = "inline-note";
    empty.textContent = "本次尚未上传照片";
    el.phoneCurrentBatch.append(empty);
    return;
  }

  const summary = document.createElement("div");
  summary.className = "phone-batch-summary";
  summary.textContent = `本次 ${items.length}/9 张 · ${phoneBatchStatusLabel(batch.status)} · 已完成 ${batch.completed_count || 0}`;
  el.phoneCurrentBatch.append(summary);
  for (const item of items) {
    const row = document.createElement("div");
    row.className = `phone-history-card phone-batch-selection-card${item.upload_id === state.selectedPhoneUploadId ? " active" : ""}`;
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(item.upload_id && state.selectedPhoneUploadIds.has(item.upload_id));
    checkbox.disabled = !item.upload_id;
    checkbox.setAttribute("aria-label", `选择 ${item.name || item.upload_id || "批次照片"}`);
    checkbox.addEventListener("change", () => {
      if (!item.upload_id) return;
      if (checkbox.checked) state.selectedPhoneUploadIds.add(item.upload_id);
      else state.selectedPhoneUploadIds.delete(item.upload_id);
      renderPhoneUploads();
    });
    const card = document.createElement("button");
    card.type = "button";
    card.className = "phone-batch-card";
    card.disabled = !item.upload_id;
    if (item.upload_id) card.addEventListener("click", () => setPhoneUpload(item.upload_id));
    const image = document.createElement("img");
    image.alt = item.name || `批次图片 ${Number(item.batch_index || 0) + 1}`;
    if (item.annotated_image || item.raw_image) image.src = phoneArtifactUrl(item, item.annotated_image || item.raw_image);
    const body = document.createElement("span");
    body.className = "phone-batch-card-body";
    const title = document.createElement("strong");
    title.textContent = `${Number(item.batch_index || 0) + 1}. ${item.name || item.upload_id}`;
    const status = document.createElement("small");
    const defectText = item.status === "detected" ? ` · 缺陷 ${(item.defects || []).length}` : "";
    status.textContent = `${phoneBatchStatusLabel(item.status)}${defectText}${item.error ? ` · ${item.error}` : ""}`;
    body.append(title, status);
    card.append(image, body);
    row.append(checkbox, card);
    el.phoneCurrentBatch.append(row);
  }
}

function setPhoneUpload(uploadId, options = {}) {
  const item = state.phoneUploads.find((entry) => entry.upload_id === uploadId) || null;
  state.selectedPhoneUploadId = item ? item.upload_id : "";
  state.phoneResult = item && item.annotated_image ? item : null;
  state.phoneElapsedMs = item && Number.isFinite(Number(item.total_elapsed_ms))
    ? Number(item.total_elapsed_ms)
    : null;
  resetPhoneViewport(false);
  updatePhoneElapsedDisplay();
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
    if (item && item.result_json) {
      setReportTarget({ target_type: "phone", upload_id: item.upload_id }, `手机整图结果 · 缺陷 ${(item.defects || []).length} 项`);
    } else {
      setReportTarget(null, "请选择已完成检测的手机照片");
    }
    drawCanvas();
  }
  if (item && options.log !== false) {
    logEvent(`手机照片已选择 ${item.name || item.upload_id}`);
  }
  return Boolean(item);
}

async function refreshPhoneBatches(options = {}) {
  try {
    const response = await fetch("/api/phone/batches");
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `phone batches ${response.status}`);
    }
    const payload = await response.json();
    state.phoneBatches = Array.isArray(payload.batches) ? payload.batches : [];
    const batch = currentPhoneBatch();
    if (batch && Array.isArray(batch.items)) {
      for (const item of batch.items) mergePhoneUpload(item);
    }
    renderPhoneUploads();
    schedulePhoneBatchPoll();
    return state.phoneBatches;
  } catch (error) {
    if (!options.quiet) {
      const message = error && error.message ? error.message : String(error);
      el.phoneUploadStatus.textContent = `批次读取失败：${message}`;
      logEvent(`phone batches failed: ${message}`);
    }
    return [];
  }
}

function schedulePhoneBatchPoll() {
  if (state.phoneBatchPollTimer) window.clearTimeout(state.phoneBatchPollTimer);
  state.phoneBatchPollTimer = null;
  const batch = currentPhoneBatch();
  if (!batch || !["queued", "detecting"].includes(batch.status)) return;
  state.phoneBatchPollTimer = window.setTimeout(async () => {
    await refreshPhoneUploads({ preserveSelection: true, selectLatest: false, quiet: true });
  }, 1000);
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
    const previous = state.selectedPhoneUploadId;
    state.phoneUploads = Array.isArray(payload.uploads) ? payload.uploads : [];
    await refreshPhoneBatches({ quiet: true });
    const previousIsValid = previous && state.phoneUploads.some((item) => item.upload_id === previous);
    const selectLatest = options.selectLatest === true;
    const targetUploadId = selectLatest
      ? window.AoiUiHelpers.latestPhoneUploadId(state.phoneUploads)
      : options.preserveSelection && previousIsValid
        ? previous
        : previousIsValid
          ? previous
          : window.AoiUiHelpers.latestPhoneUploadId(state.phoneUploads);
    setPhoneUpload(targetUploadId, { log: false });
    if (!options.quiet) el.phoneUploadStatus.textContent = state.phoneUploads.length
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
  const selectionError = window.AoiUiHelpers.phoneBatchSelectionError(files.length);
  if (selectionError) {
    el.phoneUploadStatus.textContent = selectionError;
    return;
  }

  el.uploadPhoneBtn.disabled = true;
  const uploaded = [];
  const failed = [];
  try {
    const batchResponse = await fetch("/api/phone/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "desktop_web" }),
    });
    if (!batchResponse.ok) {
      const err = await batchResponse.json().catch(() => ({}));
      throw new Error(err.detail || `create batch ${batchResponse.status}`);
    }
    const batch = await batchResponse.json();
    state.currentPhoneBatchId = batch.batch_id;
    window.localStorage.setItem("aoiCurrentPhoneBatchId", batch.batch_id);
    state.phoneBatches.unshift(batch);
    renderPhoneCurrentBatch();
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      try {
        el.phoneUploadStatus.textContent = `正在转灰度并压缩 ${index + 1}/${files.length}：${file.name}`;
        const prepared = await compressImageForUpload(file);
        el.phoneUploadStatus.textContent = prepared.grayscale
          ? `已转灰度 ${formatBytes(prepared.originalSize)} → ${formatBytes(prepared.uploadSize)}，正在上传 ${index + 1}/${files.length}`
          : `正在上传 ${index + 1}/${files.length}：${file.name}`;
        const query = new URLSearchParams({
          filename: prepared.filename,
          batch_id: batch.batch_id,
          batch_index: String(index),
        });
        const response = await fetch(`/api/phone/upload?${query.toString()}`, {
          method: "POST",
          headers: { "Content-Type": prepared.blob.type || file.type || "application/octet-stream" },
          body: prepared.blob,
        });
        if (!response.ok) {
          const err = await response.json().catch(() => ({}));
          throw new Error(err.detail || `upload ${response.status}`);
        }
        const item = await response.json();
        uploaded.push(item);
        mergePhoneUpload(item);
      } catch (error) {
        const message = error && error.message ? error.message : String(error);
        failed.push({ index, name: file.name, error: message });
        await fetch(`/api/phone/batches/${encodeURIComponent(batch.batch_id)}/upload-error`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ batch_index: index, filename: file.name, error: message }),
        }).catch(() => null);
      }
    }
    await refreshPhoneUploads({ selectLatest: true });
    if (uploaded.length) {
      setPhoneUpload(uploaded[0].upload_id, { log: true });
    }
    el.phoneUploadStatus.textContent = failed.length
      ? `本次上传完成：成功 ${uploaded.length} 张，失败 ${failed.length} 张`
      : `本次 ${uploaded.length} 张照片上传完成，可检测本次全部`;
    if (failed.length) logEvent(`phone batch upload partial_failed ${failed.map((item) => `${item.name}: ${item.error}`).join("; ")}`);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `上传失败：${message}`;
    logEvent(`phone upload failed: ${message}`);
  } finally {
    el.uploadPhoneBtn.disabled = false;
    if (el.phoneImageFile) el.phoneImageFile.value = "";
  }
}

async function detectCurrentPhoneBatch() {
  const batch = currentPhoneBatch();
  if (!batch || !(batch.items || []).length) {
    el.phoneUploadStatus.textContent = "本次批次没有可检测照片";
    return;
  }
  el.detectPhoneBatchBtn.disabled = true;
  try {
    const response = await fetch(`/api/phone/batches/${encodeURIComponent(batch.batch_id)}/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        detection_profile: state.phoneDetectionProfile,
        phone_experiment_profile: currentPhoneExperimentProfile(),
      }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `batch detect ${response.status}`);
    }
    const updated = await response.json();
    state.phoneBatches = state.phoneBatches.map((item) => item.batch_id === updated.batch_id ? updated : item);
    renderPhoneCurrentBatch();
    schedulePhoneBatchPoll();
    el.phoneUploadStatus.textContent = `本次 ${updated.upload_count || batch.items.length} 张照片已进入顺序检测`;
    logEvent(`phone batch detecting ${batch.batch_id}`);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `批量检测启动失败：${message}`;
    logEvent(`phone batch detect failed: ${message}`);
    renderPhoneCurrentBatch();
  }
}

async function deleteCurrentPhoneBatch() {
  const batch = currentPhoneBatch();
  if (!batch) return;
  const count = batch.upload_count || (batch.items || []).length;
  const prompt = batch.owns_uploads === false
    ? `删除当前引用检测批次？其中 ${count} 张原始上传照片会保留。`
    : `删除本次批次及其中 ${count} 张照片？`;
  if (!window.confirm(prompt)) return;
  el.deletePhoneBatchBtn.disabled = true;
  try {
    const response = await fetch(`/api/phone/batches/${encodeURIComponent(batch.batch_id)}`, { method: "DELETE" });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `delete batch ${response.status}`);
    }
    state.currentPhoneBatchId = "";
    window.localStorage.removeItem("aoiCurrentPhoneBatchId");
    state.selectedPhoneUploadId = "";
    await refreshPhoneUploads({ selectLatest: true });
    el.phoneUploadStatus.textContent = "本次批次已删除";
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `批次删除失败：${message}`;
    renderPhoneCurrentBatch();
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

function selectAllPhoneHistoryUploads() {
  const history = window.AoiUiHelpers.partitionPhoneUploads(state.phoneUploads, state.currentPhoneBatchId).history;
  state.selectedPhoneUploadIds = new Set(history.map((item) => item.upload_id));
  renderPhoneUploads();
}

function clearPhoneHistorySelection() {
  state.selectedPhoneUploadIds.clear();
  renderPhoneUploads();
}

async function detectSelectedPhoneUploads() {
  const uploadIds = selectedPhoneUploadIds();
  const selectionError = window.AoiUiHelpers.phoneBatchSelectionError(uploadIds.length);
  if (selectionError) {
    el.phoneUploadStatus.textContent = selectionError;
    return;
  }
  el.detectSelectedPhoneUploadsBtn.disabled = true;
  try {
    const createResponse = await fetch("/api/phone/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "desktop_selection", upload_ids: uploadIds }),
    });
    if (!createResponse.ok) {
      const err = await createResponse.json().catch(() => ({}));
      throw new Error(err.detail || `create selection batch ${createResponse.status}`);
    }
    const batch = await createResponse.json();
    state.currentPhoneBatchId = batch.batch_id;
    window.localStorage.setItem("aoiCurrentPhoneBatchId", batch.batch_id);
    state.phoneBatches = [batch, ...state.phoneBatches.filter((item) => item.batch_id !== batch.batch_id)];
    renderPhoneUploads();
    await detectCurrentPhoneBatch();
    el.phoneUploadStatus.textContent = `已选择 ${uploadIds.length} 张历史照片，正在顺序重新检测`;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `批量检测启动失败：${message}`;
    logEvent(`phone selection batch failed: ${message}`);
    renderPhoneUploads();
  }
}

async function deleteSelectedPhoneUploads() {
  const uploadIds = selectedPhoneUploadIds();
  if (!uploadIds.length) return;
  const names = uploadIds
    .map((uploadId) => state.phoneUploads.find((item) => item.upload_id === uploadId))
    .filter(Boolean)
    .map((item) => item.name || item.upload_id);
  const preview = names.slice(0, 4).join("、");
  const remainder = names.length > 4 ? ` 等 ${names.length} 张` : "";
  if (!window.confirm(`删除已选历史照片：${preview}${remainder}？此操作不可撤销。`)) return;
  el.deleteSelectedPhoneUploadsBtn.disabled = true;
  try {
    const response = await fetch("/api/phone/uploads/delete-selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ upload_ids: uploadIds }),
    });
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || `delete selection ${response.status}`);
    }
    const result = await response.json();
    state.selectedPhoneUploadIds.clear();
    await refreshPhoneUploads({ selectLatest: true });
    el.phoneUploadStatus.textContent = `已删除 ${result.deleted_count || uploadIds.length} 张历史照片`;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phoneUploadStatus.textContent = `批量删除失败：${message}`;
    logEvent(`phone selection delete failed: ${message}`);
    renderPhoneUploads();
  }
}

function updateLiveCameraPreview() {
  const showLive = state.mode === "real";
  if (!el.liveCameraPanel || !el.liveCameraPreview) return;
  el.liveCameraPanel.classList.toggle("is-hidden", !showLive);
  if (!showLive) {
    el.liveCameraPreview.removeAttribute("src");
    el.liveCameraPreview.classList.remove("is-hidden");
    if (el.browserCameraPreview) el.browserCameraPreview.classList.add("is-hidden");
    return;
  }
  if (currentCameraInputMode() === "browser") {
    el.liveCameraPreview.removeAttribute("src");
    el.liveCameraPreview.classList.add("is-hidden");
    if (el.browserCameraPreview) {
      el.browserCameraPreview.classList.remove("is-hidden");
    }
    if (el.liveCameraStatus) {
      el.liveCameraStatus.textContent = state.browserCameraReady ? "浏览器相机已打开" : "等待打开浏览器相机";
    }
    return;
  }
  if (el.browserCameraPreview) el.browserCameraPreview.classList.add("is-hidden");
  el.liveCameraPreview.classList.remove("is-hidden");
  if (!el.liveCameraPreview.getAttribute("src")) {
    el.liveCameraPreview.src = `/api/camera/stream.mjpg?ts=${Date.now()}`;
  }
  if (el.liveCameraStatus) {
    el.liveCameraStatus.textContent = "连接中";
  }
}

function updatePreviewSource() {
  state.previewOverride = null;
  updateLiveCameraPreview();
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
      el.cameraPreview.src = phoneArtifactUrl(item, imagePath);
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
  updateLiveCameraPreview();
  el.previewTitle.textContent = "检测结果预览";
  el.previewSourceText.textContent = "逐格照片 / 标注图";
  el.reloadCameraBtn.textContent = "刷新";
  const currentSrc = el.cameraPreview.getAttribute("src") || "";
  if (!currentSrc || currentSrc.includes("/api/camera/stream.mjpg") || currentSrc.includes("/api/video/stream.mjpg")) {
    el.cameraPreview.removeAttribute("src");
  }
  el.cameraPreviewStatus.textContent = "等待拍照检测结果，或点击 PCB 画板中的已检测区域";
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
        width: payload.width,
        height: payload.height,
        original_width: payload.original_width,
        original_height: payload.original_height,
        resized: Boolean(payload.resized),
        board_safe_mode: Boolean(payload.board_safe_mode),
        max_dimension: payload.max_dimension,
        preprocess: payload.preprocess || [],
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
      const resizedCount = uploaded.filter((item) => item.resized).length;
      const resizedText = resizedCount ? `，${resizedCount} 张已缩放到安全尺寸` : "";
      el.simulationUploadStatus.textContent = `已导入 ${uploaded.length} 张图片${resizedText}，可在 Source 区选择`;
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

  if (state.mode === "real") {
    const params = new URLSearchParams({
      origin_x_mm: el.originX.value,
      origin_y_mm: el.originY.value,
    });
    try {
      const response = await fetch(`/api/grid/fixed16-preview?${params}`);
      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || "fixed grid preview failed");
      }
      state.plan = await response.json();
      state.results.clear();
      updatePlanUi();
      drawCanvas();
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      el.gridMeta.textContent = message;
      logEvent(`固定 16 点预览失败: ${message}`);
    }
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
    state.statusRequestFailed = false;
    state.lastStatus = status;
    renderSystemStatus(status);
    if (options.log) {
      logEvent("系统状态已刷新");
    }
    return status;
  } catch (error) {
    state.statusRequestFailed = true;
    const message = error && error.message ? error.message : String(error);
    setStatusText(el.motorStatus, "状态接口异常", "bad");
    setStatusText(el.captureStatus, "状态接口异常", "bad");
    setStatusText(el.weightsStatus, "状态接口异常", "bad");
    setStatusText(el.scriptStatus, "状态接口异常", "bad");
    if (el.llmReportStatus) {
      setStatusText(el.llmReportStatus, "状态接口异常", "bad");
    }
    refreshRailBadges();
    logEvent(`系统状态刷新失败: ${message}`);
    return null;
  }
}

async function applyCameraSource() {
  if (!el.cameraSourceInput || !el.applyCameraSourceBtn) return;
  const source = (el.cameraSourceInput.value || "").trim();
  if (!source) {
    el.cameraSourceStatus.textContent = "请输入相机编号或设备路径，例如 0、1、/dev/video0";
    return;
  }
  el.applyCameraSourceBtn.disabled = true;
  el.cameraSourceStatus.textContent = `正在切换相机源 ${source}...`;
  try {
    const response = await fetch("/api/camera/source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `camera source ${response.status}`);
    }
    const overrideText = payload.env_override ? "；当前环境变量仍优先" : "";
    el.cameraSourceStatus.textContent = `已保存 ${payload.requested_source}，当前生效 ${payload.active_source}${overrideText}`;
    logEvent(`相机源已设置为 ${payload.requested_source}`);
    state.previewOverride = null;
    await refreshSystemStatus({ log: true });
    updateLiveCameraPreview();
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.cameraSourceStatus.textContent = `相机源设置失败：${message}`;
    logEvent(`相机源设置失败: ${message}`);
  } finally {
    el.applyCameraSourceBtn.disabled = false;
  }
}

async function probeCameraSource() {
  if (el.cameraSourceStatus) {
    el.cameraSourceStatus.textContent = "正在探测相机...";
  }
  try {
    const response = await fetch("/api/camera/status?probe=true");
    const payload = await response.json();
    state.lastStatus = state.lastStatus || {};
    state.lastStatus.camera = state.lastStatus.camera || {};
    state.lastStatus.camera.runtime = payload;
    if (el.cameraSourceInput && document.activeElement !== el.cameraSourceInput) {
      el.cameraSourceInput.value = payload.source || "";
    }
    if (el.cameraSourceStatus) {
      el.cameraSourceStatus.textContent = payload.opened
        ? `相机已打开：${payload.source} · ${payload.width}x${payload.height}`
        : `相机不可用：${payload.last_error || payload.source || "unknown"}`;
    }
    await refreshSystemStatus({ log: true });
    updateLiveCameraPreview();
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    if (el.cameraSourceStatus) {
      el.cameraSourceStatus.textContent = `探测失败：${message}`;
    }
    logEvent(`相机探测失败: ${message}`);
  }
}

async function openBrowserCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (el.cameraSourceStatus) el.cameraSourceStatus.textContent = "当前浏览器不支持 getUserMedia，无法打开浏览器相机";
    return;
  }
  try {
    if (state.browserCameraStream) {
      state.browserCameraStream.getTracks().forEach((track) => track.stop());
      state.browserCameraStream = null;
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        width: { ideal: 1280 },
        height: { ideal: 800 },
        facingMode: "environment",
      },
      audio: false,
    });
    state.browserCameraStream = stream;
    state.browserCameraReady = true;
    if (el.browserCameraPreview) {
      el.browserCameraPreview.srcObject = stream;
      el.browserCameraPreview.classList.remove("is-hidden");
    }
    if (el.liveCameraPreview) {
      el.liveCameraPreview.removeAttribute("src");
      el.liveCameraPreview.classList.add("is-hidden");
    }
    if (el.liveCameraStatus) el.liveCameraStatus.textContent = "浏览器相机已打开";
    if (el.cameraSourceStatus) el.cameraSourceStatus.textContent = "浏览器相机已打开；点击“拍照检测”会按当前检测平台推理";
    syncCameraInputModeUi();
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    state.browserCameraReady = false;
    if (el.cameraSourceStatus) el.cameraSourceStatus.textContent = `浏览器相机打开失败：${message}`;
    if (el.liveCameraStatus) el.liveCameraStatus.textContent = "浏览器相机不可用";
    logEvent(`browser_camera_open_failed: ${message}`);
    syncCameraInputModeUi();
  }
}

function captureBrowserCameraBlob() {
  return new Promise((resolve, reject) => {
    const video = el.browserCameraPreview;
    if (!video || !state.browserCameraReady || !video.videoWidth || !video.videoHeight) {
      reject(new Error("浏览器相机尚未就绪"));
      return;
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("浏览器相机帧编码失败"));
        return;
      }
      resolve(blob);
    }, "image/jpeg", 0.9);
  });
}

async function captureBrowserFrameDetection() {
  if (currentCameraInputMode() !== "browser") {
    setCommandHint("请先把相机输入源切换为浏览器相机", "warn");
    return;
  }
  if (!state.browserCameraReady) {
    await openBrowserCamera();
  }
  if (!state.browserCameraReady) return;

  const experimentProfile = currentPhoneExperimentProfile();

  setScanButtons(true);
  state.scanning = true;
  state.backendRunning = true;
  state.results.clear();
  state.cellArtifacts.clear();
  state.mosaic = null;
  state.phoneResult = null;
  el.defectList.innerHTML = "";
  setFinalDefectCount(0);
  setCommandHint("浏览器相机拍照检测中", "good");
  el.phaseValue.textContent = "Browser Capture";
  setProgressPercent(20);
  try {
    const blob = await captureBrowserCameraBlob();
    const filename = `browser-camera-${Date.now()}.jpg`;
    const uploadResponse = await fetch(`/api/phone/upload?filename=${encodeURIComponent(filename)}`, {
      method: "POST",
      headers: { "Content-Type": "image/jpeg" },
      body: blob,
    });
    const uploadPayload = await uploadResponse.json().catch(() => ({}));
    if (!uploadResponse.ok || !uploadPayload.ok) {
      throw new Error(uploadPayload.detail || uploadPayload.message || `upload ${uploadResponse.status}`);
    }
    mergePhoneUpload(Object.assign({}, uploadPayload, { source: "browser_camera" }));
    state.selectedPhoneUploadId = uploadPayload.upload_id;
    setProgressPercent(55);
    el.phaseValue.textContent = "Detecting";

    const detectResponse = await fetch("/api/phone/detect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_id: uploadPayload.upload_id,
        detection_profile: "fast",
        detection_platform: "huawei_cloud",
        phone_experiment_profile: experimentProfile,
        operation_mode: "real_browser_camera",
      }),
    });
    const result = await detectResponse.json().catch(() => ({}));
    if (!detectResponse.ok || !result.ok) {
      throw new Error(result.detail || result.message || `detect ${detectResponse.status}`);
    }
    mergePhoneUpload(result);
    state.phoneResult = result;
    state.results.clear();
    state.results.set("phone", decorateDefects(result.defects || [], result));
    setProgressPercent(100);
    el.phaseValue.textContent = "Browser Complete";
    renderDefects([], 0, 0);
    showPreviewImage("浏览器", "整图", result.annotated_image || result.raw_image, "浏览器相机整图检测结果");
    if (result.upload_id && result.result_json) {
      setReportTarget({ target_type: "phone", upload_id: result.upload_id }, `浏览器相机整图结果 · 缺陷 ${(result.defects || []).length} 项`);
    }
    setCommandHint(`浏览器相机检测完成：${phoneExperimentProfileLabel(experimentProfile)}档位 · 实际执行：华为云`, "good");
    logEvent(`browser_camera_result ${phoneExperimentProfileLabel(experimentProfile)} actual=huawei_cloud defects=${(result.defects || []).length}`);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.phaseValue.textContent = "Browser Error";
    setCommandHint(`浏览器相机检测失败：${message}`, "bad");
    logEvent(`BROWSER CAMERA ERROR: ${message}`);
  } finally {
    state.scanning = false;
    state.backendRunning = false;
    setScanButtons(false);
    syncCameraInputModeUi();
  }
}

function renderSystemStatus(status) {
  const motor = status.motor || {};
  const camera = status.camera || {};
  const simulation = status.simulation || {};
  const algorithm = status.algorithm || {};
  const network = status.network || {};
  const cloudDetection = status.cloud_detection || {};
  const phoneInference = status.phone_inference || {};
  const detectionPlatforms = status.detection_platforms || {};
  const localDnn = detectionPlatforms.local_dnn || {};
  const llmReport = status.llm_report || {};
  const runtime = camera.runtime || {};
  const simulationRuntime = simulation.runtime || {};
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
  if (el.cameraSourceInput && document.activeElement !== el.cameraSourceInput) {
    el.cameraSourceInput.value = runtime.source || "";
  }
  if (el.cameraSourceStatus) {
    const origin = runtime.source_origin || "default";
    const probeText = detectionPlatforms.status_camera_probe ? "status probe on" : "status probe off";
    const errorText = runtime.last_error ? ` · ${runtime.last_error}` : "";
    el.cameraSourceStatus.textContent = `当前源 ${runtime.source || "-"} · ${origin} · ${probeText}${errorText}`;
  }
  if (runtime.source && state.mode !== "simulation" && state.mode !== "phone" && !state.previewOverride) {
    const origin = runtime.source_origin ? ` · ${runtime.source_origin}` : "";
    el.previewSourceText.textContent = `OV9281 ${runtime.source}${origin}`;
  }

  const runtimeBackend = algorithm.runtime && algorithm.runtime.backend ? algorithm.runtime.backend : "unknown";
  setStatusText(el.weightsStatus, weights.exists ? `已找到 ${formatBytes(weights.size_bytes)}` : "未找到模型权重", weights.exists ? "ok" : "bad");
  setStatusText(el.scriptStatus, script.exists && config.exists ? `${runtimeBackend} / 配置可解析` : "推理运行时或配置缺失", script.exists && config.exists ? "ok" : "bad");
  if (el.cloudInferenceStatus) {
    const cloudText = cloudDetection.enabled && cloudDetection.configured
      ? `在线优先 · ${cloudDetection.api_base || "cloud"}`
      : "未配置，跳过云端";
    setStatusText(el.cloudInferenceStatus, cloudText, cloudDetection.enabled && cloudDetection.configured ? "ok" : "warn");
  }
  const readyWorkers = Number(phoneInference.ready_worker_count || 0);
  const workerCount = Number(phoneInference.worker_count || 0);
  const pendingJobs = Number(phoneInference.pending_jobs || 0);
  const phoneInferenceText = phoneInference.ready
    ? `在线 ${readyWorkers}/${workerCount}${pendingJobs ? ` · 等待 ${pendingJobs}` : ""}`
    : "离线";
  setStatusText(el.phoneInferenceStatus, phoneInferenceText, phoneInference.ready ? "ok" : "warn");
  state.detectionPlatformsStatus = detectionPlatforms;
  syncDetectionPlatformUi();
  if (el.localDnnStatus) {
    const localText = localDnn.model_exists
      ? `可用 · ${localDnn.backend || "opencv_dnn"} · ${localDnn.imgsz || "-"}`
      : "未找到本地 ONNX";
    setStatusText(el.localDnnStatus, localText, localDnn.model_exists ? "ok" : "bad");
  }
  state.llmReportConfigured = Boolean(llmReport.configured);
  if (el.llmReportStatus) {
    const llmText = state.llmReportConfigured
      ? `已配置 · ${llmReport.model || "model"}`
      : "未配置 API Key";
    setStatusText(el.llmReportStatus, llmText, state.llmReportConfigured ? "ok" : "warn");
  }
  renderReportControls();
  refreshRailBadges();

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
  } else if (state.mode === "simulation") {
    const current = currentSimulationImage();
    const safeMode = simulationRuntime.board_safe_mode ? "板端安全模式" : "模拟模式";
    const maxDim = Number(simulationRuntime.max_dimension || 0);
    const limitText = maxDim > 0 ? `长边≤${maxDim}px` : "导入尺寸不限制";
    const threads = algorithm.runtime && algorithm.runtime.opencv_threads ? ` · OpenCV ${algorithm.runtime.opencv_threads}线程` : "";
    const selected = current ? ` · ${current.name || current.path}` : "";
    el.cameraPreviewStatus.textContent = `${safeMode} · ${limitText}${threads}${selected}`;
  } else if (state.mode === "real") {
    if (runtime.opened) {
      if (el.liveCameraStatus) el.liveCameraStatus.textContent = `OV9281 ${runtime.width || "-"}x${runtime.height || "-"} 在线`;
    } else if (runtime.last_error) {
      if (el.liveCameraStatus) el.liveCameraStatus.textContent = runtime.last_error;
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
  if (state.plan.scan_type === "fixed_points_16") {
    const imagePx = state.plan.image_px || {};
    const speedText = state.plan.motion_speed_mm_s ? ` · 速度 ${state.plan.motion_speed_mm_s}mm/s` : "";
    el.gridMeta.textContent = `固定 16 点 · 步距 ${step.x}mm x ${step.y}mm · 相机 ${imagePx.width || 1280}x${imagePx.height || 800}${speedText} · PCB 尺寸未知 · 拼接后自动估计重叠`;
    el.xyReadout.textContent = "固定扫描窗口";
    return;
  }
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

function drawCellImage(ctx, image, rect, mirrorX = false) {
  if (!mirrorX) {
    ctx.drawImage(image, rect.x, rect.y, rect.w, rect.h);
    return;
  }
  ctx.save();
  ctx.translate(rect.x + rect.w, rect.y);
  ctx.scale(-1, 1);
  ctx.drawImage(image, 0, 0, rect.w, rect.h);
  ctx.restore();
}

function drawDetectionOverlay(ctx, defects, rect, image, tone = "#c43c3c", mirrorX = false) {
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
    const displayX1 = mirrorX ? imageW - x2 : x1;
    const displayX2 = mirrorX ? imageW - x1 : x2;
    const bx = rect.x + (displayX1 / imageW) * rect.w;
    const by = rect.y + (y1 / imageH) * rect.h;
    const bw = Math.max(2, ((displayX2 - displayX1) / imageW) * rect.w);
    const bh = Math.max(2, ((y2 - y1) / imageH) * rect.h);
    const decision = defect.decision || "confirmed";
    const defectTone = decision === "suspected" ? "#b7791f" : tone;
    ctx.strokeStyle = defectTone;
    ctx.setLineDash(decision === "suspected" ? [6, 4] : []);
    ctx.strokeRect(bx, by, bw, bh);
    ctx.setLineDash([]);
    const prefix = decision === "suspected" ? "suspected " : "";
    const label = `${prefix}${defect.label || defect.type || "defect"} ${Math.round((defect.confidence || 0) * 100)}%`;
    const labelW = ctx.measureText(label).width + 10;
    ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
    ctx.fillRect(bx, Math.max(rect.y, by - 18), labelW, 18);
    ctx.fillStyle = defectTone;
    ctx.fillText(label, bx + 5, Math.max(rect.y + 13, by - 5));
  }
  ctx.restore();
}

function drawMosaicDetections(ctx, metrics, mosaicImage) {
  if (!state.mosaic || !state.mosaic.defects || !state.mosaic.defects.length || !mosaicImage) return;
  if (state.mosaic.mosaic_detection_skipped) return;
  if (state.mosaic.board_crop_raw) return;
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
  const mosaicPath = state.mosaic && (state.mosaic.board_crop_raw || state.mosaic.mosaic_raw);
  const mosaicImage = mosaicPath ? cachedArtifactImage(mosaicPath) : null;

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
          drawCellImage(ctx, image, rect, true);
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
          drawDetectionOverlay(ctx, cellDefects, rect, image, "#c43c3c", true);
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
      const keyedDefects = cellDefects.map((defect) => Object.assign({ cell: key }, defect));
      const selectedDefects = keyedDefects.filter((defect) => state.selectedDefectKeys.has(defectSelectionKey(defect)));
      const focusedDefects = keyedDefects.filter((defect) => state.selectedDefectKey === defectSelectionKey(defect));
      const highlightedCell = state.selectedCell || state.currentCell;
      const isCurrent = highlightedCell && highlightedCell.row === row && highlightedCell.col === col;

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

      if (selectedDefects.length) {
        ctx.strokeStyle = "#f0a500";
        ctx.lineWidth = 4;
        ctx.strokeRect(rect.x + 3, rect.y + 3, rect.w - 6, rect.h - 6);
        if (!mosaicImage) {
          const image = artifact && artifact.raw_image ? cachedArtifactImage(artifact.raw_image) : null;
          drawDetectionOverlay(ctx, selectedDefects, rect, image, "#f0a500", true);
        }
      }
      if (focusedDefects.length && !mosaicImage) {
        const image = artifact && artifact.raw_image ? cachedArtifactImage(artifact.raw_image) : null;
        drawDetectionOverlay(ctx, focusedDefects, rect, image, "#1769aa", true);
      }

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
    ctx.fillText(state.mosaic && state.mosaic.board_crop_raw ? "自动裁剪整板图" : "整板拼接复核图", ox + 18, oy + 26);
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
    notifyDiagnosisWorkspace();
    return;
  }

  if (state.plan.scan_type === "handheld") {
    drawHandheldCanvas(ctx, width, height);
    notifyDiagnosisWorkspace();
    return;
  }

  if (state.plan.scan_type === "phone") {
    drawPhoneCanvas(ctx, width, height);
    notifyDiagnosisWorkspace();
    return;
  }

  if (state.plan.scan_type === "continuous_video") {
    drawVideoCanvas(ctx, width, height);
    notifyDiagnosisWorkspace();
    return;
  }

  drawSteppedCanvas(ctx, width, height);
  notifyDiagnosisWorkspace();
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
  const baseRect = { x: ox, y: oy, w: boardW, h: boardH };
  state.phoneViewportBaseRect = baseRect;
  state.phoneViewport = window.AoiUiHelpers.clampPhoneViewport(
    state.phoneViewport,
    baseRect,
    { width, height },
  );
  const displayRect = window.AoiUiHelpers.phoneViewportRect(baseRect, state.phoneViewport);
  if (el.phoneZoomValue) el.phoneZoomValue.textContent = `${Math.round(state.phoneViewport.scale * 100)}%`;

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, width, height);
  ctx.clip();
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(displayRect.x, displayRect.y, displayRect.w, displayRect.h);

  if (image) {
    ctx.drawImage(image, displayRect.x, displayRect.y, displayRect.w, displayRect.h);
    drawDetectionOverlay(ctx, defects, displayRect, image, "#b42318");
  } else {
    ctx.fillStyle = "#667085";
    ctx.font = "22px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("等待手机上传或选择照片", displayRect.x + 28, displayRect.y + 58);
    ctx.font = "14px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("手机访问 /phone 上传后，电脑端选择照片并开始整图检测。", displayRect.x + 28, displayRect.y + 92);
  }
  ctx.strokeStyle = "#1769aa";
  ctx.lineWidth = 2;
  ctx.strokeRect(displayRect.x, displayRect.y, displayRect.w, displayRect.h);
  ctx.restore();

  ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
  ctx.fillRect(pad, pad, Math.max(80, Math.min(260, width - pad * 2)), 28);
  ctx.fillStyle = "#17202a";
  ctx.font = "13px Microsoft YaHei, Segoe UI, sans-serif";
  const label = item ? `手机照片 · ${item.name || item.upload_id}` : "手机拍照模式";
  ctx.fillText(label.slice(0, 32), pad + 10, pad + 19);
}

function phoneCanvasPoint(event) {
  const rect = el.canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * (el.canvas.width / Math.max(1, rect.width)),
    y: (event.clientY - rect.top) * (el.canvas.height / Math.max(1, rect.height)),
  };
}

function zoomPhoneCanvas(scaleFactor, point = null) {
  if (state.mode !== "phone" || !state.phoneViewportBaseRect) return;
  const target = point || { x: el.canvas.width / 2, y: el.canvas.height / 2 };
  state.phoneViewport = window.AoiUiHelpers.zoomPhoneViewport(
    state.phoneViewport,
    target,
    scaleFactor,
    state.phoneViewportBaseRect,
    { width: el.canvas.width, height: el.canvas.height },
  );
  drawCanvas();
}

function handlePhoneCanvasWheel(event) {
  if (state.mode !== "phone" || !state.phoneViewportBaseRect) return;
  event.preventDefault();
  zoomPhoneCanvas(event.deltaY < 0 ? 1.2 : 1 / 1.2, phoneCanvasPoint(event));
}

function handlePhoneCanvasPointerDown(event) {
  if (state.mode !== "phone" || event.button !== 0 || !state.phoneViewportBaseRect) return;
  state.phonePointer = {
    pointerId: event.pointerId,
    clientX: event.clientX,
    clientY: event.clientY,
    moved: 0,
  };
  el.canvas.setPointerCapture(event.pointerId);
  el.canvas.classList.add("is-dragging");
}

function handlePhoneCanvasPointerMove(event) {
  const pointer = state.phonePointer;
  if (state.mode !== "phone" || !pointer || pointer.pointerId !== event.pointerId || !state.phoneViewportBaseRect) return;
  const rect = el.canvas.getBoundingClientRect();
  const deltaClientX = event.clientX - pointer.clientX;
  const deltaClientY = event.clientY - pointer.clientY;
  pointer.clientX = event.clientX;
  pointer.clientY = event.clientY;
  pointer.moved += Math.abs(deltaClientX) + Math.abs(deltaClientY);
  const deltaX = deltaClientX * (el.canvas.width / Math.max(1, rect.width));
  const deltaY = deltaClientY * (el.canvas.height / Math.max(1, rect.height));
  state.phoneViewport = window.AoiUiHelpers.panPhoneViewport(
    state.phoneViewport,
    deltaX,
    deltaY,
    state.phoneViewportBaseRect,
    { width: el.canvas.width, height: el.canvas.height },
  );
  drawCanvas();
}

function handlePhoneCanvasPointerEnd(event) {
  const pointer = state.phonePointer;
  if (!pointer || pointer.pointerId !== event.pointerId) return;
  state.suppressCanvasClick = pointer.moved > 4;
  state.phonePointer = null;
  el.canvas.classList.remove("is-dragging");
  if (el.canvas.hasPointerCapture(event.pointerId)) el.canvas.releasePointerCapture(event.pointerId);
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

function defectSelectionKey(defect, index) {
  const source = window.AoiUiHelpers.resolveDefectSourceCell(defect);
  const sourceKey = source ? `${source.row}:${source.col}` : "mosaic";
  return `${defect.cell || "result"}|${sourceKey}|${defect.type || defect.label || "unknown"}|${JSON.stringify(defect.bbox || [])}`;
}

function cellCoordinates(row, col) {
  const origin = state.plan && state.plan.origin_mm ? state.plan.origin_mm : { x: 0, y: 0 };
  const fov = state.plan && state.plan.fov_mm ? state.plan.fov_mm : { width: 30, height: 30 };
  const step = state.plan && state.plan.step_mm ? state.plan.step_mm : { x: fov.width, y: fov.height };
  return {
    x: Number(origin.x || 0) + col * Number(step.x || 0),
    y: Number(origin.y || 0) + row * Number(step.y || 0),
  };
}

function navigateToDefect(defect, selectionKey) {
  if (!["real", "simulation"].includes(state.mode)) return;
  state.selectedDefectKey = selectionKey;
  state.previewPinned = true;
  const source = window.AoiUiHelpers.resolveDefectSourceCell(defect);
  if (!source) {
    state.selectedCell = null;
    const mosaicPath = state.mosaic && (state.mosaic.board_crop_annotated || state.mosaic.mosaic_annotated || state.mosaic.board_crop_raw || state.mosaic.mosaic_raw);
    if (mosaicPath) showPreviewImage("整板", "复核", mosaicPath, "整板缺陷结果");
    setCommandHint("该整板缺陷没有可靠的来源格，已显示整板结果", "warn");
    renderDefects([], 0, 0);
    drawCanvas();
    notifyDiagnosisWorkspace();
    return;
  }

  const { row, col } = source;
  state.selectedCell = { row, col };
  const artifact = state.cellArtifacts.get(cellKey(row, col)) || {};
  const imagePath = source.annotated_image || artifact.annotated_image || source.raw_image || artifact.raw_image;
  const coordinates = cellCoordinates(row, col);
  el.xyReadout.textContent = `${coordinates.x.toFixed(1)}, ${coordinates.y.toFixed(1)}`;
  if (imagePath) {
    showPreviewImage(row, col, imagePath, source.annotated_image || artifact.annotated_image ? "缺陷所在单格标注图" : "缺陷所在单格原图");
  } else {
    el.cameraPreviewStatus.textContent = `区域 ${row},${col} 没有可用图片`;
  }
  if (artifact.scan_id && artifact.result_json) {
    setReportTarget({ target_type: "cell", scan_id: artifact.scan_id, row, col }, `单格 ${row},${col} · 缺陷来源`);
  }
  setCommandHint(`已定位到缺陷来源区域 ${row},${col}`, "good");
  logEvent(`缺陷定位 row=${row} col=${col}`);
  renderDefects([], 0, 0);
  drawCanvas();
  notifyDiagnosisWorkspace();
}

function allDefectsForDiagnosis() {
  const all = [];
  state.results.forEach((items, cell) => {
    for (const defect of items || []) {
      const decorated = Object.assign({ cell }, defect);
      decorated.selection_key = defectSelectionKey(decorated);
      all.push(decorated);
    }
  });
  return all;
}

function setDefectContextSelected(defect, selectionKey, selected) {
  if (selected) state.selectedDefectKeys.add(selectionKey);
  else state.selectedDefectKeys.delete(selectionKey);
  renderDefects([], 0, 0);
  drawCanvas();
  notifyDiagnosisWorkspace();
}

function renderDefects(defects, row, col) {
  if (defects.length) {
    state.results.set(cellKey(row, col), defects);
  }
  const all = allDefectsForDiagnosis();
  const validKeys = new Set(all.map((item) => item.selection_key));
  state.selectedDefectKeys = new Set(Array.from(state.selectedDefectKeys).filter((key) => validKeys.has(key)));
  setFinalDefectCount(all.length);
  el.defectList.innerHTML = "";

  if (!all.length) {
    const empty = document.createElement("div");
    empty.className = "defect-item empty";
    empty.innerHTML = `<div class="thumb"></div><div><strong>暂无缺陷</strong><br><span>等待检测结果回传</span></div><span>OK</span>`;
    el.defectList.append(empty);
    notifyDiagnosisWorkspace();
    return;
  }

  for (const defect of all.slice(-40).reverse()) {
    const navigable = ["real", "simulation"].includes(state.mode);
    const decision = defect.decision || "confirmed";
    const selectionKey = defect.selection_key;
    const item = document.createElement("div");
    item.className = `defect-item selectable ${decision === "suspected" ? "suspected" : "confirmed"}${state.selectedDefectKey === selectionKey ? " selected" : ""}${state.selectedDefectKeys.has(selectionKey) ? " context-selected" : ""}`;
    const source = window.AoiUiHelpers.resolveDefectSourceCell(defect);
    const imagePath = source && (source.annotated_image || source.raw_image) ? (source.annotated_image || source.raw_image) : defect.annotated_image;
    const image = imagePath ? artifactUrl(imagePath) : "";
    const timing = defect.inference_ms ? ` · ${Math.round(defect.inference_ms)}ms` : "";
    const decisionText = decision === "suspected" ? "疑似" : "确认";
    const support = defect.support_count ? ` · support ${defect.support_count}` : "";
    const maxConfidence = defect.max_confidence ? ` · max ${Math.round(defect.max_confidence * 100)}%` : "";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "defect-context-checkbox";
    checkbox.checked = state.selectedDefectKeys.has(selectionKey);
    checkbox.setAttribute("aria-label", `选择缺陷 ${defect.label || defect.type || "unknown"} 作为 AI 上下文`);
    checkbox.addEventListener("change", () => setDefectContextSelected(defect, selectionKey, checkbox.checked));
    const body = document.createElement(navigable ? "button" : "div");
    if (navigable) body.type = "button";
    body.className = "defect-item-body";
    body.innerHTML = `
      <div class="thumb"${image ? ` style="background-image:url('${image}')"` : ""}></div>
      <div><strong>${escapeHtml(decisionText)} · ${escapeHtml(defect.label || defect.type || "unknown")}</strong><br><span>cell ${escapeHtml(defect.cell)} · bbox ${escapeHtml(JSON.stringify(defect.bbox || []))}${support}${maxConfidence}${timing}</span></div>
      <span>${Math.round((defect.confidence || 0) * 100)}%</span>
    `;
    if (navigable) {
      body.setAttribute("aria-label", `定位缺陷 ${defect.label || defect.type || "unknown"}`);
      body.addEventListener("click", () => navigateToDefect(defect, selectionKey));
    }
    item.append(checkbox, body);
    el.defectList.append(item);
  }
  notifyDiagnosisWorkspace();
}

function rememberCellArtifact(payload) {
  if (payload.row === undefined || payload.col === undefined) return;
  const key = cellKey(payload.row, payload.col);
  const existing = state.cellArtifacts.get(key) || {};
  const next = Object.assign({}, existing, {
    row: payload.row,
    col: payload.col,
  });
  for (const field of ["scan_id", "raw_image", "annotated_image", "result_json", "latest_image", "source_image", "simulation_crop"]) {
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
  if (artifact.scan_id && artifact.result_json) {
    setReportTarget(
      {
        target_type: "cell",
        scan_id: artifact.scan_id,
        row,
        col,
      },
      `单格 r${String(row).padStart(3, "0")}_c${String(col).padStart(3, "0")}`,
    );
  } else {
    setReportTarget(null, `区域 ${row},${col} 暂无可报告的检测 JSON`);
  }
}

function reportTargetLabel(target) {
  if (!target) return "请选择已检测单格、整板或手机结果";
  if (target.target_type === "cell") {
    return `单格报告 · ${target.scan_id || "-"} · r${String(target.row).padStart(3, "0")}_c${String(target.col).padStart(3, "0")}`;
  }
  if (target.target_type === "mosaic") {
    return `整板报告 · ${target.scan_id || "-"}`;
  }
  if (target.target_type === "phone") {
    return `手机整图报告 · ${target.upload_id || "-"}`;
  }
  return "未知报告目标";
}

function setReportTarget(target, message = "") {
  const targetKey = reportTargetKey(target);
  state.lastReport = targetKey ? state.reportsByTarget.get(targetKey) || null : null;
  state.reportTarget = target;
  state.reportMessage = message || "";
  renderReportControls();
}

function reportTargetKey(target) {
  if (!target) return "";
  if (target.target_type === "cell") {
    return `cell:${target.scan_id}:${target.row}:${target.col}`;
  }
  if (target.target_type === "mosaic") return `mosaic:${target.scan_id}`;
  if (target.target_type === "phone") return `phone:${target.upload_id}`;
  return "";
}

function resetReportPanel(message = "检测完成后，可在这里生成中文 LLM 诊断报告。") {
  state.reportTarget = null;
  state.lastReport = null;
  state.reportsByTarget.clear();
  state.reportBusy = false;
  state.reportMessage = "等待检测结果";
  if (el.diagnosisReport) {
    el.diagnosisReport.textContent = message;
  }
  renderReportControls();
}

function selectMosaicReportTarget() {
  if (!state.mosaic || !state.mosaic.scan_id || !state.mosaic.mosaic_result_json) {
    setReportTarget(null, "整板 PCB 结果尚未生成；请等待扫描和拼接复核完成");
    return;
  }
  setReportTarget(
    { target_type: "mosaic", scan_id: state.mosaic.scan_id },
    `整板 PCB 结果 · 缺陷 ${(state.mosaic.defects || []).length} 项`,
  );
  showPreviewImage("整板", "PCB", state.mosaic.mosaic_annotated || state.mosaic.mosaic_raw, "整板 PCB 检测结果");
}

function diagnosisArtifactControlState() {
  const batch = currentPhoneBatch();
  const phoneUpload = currentPhoneUpload();
  const phoneBatchReady = Boolean(
    batch
    && !["queued", "detecting"].includes(batch.status)
    && Number(batch.completed_count || 0) > 0
  );
  return window.AoiUiHelpers.diagnosisArtifactControls({
    mode: state.mode,
    running: state.scanning || state.backendRunning,
    hasMosaic: Boolean(state.mosaic && state.mosaic.scan_id && state.mosaic.mosaic_result_json),
    hasAnnotatedFrames: Boolean(state.mosaic && state.mosaic.annotated_frames_zip),
    annotatedFrameCount: state.mosaic && state.mosaic.annotated_frame_count || 16,
    phoneBatchReady,
    hasPhoneAnnotated: Boolean(phoneUpload && phoneUpload.upload_id && phoneUpload.annotated_image),
  });
}

function reportStatusLabel(hasTarget = Boolean(state.reportTarget)) {
  if (state.reportBusy) return "正在调用大模型";
  if (!state.llmReportConfigured) return hasTarget ? "LLM 未配置" : "等待检测结果";
  if (!hasTarget) return state.reportMessage || "等待检测结果";
  return state.reportMessage || "可生成";
}

function renderReportControls() {
  if (!el.generateReportBtn || !el.reportTargetText || !el.reportStatus) return;
  const hasTarget = Boolean(state.reportTarget);
  const artifactControls = diagnosisArtifactControlState();
  el.reportTargetText.textContent = reportTargetLabel(state.reportTarget);
  const disabled = state.reportBusy || state.scanning || state.backendRunning || !state.llmReportConfigured || !hasTarget;
  el.generateReportBtn.disabled = disabled;
  if (el.llmThinkingToggle) {
    el.llmThinkingToggle.checked = state.llmThinkingEnabled;
    el.llmThinkingToggle.disabled = state.reportBusy;
  }
  if (el.llmModelSelect) {
    el.llmModelSelect.value = state.llmModel;
    el.llmModelSelect.disabled = state.reportBusy;
  }
  if (el.downloadReportBtn) {
    const generatedTarget = state.lastReport && state.lastReport.target
      ? { target_type: state.lastReport.target_type, ...state.lastReport.target }
      : null;
    el.downloadReportBtn.disabled = state.reportBusy
      || !state.lastReport
      || reportTargetKey(generatedTarget) !== reportTargetKey(state.reportTarget);
  }
  if (el.selectMosaicReportBtn) {
    el.selectMosaicReportBtn.disabled = artifactControls.mosaicDisabled;
  }
  if (el.downloadRawFramesBtn) {
    el.downloadRawFramesBtn.disabled = artifactControls.exportDisabled;
    el.downloadRawFramesBtn.textContent = artifactControls.exportLabel;
  }
  el.generateReportBtn.textContent = state.reportBusy ? "生成中..." : "生成诊断报告";
  el.reportStatus.textContent = reportStatusLabel(hasTarget);
  notifyDiagnosisWorkspace();
}

function downloadRawFramesArchive() {
  if (state.mode === "phone") {
    const batch = currentPhoneBatch();
    if (batch && !["queued", "detecting"].includes(batch.status) && Number(batch.completed_count || 0) > 0) {
      const anchor = document.createElement("a");
      anchor.href = `/api/phone/batches/${encodeURIComponent(batch.batch_id)}/annotated.zip`;
      anchor.download = `phone_batch_${batch.batch_id}_annotated.zip`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      logEvent(`download phone batch annotated archive · ${batch.batch_id}`);
      return;
    }
    const item = currentPhoneUpload();
    if (!item || !item.upload_id || !item.annotated_image) {
      setCommandHint("手机标记图尚未生成；请先完成检测", "warn");
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = `/api/phone/uploads/${encodeURIComponent(item.upload_id)}/annotated`;
    anchor.download = `${item.upload_id}_annotated.jpg`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    logEvent(`download phone annotated image · ${item.upload_id}`);
    return;
  }
  if (!state.mosaic || !state.mosaic.annotated_frames_zip) {
    setCommandHint("16 张标注照片尚未生成；请等待真实模式扫描与检测完成", "warn");
    return;
  }
  window.open(artifactUrl(state.mosaic.annotated_frames_zip), "_blank", "noopener");
  logEvent(`download annotated frames · ${state.mosaic.annotated_frames_zip}`);
}

function sendRealScanSpeedUpdate() {
  if (!el.realMotionSpeed || state.mode !== "real" || !(state.scanning || state.backendRunning)) return;
  const requestedSpeed = Number(el.realMotionSpeed.value || 3);
  const speed = Math.min(requestedSpeed, 25);
  if (!Number.isFinite(speed) || speed <= 0) {
    setCommandHint("滑台速度必须是正数", "warn");
    return;
  }
  if (requestedSpeed > 25) {
    el.realMotionSpeed.value = "25";
    setCommandHint("滑台速度上限为 25mm/s", "warn");
  }
  const sent = send("update_real_scan_speed", { motion_speed_mm_s: speed });
  if (sent) {
    const speedClass = speed > 10 ? "软实时高速档" : "稳定档";
    setCommandHint(`速度 ${speed}mm/s 已下发（${speedClass}），将在下一段移动生效`, "neutral");
  }
}

function scheduleRealScanSpeedUpdate() {
  if (realSpeedUpdateTimer) window.clearTimeout(realSpeedUpdateTimer);
  realSpeedUpdateTimer = window.setTimeout(sendRealScanSpeedUpdate, 250);
}

async function generateDiagnosisReport() {
  if (!state.reportTarget) {
    setReportTarget(null, "请先点击已检测单格，或等待整板/手机检测完成");
    return null;
  }
  if (!state.llmReportConfigured) {
    state.reportMessage = "LLM 未配置：请在后端设置 API Key";
    renderReportControls();
    logEvent("LLM 诊断未配置，跳过报告生成");
    return null;
  }

  state.reportBusy = true;
  state.reportMessage = "";
  renderReportControls();
  try {
    const response = await fetch("/api/llm/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...state.reportTarget,
        mode: state.mode,
        model: state.llmModel,
        enable_thinking: state.llmThinkingEnabled,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || `report ${response.status}`);
    }
    state.lastReport = data;
    state.reportsByTarget.set(reportTargetKey(state.reportTarget), data);
    const executedModel = data.executed_model && data.executed_model !== data.model ? ` → ${data.executed_model}` : "";
    state.reportMessage = `已生成 · ${data.model || ""}${executedModel} · ${data.thinking_enabled ? "深度思考" : "快速响应"} · ${Math.round(Number(data.elapsed_ms || 0))}ms`;
    if (el.diagnosisReport) {
      el.diagnosisReport.textContent = data.report_markdown || "报告为空";
    }
    logEvent(`llm_report ${state.reportTarget.target_type} saved=${data.report_path || "-"}`);
    return data;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    state.reportMessage = "生成失败";
    if (el.diagnosisReport) {
      el.diagnosisReport.textContent = `诊断报告生成失败：${message}`;
    }
    logEvent(`LLM REPORT ERROR: ${message}`);
    return null;
  } finally {
    state.reportBusy = false;
    renderReportControls();
  }
}

function downloadDiagnosisReport() {
  if (!state.reportTarget || !state.lastReport) {
    setCommandHint("请先为当前目标生成诊断报告", "warn");
    return;
  }
  const generatedTarget = state.lastReport.target
    ? { target_type: state.lastReport.target_type, ...state.lastReport.target }
    : null;
  if (reportTargetKey(generatedTarget) !== reportTargetKey(state.reportTarget)) {
    setCommandHint("当前目标尚未生成诊断报告", "warn");
    return;
  }
  const query = new URLSearchParams();
  Object.entries(state.reportTarget).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== "") query.set(key, String(value));
  });
  const anchor = document.createElement("a");
  anchor.href = `/api/llm/report/download?${query.toString()}`;
  anchor.download = "diagnosis_report.md";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  logEvent(`download diagnosis report · ${reportTargetKey(state.reportTarget)}`);
}

function updateProgress(progress) {
  if (!progress) return;
  const ratio = progress.total ? (progress.done / progress.total) * 100 : 0;
  setProgressPercent(ratio);
}

function updateDetectionQueue(payload) {
  if (!payload) return;
  state.detectionQueue = {
    pending: Number(payload.pending || 0),
    captured: Number(payload.captured || 0),
    completed: Number(payload.completed || 0),
    total: Number(payload.total || 0),
    errors: Number(payload.errors || 0),
    active: payload.active || null,
  };
  if (el.detectQueueValue) {
    const active = state.detectionQueue.active
      ? ` · 检测 ${state.detectionQueue.active.row},${state.detectionQueue.active.col}`
      : "";
    const errors = state.detectionQueue.errors ? ` · 错误 ${state.detectionQueue.errors}` : "";
    el.detectQueueValue.textContent = `${state.detectionQueue.pending} 待检 · ${state.detectionQueue.completed}/${state.detectionQueue.total}${active}${errors}`;
  }
}

function resetScanUi() {
  state.currentCell = null;
  state.selectedCell = null;
  state.selectedDefectKey = "";
  state.videoPosition = null;
  state.videoTrail = [];
  state.mosaic = null;
  state.phoneResult = null;
  state.detectionQueue = { pending: 0, captured: 0, completed: 0, total: 0, errors: 0 };
  state.results.clear();
  state.cellArtifacts.clear();
  state.previewOverride = null;
  state.previewPinned = false;
  el.defectList.innerHTML = "";
  setFinalDefectCount(0);
  setProgressPercent(0);
  el.phaseValue.textContent = "Idle";
  el.debounceValue.textContent = "Ready";
  if (el.detectQueueValue) el.detectQueueValue.textContent = "0 / 0";
  renderDefects([], 0, 0);
  resetReportPanel();
  drawCanvas();
}

function handlePacket(packet) {
  const payload = packet.payload || {};
  if (packet.scan_id && !payload.scan_id) {
    payload.scan_id = packet.scan_id;
  }
  switch (packet.type) {
    case "ack":
      logEvent(payload.message || "ack");
      break;
    case "real_scan_speed_updated":
      if (payload.motion_speed_mm_s) {
        el.debounceValue.textContent = `${payload.motion_speed_mm_s}mm/s`;
      }
      setCommandHint(`滑台速度已更新为 ${payload.motion_speed_mm_s}mm/s，下一段移动生效`, "neutral");
      logEvent(`real_scan_speed_updated ${payload.motion_speed_mm_s}mm/s`);
      break;
    case "motion_complete": {
      const telemetry = payload.motion_telemetry || {};
      const achieved = Number(telemetry.achieved_dominant_speed_mm_s || 0);
      const delay = Number(telemetry.max_scheduler_delay_ms || 0);
      const priorityWarning = telemetry.priority && telemetry.priority.warning;
      if (achieved > 0) {
        el.debounceValue.textContent = `实测 ${achieved.toFixed(2)}mm/s`;
      }
      logEvent(
        `motion_complete target=${Number(payload.requested_speed_mm_s || 0).toFixed(2)}mm/s ` +
          `actual=${achieved.toFixed(2)}mm/s max_jitter=${delay.toFixed(2)}ms`
      );
      if (priorityWarning) {
        setCommandHint(`滑台运动完成，但 GPIO helper 优先级调整失败：${priorityWarning}`, "warn");
      } else if (delay > 50) {
        setCommandHint(`滑台运动完成，但检测到 ${delay.toFixed(1)}ms 调度停顿`, "warn");
      }
      break;
    }
    case "grid_plan":
      state.plan = payload;
      state.selectedCell = null;
      state.selectedDefectKey = "";
      state.previewPinned = false;
      state.previewOverride = null;
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
      if (payload.phase === "capture_first_frame") el.debounceValue.textContent = "首点先拍";
      if (payload.phase === "drain_detection_queue") el.debounceValue.textContent = "等待队列清空";
      if (payload.motion_speed_mm_s) el.debounceValue.textContent = `${payload.motion_speed_mm_s}mm/s`;
      if (payload.queue) updateDetectionQueue(payload.queue);
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
        if (!state.previewPinned) showPreviewImage(payload.row, payload.col, payload.raw_image, "当前拍摄原图");
      }
      if (payload.phase === "detect_inference" && payload.raw_image && payload.row !== undefined) {
        if (!state.previewPinned) showPreviewImage(payload.row, payload.col, payload.raw_image, "当前原图");
      }
      logPhase(payload);
      break;
    case "scan_result":
      state.currentCell = { row: payload.row, col: payload.col };
      rememberCellArtifact(payload);
      if (payload.queue) updateDetectionQueue(payload.queue);
      updateProgress(payload.progress);
      drawCanvas();
      if (!state.previewPinned) showCellArtifact(payload.row, payload.col);
      logEvent(`result row=${payload.row} col=${payload.col} ${inferenceLocationLabel(payload)} defects=${(payload.defects || []).length} infer=${payload.inference_ms ? Math.round(payload.inference_ms) : "-"}ms`);
      break;
    case "detection_queue":
      updateDetectionQueue(payload);
      logEvent(`queue pending=${payload.pending || 0} completed=${payload.completed || 0}/${payload.total || 0}`);
      break;
    case "mosaic_result":
      state.mosaic = payload;
      if (payload.mosaic_raw) cachedArtifactImage(payload.mosaic_raw);
      if (payload.mosaic_annotated) cachedArtifactImage(payload.mosaic_annotated);
      if (payload.board_crop_raw) cachedArtifactImage(payload.board_crop_raw);
      if (payload.board_crop_annotated) cachedArtifactImage(payload.board_crop_annotated);
      if (payload.defects && payload.defects.length) {
        state.results.set("mosaic", decorateDefects(payload.defects, {
          raw_image: payload.mosaic_raw,
          annotated_image: payload.mosaic_annotated,
          inference_ms: payload.inference_ms,
        }));
      }
      renderDefects([], 0, 0);
      drawCanvas();
      if (!state.previewPinned) {
        showPreviewImage(
          "整板",
          "复核",
          payload.board_crop_annotated || payload.board_crop_raw || payload.mosaic_annotated || payload.mosaic_raw,
          payload.board_crop_raw ? "自动裁剪整板图" : "整板拼接复核图",
        );
      }
      if (payload.scan_id && payload.mosaic_result_json) {
        setReportTarget({ target_type: "mosaic", scan_id: payload.scan_id }, `整板拼接结果 · 缺陷 ${(payload.defects || []).length} 项`);
      }
      renderReportControls();
      logEvent(`mosaic_result cells=${payload.cell_count || 0} defects=${(payload.defects || []).length} crop=${payload.board_crop_status || "-"} stitch=${payload.stitch_ms ? Math.round(payload.stitch_ms) : "-"}ms`);
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
        if (state.mosaic.scan_id && state.mosaic.mosaic_result_json) {
          setReportTarget({ target_type: "mosaic", scan_id: state.mosaic.scan_id }, "整板拼接结果可生成报告");
        }
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
      setProgressPercent(100);
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
      state.phoneExperimentProfile = payload.phone_experiment_profile || state.phoneExperimentProfile;
      setCommandHint(
        `手机照片整图推理中 · ${phoneExperimentProfileLabel(state.phoneExperimentProfile)} · ${payload.detection_profile_label || phoneDetectionProfileLabel()}`,
        "good",
      );
      setProgressPercent(45);
      logEvent(`phone_detecting ${payload.upload_id || ""} ${phoneExperimentProfileLabel(state.phoneExperimentProfile)} ${payload.detection_profile_label || ""}`);
      break;
    case "phone_result":
      {
        const clientElapsedMs = stopPhoneElapsedTimer(payload.total_elapsed_ms);
        payload.client_total_elapsed_ms = Math.round(Number(clientElapsedMs || 0) * 100) / 100;
      }
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
      setProgressPercent(100);
      el.phaseValue.textContent = "Phone Complete";
      {
        const experimentLabel = phoneExperimentProfileLabel(payload.experiment_profile || state.phoneExperimentProfile);
        setCommandHint(
          `手机照片检测完成：${payload.detection_profile_label || phoneDetectionProfileLabel(payload.detection_profile)} · ${experimentLabel} · 总耗时 ${formatElapsedMs(state.phoneElapsedMs)}`,
          "good",
        );
      }
      showPreviewImage("手机", "整图", payload.annotated_image || payload.raw_image, "手机整图检测结果");
      if (payload.upload_id && payload.result_json) {
        setReportTarget({ target_type: "phone", upload_id: payload.upload_id }, `手机整图结果 · 缺陷 ${(payload.defects || []).length} 项`);
      }
      drawCanvas();
      refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
      logEvent(`phone_result ${phoneExperimentProfileLabel(payload.experiment_profile)} defects=${(payload.defects || []).length} total=${formatElapsedMs(state.phoneElapsedMs)}`);
      break;
    case "phone_error":
      stopPhoneElapsedTimer();
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
  setPhysicalControlsEnabled(state.mode === "real" && !busy);
  renderReportControls();
}

function modePreflight(status) {
  if (!state.mode) {
    return { ok: false, message: "请先选择运行模式" };
  }
  const camera = status && status.camera ? status.camera : {};
  const motor = status && status.motor ? status.motor : {};
  const scan = status && status.scan ? status.scan : {};
  const platforms = status && status.detection_platforms ? status.detection_platforms : {};
  const cameraReady = camera.runtime && camera.runtime.opened;

  if (scan.running && !state.scanning) {
    return { ok: false, message: "已有检测任务运行，请先停止" };
  }
  const cloud = platforms.huawei_cloud || (status && status.cloud_detection) || {};
  if (!(cloud.enabled && cloud.configured)) {
    return { ok: false, message: "三个实验档位均由华为云执行；请先配置并启用华为云检测" };
  }
  if (state.mode === "real" && currentCameraInputMode() !== "browser" && !cameraReady && platforms.status_camera_probe) {
    return { ok: false, message: "相机未打开，无法进入相机抓拍流程" };
  }
  if (state.mode === "phone" && !state.selectedPhoneUploadId) {
    return { ok: false, message: "请先选择一张手机上传照片" };
  }
  if (state.mode === "real" && !motor.connected) {
    logEvent("真实模式提示：当前滑台状态不是 connected，请确认驱动配置或 dry-run 环境。");
  }
  return { ok: true };
}

async function startScan() {
  const phoneMode = state.mode === "phone";
  if (phoneMode) {
    resetPhoneViewport(false);
    startPhoneElapsedTimer();
  }
  const status = await refreshSystemStatus({ log: true });
  const preflight = modePreflight(status);
  if (!preflight.ok) {
    if (phoneMode) stopPhoneElapsedTimer();
    el.phaseValue.textContent = "Preflight";
    setCommandHint(preflight.message, "bad");
    logEvent(`启动被拦截：${preflight.message}`);
    return;
  }

  if (state.mode === "real" && currentCameraInputMode() === "browser") {
    await captureBrowserFrameDetection();
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
  setProgressPercent(0);

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
    const detectionProfile = currentPhoneDetectionProfile();
    state.plan = buildPhonePlan(item);
    updatePlanUi();
    drawCanvas();
    updatePreviewSource();
    const sent = send("start_phone_detection", {
      upload_id: state.selectedPhoneUploadId,
      detection_profile: detectionProfile,
      detection_platform: "huawei_cloud",
      phone_experiment_profile: currentPhoneExperimentProfile(),
    });
    if (!sent) {
      stopPhoneElapsedTimer();
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

  const experimentProfile = currentPhoneExperimentProfile();
  const sent = send("start_scan", {
    operation_mode: state.mode,
    capture_mode: "real",
    detection_platform: "huawei_cloud",
    experiment_profile: experimentProfile,
    pcb_length_mm: Number(el.pcbLength ? el.pcbLength.value : 100),
    pcb_width_mm: Number(el.pcbWidth ? el.pcbWidth.value : 70),
    origin_x_mm: Number(el.originX ? el.originX.value : 0),
    origin_y_mm: Number(el.originY ? el.originY.value : 0),
    motion_speed_mm_s: state.mode === "real" ? Number(el.realMotionSpeed.value || 3) : undefined,
    settle_seconds: state.mode === "real" ? Number(el.realSettleSeconds.value || 0.05) : undefined,
    image_path: el.imagePath ? el.imagePath.value.trim() : "algorithm/outputs/camera/latest.jpg",
  });
  if (!sent) {
    state.backendRunning = false;
    setScanButtons(false);
  }
}

function selectCellFromCanvas(event) {
  if (state.suppressCanvasClick) {
    state.suppressCanvasClick = false;
    return;
  }
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
      if (item.result_json) {
        setReportTarget({ target_type: "phone", upload_id: item.upload_id }, `手机整图结果 · 缺陷 ${(item.defects || []).length} 项`);
      }
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
  state.selectedCell = { row, col };
  state.selectedDefectKey = "";
  state.previewPinned = true;
  const x = selected.rect.x_mm;
  const y = selected.rect.y_mm;
  el.xyReadout.textContent = `${x.toFixed(1)}, ${y.toFixed(1)}`;
  showCellArtifact(row, col);
  logEvent(`选中区域 row=${row} col=${col} X=${x.toFixed(1)} Y=${y.toFixed(1)}`);
  drawCanvas();
}

function navigateToPanel(button) {
  setWorkspaceView(button.dataset.workspaceView, { toggle: true });
}

function activeDiagnosisScanId() {
  if (state.reportTarget && state.reportTarget.scan_id) return String(state.reportTarget.scan_id);
  if (state.mosaic && state.mosaic.scan_id) return String(state.mosaic.scan_id);
  for (const artifact of state.cellArtifacts.values()) {
    if (artifact && artifact.scan_id) return String(artifact.scan_id);
  }
  return "";
}

function diagnosisSessionQuery() {
  if (["real", "simulation"].includes(state.mode)) {
    const scanId = activeDiagnosisScanId();
    return scanId ? { mode: state.mode, scan_id: scanId } : null;
  }
  if (state.mode === "phone") {
    const batch = currentPhoneBatch();
    if (batch && batch.batch_id) return { mode: "phone", batch_id: batch.batch_id };
    const item = currentPhoneUpload();
    return item && item.upload_id ? { mode: "phone", upload_id: item.upload_id } : null;
  }
  return null;
}

function selectedDefectsForDiagnosis() {
  return allDefectsForDiagnosis().filter((defect) => state.selectedDefectKeys.has(defect.selection_key));
}

function diagnosisSnapshot() {
  const uploads = state.phoneUploads.map((item) => Object.assign({}, item, {
    preview_url: phoneArtifactUrl(item, item.annotated_image || item.raw_image),
  }));
  const currentUpload = uploads.find((item) => item.upload_id === state.selectedPhoneUploadId) || null;
  const batch = currentPhoneBatch();
  return {
    mode: state.mode,
    canvas: el.canvas,
    defects: allDefectsForDiagnosis(),
    selectedDefectKeys: Array.from(state.selectedDefectKeys),
    currentDefectKey: state.selectedDefectKey,
    phoneUploads: uploads,
    selectedPhoneUploadIds: selectedPhoneUploadIds(),
    selectedPhoneUploadId: state.selectedPhoneUploadId,
    currentPhoneUpload: currentUpload,
    currentPhoneBatchId: batch ? batch.batch_id : "",
    currentPhoneBatchItems: batch && Array.isArray(batch.items) ? batch.items.map((item) => item.upload_id).filter(Boolean) : [],
    reportTarget: state.reportTarget ? Object.assign({}, state.reportTarget) : null,
    reportTargetText: reportTargetLabel(state.reportTarget),
    reportStatusText: reportStatusLabel(),
    artifactControls: diagnosisArtifactControlState(),
    reportBusy: state.reportBusy,
    reportMessage: state.reportMessage,
    lastReport: state.lastReport ? Object.assign({}, state.lastReport) : null,
    llmReportConfigured: state.llmReportConfigured,
    llmThinkingEnabled: state.llmThinkingEnabled,
    llmModel: state.llmModel,
    sessionQuery: diagnosisSessionQuery(),
  };
}

function setDiagnosisLlmModel(model) {
  const selected = String(model || "");
  state.llmModel = LLM_MODELS.some((item) => item.id === selected) ? selected : "qwen3.7-plus";
  window.localStorage.setItem("aoiLlmModel", state.llmModel);
  const selectedModel = LLM_MODELS.find((item) => item.id === state.llmModel);
  state.reportMessage = `诊断模型已切换为 ${selectedModel ? selectedModel.label : state.llmModel}`;
  renderReportControls();
}

function setDiagnosisThinkingEnabled(enabled) {
  state.llmThinkingEnabled = Boolean(enabled);
  window.localStorage.setItem("aoiLlmThinkingEnabled", String(state.llmThinkingEnabled));
  state.reportMessage = state.llmThinkingEnabled ? "深度思考已开启" : "深度思考已关闭，优先生成速度";
  renderReportControls();
}

function toggleDiagnosisDefect(selectionKey, selected) {
  const defect = allDefectsForDiagnosis().find((item) => item.selection_key === selectionKey);
  if (!defect) return false;
  setDefectContextSelected(defect, selectionKey, selected);
  return true;
}

function navigateDiagnosisDefect(selectionKey) {
  const defect = allDefectsForDiagnosis().find((item) => item.selection_key === selectionKey);
  if (!defect) return false;
  navigateToDefect(defect, selectionKey);
  return true;
}

function toggleDiagnosisPhoneUpload(uploadId, selected) {
  const exists = state.phoneUploads.some((item) => item.upload_id === uploadId);
  if (!exists) return false;
  if (selected) state.selectedPhoneUploadIds.add(uploadId);
  else state.selectedPhoneUploadIds.delete(uploadId);
  renderPhoneUploads();
  return true;
}

function diagnosisChatPayload(message) {
  const query = diagnosisSessionQuery();
  if (!query) throw new Error("当前模式尚无可用检测对象。请先完成检测或选择手机照片。");
  const payload = Object.assign({}, query, {
    message: String(message || "").trim(),
    model: state.llmModel,
    enable_thinking: state.llmThinkingEnabled,
    report_target: state.reportTarget ? Object.assign({}, state.reportTarget) : null,
  });
  if (["real", "simulation"].includes(state.mode)) {
    payload.selected_defects = selectedDefectsForDiagnosis()
      .map((defect) => window.AoiUiHelpers.defectChatReference(defect))
      .filter(Boolean);
  } else {
    payload.selected_upload_ids = selectedPhoneUploadIds();
    if (!payload.selected_upload_ids.length && state.selectedPhoneUploadId) {
      payload.upload_id = state.selectedPhoneUploadId;
    }
  }
  return payload;
}

window.AoiDiagnosisBridge = {
  subscribe(callback) {
    if (typeof callback !== "function") return () => {};
    diagnosisWorkspaceSubscribers.add(callback);
    callback();
    return () => diagnosisWorkspaceSubscribers.delete(callback);
  },
  snapshot: diagnosisSnapshot,
  setModel: setDiagnosisLlmModel,
  setThinking: setDiagnosisThinkingEnabled,
  generateReport: generateDiagnosisReport,
  downloadReport: downloadDiagnosisReport,
  downloadArtifacts: downloadRawFramesArchive,
  selectMosaicReportTarget,
  toggleDefect: toggleDiagnosisDefect,
  navigateDefect: navigateDiagnosisDefect,
  togglePhoneUpload: toggleDiagnosisPhoneUpload,
  selectPhoneUpload: (uploadId) => setPhoneUpload(uploadId),
  buildChatPayload: diagnosisChatPayload,
  sessionQuery: diagnosisSessionQuery,
};

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

if (el.realMotionSpeed) {
  el.realMotionSpeed.addEventListener("change", sendRealScanSpeedUpdate);
  el.realMotionSpeed.addEventListener("input", scheduleRealScanSpeedUpdate);
}

Array.prototype.forEach.call(el.modeCards, (card) => {
  card.addEventListener("click", () => chooseMode(card.dataset.mode));
});

el.backToModesBtn.addEventListener("click", returnToModeScreen);
el.pingBtn.addEventListener("click", () => send("ping"));
el.stopBtn.addEventListener("click", () => send("stop_scan"));
el.refreshStatusBtn.addEventListener("click", () => refreshSystemStatus({ log: true }));
if (el.generateReportBtn) {
  el.generateReportBtn.addEventListener("click", generateDiagnosisReport);
}
if (el.downloadReportBtn) {
  el.downloadReportBtn.addEventListener("click", downloadDiagnosisReport);
}
if (el.llmThinkingToggle) {
  el.llmThinkingToggle.checked = state.llmThinkingEnabled;
  el.llmThinkingToggle.addEventListener("change", () => {
    state.llmThinkingEnabled = Boolean(el.llmThinkingToggle.checked);
    window.localStorage.setItem("aoiLlmThinkingEnabled", String(state.llmThinkingEnabled));
    state.reportMessage = state.llmThinkingEnabled ? "深度思考已开启" : "深度思考已关闭，优先生成速度";
    renderReportControls();
  });
}
if (el.llmModelSelect) {
  el.llmModelSelect.value = state.llmModel;
  el.llmModelSelect.addEventListener("change", () => {
    const selected = String(el.llmModelSelect.value || "");
    state.llmModel = LLM_MODELS.some((item) => item.id === selected) ? selected : "qwen3.7-plus";
    window.localStorage.setItem("aoiLlmModel", state.llmModel);
    const model = LLM_MODELS.find((item) => item.id === state.llmModel);
    state.reportMessage = `诊断模型已切换为 ${model ? model.label : state.llmModel}`;
    renderReportControls();
  });
}
if (el.selectMosaicReportBtn) {
  el.selectMosaicReportBtn.addEventListener("click", selectMosaicReportTarget);
}
el.reloadCameraBtn.addEventListener("click", () => {
  if (state.mode === "phone") {
    refreshPhoneUploads({ preserveSelection: true, selectLatest: false });
  }
  updatePreviewSource();
  refreshSystemStatus({ log: true });
});
if (el.applyCameraSourceBtn) {
  el.applyCameraSourceBtn.addEventListener("click", applyCameraSource);
}
if (el.probeCameraSourceBtn) {
  el.probeCameraSourceBtn.addEventListener("click", probeCameraSource);
}
Array.prototype.forEach.call(el.cameraInputModeInputs || [], (input) => {
  input.addEventListener("change", () => {
    state.cameraInputMode = input.value;
    syncCameraInputModeUi();
    setCommandHint(input.value === "browser" ? "已切换为浏览器相机；可打开相机后拍照检测" : "已切换为后端相机；真实模式将使用运行网站后端的设备", "neutral");
    logEvent(`camera_input_mode ${input.value}`);
  });
});
if (el.openBrowserCameraBtn) {
  el.openBrowserCameraBtn.addEventListener("click", openBrowserCamera);
}
if (el.captureBrowserFrameBtn) {
  el.captureBrowserFrameBtn.addEventListener("click", captureBrowserFrameDetection);
}
if (el.cameraSourceInput) {
  el.cameraSourceInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      applyCameraSource();
    }
  });
}
if (el.uploadSimulationBtn) {
  el.uploadSimulationBtn.addEventListener("click", uploadSimulationImages);
}
if (el.uploadPhoneBtn) {
  el.uploadPhoneBtn.addEventListener("click", uploadPhoneImages);
}
if (el.refreshPhoneUploadsBtn) {
  el.refreshPhoneUploadsBtn.addEventListener("click", () => refreshPhoneUploads({ preserveSelection: false, selectLatest: true }));
}
if (el.deletePhoneUploadBtn) {
  el.deletePhoneUploadBtn.addEventListener("click", deleteSelectedPhoneUpload);
}
if (el.detectPhoneBatchBtn) {
  el.detectPhoneBatchBtn.addEventListener("click", detectCurrentPhoneBatch);
}
if (el.deletePhoneBatchBtn) {
  el.deletePhoneBatchBtn.addEventListener("click", deleteCurrentPhoneBatch);
}
if (el.selectAllPhoneHistoryBtn) {
  el.selectAllPhoneHistoryBtn.addEventListener("click", selectAllPhoneHistoryUploads);
}
if (el.clearPhoneHistorySelectionBtn) {
  el.clearPhoneHistorySelectionBtn.addEventListener("click", clearPhoneHistorySelection);
}
if (el.detectSelectedPhoneUploadsBtn) {
  el.detectSelectedPhoneUploadsBtn.addEventListener("click", detectSelectedPhoneUploads);
}
if (el.deleteSelectedPhoneUploadsBtn) {
  el.deleteSelectedPhoneUploadsBtn.addEventListener("click", deleteSelectedPhoneUploads);
}
if (el.downloadRawFramesBtn) {
  el.downloadRawFramesBtn.addEventListener("click", downloadRawFramesArchive);
}
Array.prototype.forEach.call(el.phoneProfileInputs || [], (input) => {
  input.addEventListener("change", () => {
    updatePhoneProfileUi();
    logEvent(`手机检测模式切换为 ${phoneDetectionProfileLabel()}`);
  });
});
Array.prototype.forEach.call(el.platformInputs || [], (input) => {
  input.addEventListener("change", () => {
    state.detectionPlatform = input.value;
    state.phoneExperimentProfile = phoneExperimentProfileFromPlatform(input.value);
    syncDetectionPlatformUi();
    const selectorLabel = "检测实验档位";
    setCommandHint(`${selectorLabel}已切换为 ${detectionPlatformLabel(input.value)} · 实际执行：华为云`, "neutral");
    logEvent(`${selectorLabel}切换为 ${detectionPlatformLabel(input.value)} actual=huawei_cloud`);
  });
});
if (el.phoneImageFile) {
  el.phoneImageFile.addEventListener("change", () => {
    const files = Array.from(el.phoneImageFile.files || []);
    const selectionError = window.AoiUiHelpers.phoneBatchSelectionError(files.length);
    el.phoneUploadStatus.textContent = selectionError || `已选择 ${files.length} 张手机照片，可创建新批次`;
    if (el.uploadPhoneBtn) el.uploadPhoneBtn.disabled = Boolean(selectionError);
  });
}
if (el.simulationImageFile) {
  el.simulationImageFile.addEventListener("change", () => {
    const files = Array.from(el.simulationImageFile.files || []);
    if (el.simulationUploadStatus) {
      el.simulationUploadStatus.textContent = files.length
        ? `已选择 ${files.length} 张图片`
        : "尚未导入图片";
    }
  });
}
if (el.simulationSourceSelect) {
  el.simulationSourceSelect.addEventListener("change", () => {
    setSimulationSource(el.simulationSourceSelect.value);
  });
}
if (el.originalPcbPreview) {
  el.originalPcbPreview.addEventListener("load", () => {
    const item = currentSimulationImage();
    if (item && el.originalPcbStatus) {
      el.originalPcbStatus.textContent = `完整原图已加载：${simulationImageLabel(item)}`;
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
    el.cameraPreviewStatus.textContent = item ? `模拟图已加载：${simulationImageLabel(item)}` : "等待导入 PCB 图片";
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
if (el.liveCameraPreview) {
  el.liveCameraPreview.addEventListener("load", () => {
    if (el.liveCameraStatus) el.liveCameraStatus.textContent = "实时预览已连接";
  });
  el.liveCameraPreview.addEventListener("error", () => {
    if (el.liveCameraStatus) el.liveCameraStatus.textContent = "实时预览连接失败";
  });
}
el.homeBtn.addEventListener("click", () => send("home_all_axes"));
el.startBtn.addEventListener("click", startScan);
el.zUpBtn.addEventListener("click", () => send("z_jog", { direction: "up", dist: Number(el.zDist.value) }));
el.zDownBtn.addEventListener("click", () => send("z_jog", { direction: "down", dist: Number(el.zDist.value) }));
el.canvas.addEventListener("click", selectCellFromCanvas);
el.canvas.addEventListener("wheel", handlePhoneCanvasWheel, { passive: false });
el.canvas.addEventListener("pointerdown", handlePhoneCanvasPointerDown);
el.canvas.addEventListener("pointermove", handlePhoneCanvasPointerMove);
el.canvas.addEventListener("pointerup", handlePhoneCanvasPointerEnd);
el.canvas.addEventListener("pointercancel", handlePhoneCanvasPointerEnd);
el.canvas.addEventListener("dblclick", (event) => {
  if (state.mode !== "phone") return;
  event.preventDefault();
  resetPhoneViewport();
});
if (el.phoneZoomInBtn) el.phoneZoomInBtn.addEventListener("click", () => zoomPhoneCanvas(1.25));
if (el.phoneZoomOutBtn) el.phoneZoomOutBtn.addEventListener("click", () => zoomPhoneCanvas(0.8));
if (el.phoneZoomResetBtn) el.phoneZoomResetBtn.addEventListener("click", () => resetPhoneViewport());
Array.prototype.forEach.call(el.railButtons, (button) => {
  button.addEventListener("click", () => navigateToPanel(button));
});
window.addEventListener("resize", drawCanvas);
window.addEventListener("online", connectWs);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (!state.ws || state.ws.readyState > WebSocket.OPEN)) connectWs();
});

setWorkspaceView("balanced", { redraw: false });
setProgressPercent(0);
connectWs();
refreshSystemStatus();
renderDefects([], 0, 0);
renderReportControls();
