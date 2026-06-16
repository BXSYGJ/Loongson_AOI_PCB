const state = {
  ws: null,
  plan: null,
  currentCell: null,
  results: new Map(),
  scanning: false,
};

const el = {
  wsDot: document.getElementById("wsDot"),
  wsText: document.getElementById("wsText"),
  pingBtn: document.getElementById("pingBtn"),
  stopBtn: document.getElementById("stopBtn"),
  startBtn: document.getElementById("startBtn"),
  homeBtn: document.getElementById("homeBtn"),
  zUpBtn: document.getElementById("zUpBtn"),
  zDownBtn: document.getElementById("zDownBtn"),
  pcbLength: document.getElementById("pcbLength"),
  pcbWidth: document.getElementById("pcbWidth"),
  originX: document.getElementById("originX"),
  originY: document.getElementById("originY"),
  imagePath: document.getElementById("imagePath"),
  mockMode: document.getElementById("mockMode"),
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

function logEvent(text) {
  const item = document.createElement("li");
  item.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
  el.eventLog.prepend(item);
  while (el.eventLog.children.length > 80) {
    el.eventLog.lastElementChild.remove();
  }
}

window.addEventListener("error", (event) => {
  logEvent(`前端脚本错误: ${event.message}`);
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason && event.reason.message ? event.reason.message : String(event.reason);
  logEvent(`前端异步错误: ${reason}`);
});

function setWsOnline(online) {
  el.wsDot.classList.toggle("online", online);
  el.wsText.textContent = online ? "WebSocket 在线" : "WebSocket 离线";
}

function send(type, payload = {}) {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    logEvent("WebSocket 未连接，指令未发送");
    return false;
  }
  logEvent(`发送指令: ${type}`);
  state.ws.send(JSON.stringify({ type, request_id: requestId(), payload }));
  return true;
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

async function previewGrid() {
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
    el.gridMeta.textContent = error.message;
    logEvent(`网格预览失败: ${error.message}`);
  }
}

async function refreshSystemStatus(options = {}) {
  try {
    const response = await fetch("/api/system/status");
    if (!response.ok) {
      throw new Error(`system status ${response.status}`);
    }
    const status = await response.json();
    renderSystemStatus(status);
    if (options.log) {
      logEvent("系统状态已刷新");
    }
    return status;
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    el.motorStatus.textContent = "状态接口异常";
    el.captureStatus.textContent = "状态接口异常";
    el.weightsStatus.textContent = "状态接口异常";
    el.scriptStatus.textContent = "状态接口异常";
    logEvent(`系统状态刷新失败: ${message}`);
    return null;
  }
}

function renderSystemStatus(status) {
  const motor = status.motor || {};
  const camera = status.camera || {};
  const algorithm = status.algorithm || {};
  const network = status.network || {};
  const capture = camera.capture_image || {};
  const weights = algorithm.weights || {};
  const script = algorithm.detect_script || {};

  setStatusText(el.motorStatus, motor.connected ? "已连接" : "Mock桩/未接入", motor.connected ? "ok" : "warn");
  setStatusText(el.captureStatus, capture.exists ? `已落盘 ${formatBytes(capture.size_bytes)}` : "未找到 latest.jpg", capture.exists ? "ok" : "bad");
  setStatusText(el.weightsStatus, weights.exists ? `已找到 ${formatBytes(weights.size_bytes)}` : "未找到 best.pt", weights.exists ? "ok" : "bad");
  setStatusText(el.scriptStatus, script.exists ? "已解析" : "未找到", script.exists ? "ok" : "bad");
  renderLanUrls(network.lan_urls || []);
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
  el.rowsValue.textContent = state.plan.rows;
  el.colsValue.textContent = state.plan.cols;
  el.tilesValue.textContent = state.plan.total_cells;
  const boundary = state.plan.logical_boundary;
  el.gridMeta.textContent = `FOV 30mm x 30mm · 覆盖 ${state.plan.covered_mm.length}mm x ${state.plan.covered_mm.width}mm · 溢出 ${boundary.overflow_right_mm}mm / ${boundary.overflow_bottom_mm}mm`;
}

function resizeCanvasForDisplay() {
  const rect = el.canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const width = Math.max(320, Math.floor(rect.width * ratio));
  const height = Math.max(260, Math.floor(rect.height * ratio));
  if (el.canvas.width !== width || el.canvas.height !== height) {
    el.canvas.width = width;
    el.canvas.height = height;
  }
  return { width, height, ratio };
}

function drawCanvas() {
  const { width, height } = resizeCanvasForDisplay();
  const ctx = el.canvas.getContext("2d");
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#08100f";
  ctx.fillRect(0, 0, width, height);

  if (!state.plan) {
    ctx.fillStyle = "#8fa0aa";
    ctx.font = "24px Microsoft YaHei, Segoe UI, sans-serif";
    ctx.fillText("等待 PCB 长宽参数", 36, 58);
    return;
  }

  const pad = 34;
  const cols = state.plan.cols;
  const rows = state.plan.rows;
  const tile = Math.min((width - pad * 2) / cols, (height - pad * 2) / rows);
  const gridW = tile * cols;
  const gridH = tile * rows;
  const ox = (width - gridW) / 2;
  const oy = (height - gridH) / 2;
  const boundary = state.plan.logical_boundary;

  ctx.fillStyle = "#10271f";
  ctx.fillRect(ox, oy, gridW, gridH);

  for (let row = 0; row < rows; row += 1) {
    for (let col = 0; col < cols; col += 1) {
      const x = ox + col * tile;
      const y = oy + row * tile;
      const key = `${row}:${col}`;
      const cellDefects = state.results.get(key);
      const hasDefect = cellDefects && cellDefects.length > 0;
      const isCurrent = state.currentCell && state.currentCell.row === row && state.currentCell.col === col;

      ctx.fillStyle = hasDefect ? "rgba(255, 77, 87, 0.20)" : "rgba(32, 209, 125, 0.055)";
      ctx.fillRect(x + 1, y + 1, tile - 2, tile - 2);

      if (col === cols - 1 && boundary.last_col_visible_ratio < 1) {
        const visibleW = tile * boundary.last_col_visible_ratio;
        drawClippedZone(ctx, x + visibleW, y, tile - visibleW, tile);
      }
      if (row === rows - 1 && boundary.last_row_visible_ratio < 1) {
        const visibleH = tile * boundary.last_row_visible_ratio;
        drawClippedZone(ctx, x, y + visibleH, tile, tile - visibleH);
      }

      ctx.strokeStyle = isCurrent ? "#ffb84d" : "rgba(42, 222, 145, 0.42)";
      ctx.lineWidth = isCurrent ? 3 : 1;
      ctx.strokeRect(x + 0.5, y + 0.5, tile - 1, tile - 1);

      ctx.fillStyle = "#8fa0aa";
      ctx.font = `${Math.max(10, Math.min(14, tile * 0.16))}px Segoe UI, sans-serif`;
      ctx.fillText(`${row},${col}`, x + 8, y + 18);
    }
  }

  ctx.strokeStyle = "#47b8d8";
  ctx.lineWidth = 2;
  ctx.strokeRect(ox, oy, gridW, gridH);
}

function drawClippedZone(ctx, x, y, w, h) {
  if (w <= 0 || h <= 0) return;
  ctx.save();
  ctx.fillStyle = "rgba(255, 184, 77, 0.16)";
  ctx.fillRect(x, y, w, h);
  ctx.strokeStyle = "rgba(255, 184, 77, 0.55)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = -h; i < w; i += 10) {
    ctx.moveTo(x + i, y + h);
    ctx.lineTo(x + i + h, y);
  }
  ctx.stroke();
  ctx.restore();
}

function renderDefects(defects, row, col) {
  if (defects.length) {
    state.results.set(`${row}:${col}`, defects);
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
    empty.className = "defect-item";
    empty.innerHTML = `<div class="thumb"></div><div><strong>暂无缺陷</strong><br><span>等待 result.json 回传</span></div><span>OK</span>`;
    el.defectList.append(empty);
    return;
  }

  for (const defect of all.slice(-30).reverse()) {
    const item = document.createElement("div");
    item.className = "defect-item";
    item.innerHTML = `
      <div class="thumb"></div>
      <div><strong>${defect.label || defect.type || "unknown"}</strong><br><span>cell ${defect.cell} · bbox ${JSON.stringify(defect.bbox || [])}</span></div>
      <span>${Math.round((defect.confidence || 0) * 100)}%</span>
    `;
    el.defectList.append(item);
  }
}

function updateProgress(progress) {
  if (!progress) return;
  const ratio = progress.total ? (progress.done / progress.total) * 100 : 0;
  el.progressBar.style.width = `${Math.max(0, Math.min(100, ratio))}%`;
}

function handlePacket(packet) {
  const payload = packet.payload || {};
  switch (packet.type) {
    case "ack":
      logEvent(payload.message || "ack");
      break;
    case "grid_plan":
      state.plan = payload;
      state.results.clear();
      updatePlanUi();
      drawCanvas();
      logEvent(`grid_plan Rows=${payload.rows} Cols=${payload.cols}`);
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
      const rowText = payload.row !== undefined && payload.row !== null ? payload.row : "-";
      const colText = payload.col !== undefined && payload.col !== null ? payload.col : "-";
      logEvent(`${payload.phase} row=${rowText} col=${colText}`);
      break;
    case "scan_result":
      state.currentCell = { row: payload.row, col: payload.col };
      updateProgress(payload.progress);
      renderDefects(payload.defects || [], payload.row, payload.col);
      drawCanvas();
      logEvent(`result.json parsed row=${payload.row} col=${payload.col} defects=${(payload.defects || []).length}`);
      break;
    case "scan_error":
      if (!payload.request_id || payload.error) {
        state.scanning = false;
        setScanButtons(false);
      }
      el.phaseValue.textContent = "Error";
      logEvent(`ERROR: ${payload.message || payload.error || "scan_error"}`);
      break;
    case "scan_complete":
      state.scanning = false;
      setScanButtons(false);
      updateProgress(payload.progress);
      el.phaseValue.textContent = "Complete";
      logEvent("scan_complete");
      break;
    case "scan_stopped":
      state.scanning = false;
      setScanButtons(false);
      updateProgress(payload.progress);
      el.phaseValue.textContent = "Stopped";
      logEvent(payload.message || "scan_stopped");
      break;
    default:
      logEvent(`packet ${packet.type}`);
  }
}

function setScanButtons(scanning) {
  el.startBtn.disabled = scanning;
  el.startBtn.textContent = scanning ? "盲扫中" : "自动盲扫";
  el.stopBtn.classList.toggle("armed", scanning);
}

async function startScan() {
  const status = await refreshSystemStatus({ log: true });
  if (!el.mockMode.checked && status) {
    const captureReady = status.camera && status.camera.capture_image && status.camera.capture_image.exists;
    const weightsReady = status.algorithm && status.algorithm.weights && status.algorithm.weights.exists;
    const scriptReady = status.algorithm && status.algorithm.detect_script && status.algorithm.detect_script.exists;
    if (!captureReady || !weightsReady || !scriptReady) {
      logEvent("真实模式未启动：抓拍图片、YOLO权重或检测脚本未就绪。");
      el.phaseValue.textContent = "Preflight";
      return;
    }
  }

  state.scanning = true;
  setScanButtons(true);
  state.results.clear();
  el.defectList.innerHTML = "";
  el.progressBar.style.width = "0%";
  const sent = send("start_scan", {
    pcb_length_mm: Number(el.pcbLength.value),
    pcb_width_mm: Number(el.pcbWidth.value),
    origin_x_mm: Number(el.originX.value),
    origin_y_mm: Number(el.originY.value),
    image_path: el.imagePath.value.trim(),
    capture_mode: el.mockMode.checked ? "mock" : "real",
  });
  if (!sent) {
    state.scanning = false;
    setScanButtons(false);
  }
}

function selectCellFromCanvas(event) {
  if (!state.plan) return;
  const rect = el.canvas.getBoundingClientRect();
  const ratioX = el.canvas.width / rect.width;
  const ratioY = el.canvas.height / rect.height;
  const px = (event.clientX - rect.left) * ratioX;
  const py = (event.clientY - rect.top) * ratioY;
  const pad = 34;
  const cols = state.plan.cols;
  const rows = state.plan.rows;
  const tile = Math.min((el.canvas.width - pad * 2) / cols, (el.canvas.height - pad * 2) / rows);
  const gridW = tile * cols;
  const gridH = tile * rows;
  const ox = (el.canvas.width - gridW) / 2;
  const oy = (el.canvas.height - gridH) / 2;
  if (px < ox || py < oy || px > ox + gridW || py > oy + gridH) return;
  const col = Math.min(cols - 1, Math.max(0, Math.floor((px - ox) / tile)));
  const row = Math.min(rows - 1, Math.max(0, Math.floor((py - oy) / tile)));
  state.currentCell = { row, col };
  const x = state.plan.origin_mm.x + col * state.plan.fov_mm.width;
  const y = state.plan.origin_mm.y + row * state.plan.fov_mm.height;
  el.xyReadout.textContent = `${x.toFixed(1)}, ${y.toFixed(1)}`;
  logEvent(`选中网格 row=${row} col=${col}`);
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
  logEvent(`导航到: ${button.getAttribute("title")}`);
}

for (const input of [el.pcbLength, el.pcbWidth, el.originX, el.originY]) {
  input.addEventListener("change", previewGrid);
  input.addEventListener("input", () => window.clearTimeout(input._timer));
  input.addEventListener("input", () => {
    input._timer = window.setTimeout(previewGrid, 220);
  });
}

el.pingBtn.addEventListener("click", () => send("ping"));
el.stopBtn.addEventListener("click", () => send("stop_scan"));
el.refreshStatusBtn.addEventListener("click", () => refreshSystemStatus({ log: true }));
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
previewGrid();
refreshSystemStatus();
renderDefects([], 0, 0);
