(function () {
  "use strict";

  const bridge = window.AoiDiagnosisBridge;
  const helpers = window.AoiUiHelpers;
  if (!bridge || !helpers) return;

  const dom = {
    expand: document.getElementById("expandDiagnosisBtn"),
    overlay: document.getElementById("diagnosisWorkspace"),
    window: document.getElementById("diagnosisWorkspaceWindow"),
    titlebar: document.getElementById("diagnosisWorkspaceTitlebar"),
    body: document.getElementById("diagnosisWorkspaceBody"),
    columnDividers: Array.from(document.querySelectorAll("[data-column-divider]")),
    minimize: document.getElementById("minimizeDiagnosisBtn"),
    close: document.getElementById("closeDiagnosisBtn"),
    restore: document.getElementById("restoreDiagnosisBtn"),
    restoreStatus: document.getElementById("restoreDiagnosisStatus"),
    scope: document.getElementById("workspaceScopeLabel"),
    model: document.getElementById("workspaceLlmModel"),
    thinking: document.getElementById("workspaceThinkingToggle"),
    generateReport: document.getElementById("workspaceGenerateReportBtn"),
    downloadReport: document.getElementById("workspaceDownloadReportBtn"),
    downloadChat: document.getElementById("workspaceDownloadChatBtn"),
    generationStatus: document.getElementById("workspaceGenerationStatus"),
    reportTarget: document.getElementById("workspaceReportTargetText"),
    contextStatus: document.getElementById("workspaceContextStatus"),
    downloadArtifacts: document.getElementById("workspaceDownloadArtifactsBtn"),
    selectMosaic: document.getElementById("workspaceSelectMosaicBtn"),
    reportStatus: document.getElementById("workspaceReportStatus"),
    reportContent: document.getElementById("workspaceReportContent"),
    history: document.getElementById("workspaceChatHistory"),
    contextSummary: document.getElementById("workspaceContextSummary"),
    input: document.getElementById("workspaceChatInput"),
    send: document.getElementById("workspaceSendChatBtn"),
    cancel: document.getElementById("workspaceCancelChatBtn"),
    previewTitle: document.getElementById("workspacePreviewTitle"),
    previewMeta: document.getElementById("workspacePreviewMeta"),
    pcbCanvas: document.getElementById("workspacePcbCanvas"),
    phonePreview: document.getElementById("workspacePhonePreview"),
    listTitle: document.getElementById("workspaceListTitle"),
    selectionCount: document.getElementById("workspaceSelectionCount"),
    contextList: document.getElementById("workspaceContextList"),
  };

  if (!dom.overlay || !dom.window) return;

  const local = {
    open: false,
    minimized: false,
    snapshot: null,
    session: null,
    sessionKey: "",
    sessionLoading: false,
    streamController: null,
    streamText: "",
    streamPhase: "",
    pendingUser: "",
    reportKey: "",
    geometry: null,
    pointer: null,
    columnRatios: null,
    columnPointer: null,
  };

  const DEFAULT_COLUMN_RATIOS = [0.3, 0.4, 0.3];
  const MIN_COLUMN_WIDTHS = [260, 320, 300];
  const COLUMN_DIVIDER_WIDTH = 6;
  const COLUMN_STORAGE_KEY = "aoiDiagnosisWorkspaceColumnRatios";

  function viewport() {
    return { width: window.innerWidth, height: window.innerHeight };
  }

  function defaultGeometry() {
    const view = viewport();
    const width = view.width * 0.92;
    const height = Math.max(320, view.height - 24);
    return helpers.clampWorkspaceGeometry(
      { left: (view.width - width) / 2, top: (view.height - height) / 2, width, height },
      view,
      { width: 900, height: 600 },
    );
  }

  function readGeometry() {
    try {
      const stored = JSON.parse(window.localStorage.getItem("aoiDiagnosisWorkspaceGeometry") || "null");
      return helpers.clampWorkspaceGeometry(stored || defaultGeometry(), viewport(), { width: 900, height: 600 });
    } catch (_error) {
      return defaultGeometry();
    }
  }

  function applyGeometry(geometry, save = false) {
    const view = viewport();
    local.geometry = view.width <= 960 || view.height <= 640
      ? { left: 0, top: 0, width: view.width, height: view.height }
      : helpers.clampWorkspaceGeometry(geometry || readGeometry(), view, { width: 900, height: 600 });
    dom.window.style.left = `${Math.round(local.geometry.left)}px`;
    dom.window.style.top = `${Math.round(local.geometry.top)}px`;
    dom.window.style.width = `${Math.round(local.geometry.width)}px`;
    dom.window.style.height = `${Math.round(local.geometry.height)}px`;
    if (save) window.localStorage.setItem("aoiDiagnosisWorkspaceGeometry", JSON.stringify(local.geometry));
    window.requestAnimationFrame(copyPcbCanvas);
  }

  function stackedLayout() {
    return window.matchMedia("(max-width: 960px), (max-height: 640px)").matches;
  }

  function readColumnRatios() {
    try {
      const stored = JSON.parse(window.localStorage.getItem(COLUMN_STORAGE_KEY) || "null");
      return helpers.normalizeWorkspaceColumnRatios(stored || DEFAULT_COLUMN_RATIOS);
    } catch (_error) {
      return DEFAULT_COLUMN_RATIOS.slice();
    }
  }

  function applyColumnRatios(ratios, save = false) {
    local.columnRatios = helpers.normalizeWorkspaceColumnRatios(ratios || readColumnRatios());
    const disabled = stackedLayout();
    if (disabled) {
      dom.body.style.removeProperty("grid-template-columns");
    } else {
      const [report, chat, context] = local.columnRatios;
      dom.body.style.gridTemplateColumns = [
        `minmax(${MIN_COLUMN_WIDTHS[0]}px, ${report}fr)`,
        `${COLUMN_DIVIDER_WIDTH}px`,
        `minmax(${MIN_COLUMN_WIDTHS[1]}px, ${chat}fr)`,
        `${COLUMN_DIVIDER_WIDTH}px`,
        `minmax(${MIN_COLUMN_WIDTHS[2]}px, ${context}fr)`,
      ].join(" ");
    }
    dom.columnDividers.forEach((divider, index) => {
      const cumulative = index === 0
        ? local.columnRatios[0]
        : local.columnRatios[0] + local.columnRatios[1];
      divider.setAttribute("aria-valuenow", String(Math.round(cumulative * 100)));
      divider.setAttribute("aria-disabled", String(disabled));
    });
    if (save) window.localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(local.columnRatios));
    window.requestAnimationFrame(copyPcbCanvas);
  }

  function openWorkspace() {
    local.open = true;
    local.minimized = false;
    dom.overlay.classList.add("is-open");
    dom.overlay.setAttribute("aria-hidden", "false");
    dom.restore.hidden = true;
    document.body.classList.add("diagnosis-workspace-open");
    applyGeometry(local.geometry || readGeometry());
    applyColumnRatios(local.columnRatios || readColumnRatios());
    updateFromBridge(true);
    dom.input.focus();
  }

  function minimizeWorkspace() {
    if (!local.open) return;
    local.minimized = true;
    dom.overlay.classList.remove("is-open");
    dom.overlay.setAttribute("aria-hidden", "true");
    dom.restore.hidden = false;
    document.body.classList.remove("diagnosis-workspace-open");
    dom.restoreStatus.textContent = local.streamController ? "AI 正在生成，点击恢复" : "点击恢复";
  }

  function closeWorkspace() {
    local.open = false;
    local.minimized = false;
    dom.overlay.classList.remove("is-open");
    dom.overlay.setAttribute("aria-hidden", "true");
    dom.restore.hidden = true;
    document.body.classList.remove("diagnosis-workspace-open");
  }

  function scopeKey(query) {
    if (!query) return "";
    return [query.mode, query.scan_id || "", query.batch_id || "", query.upload_id || ""].join(":");
  }

  function scopeLabel(snapshot) {
    const query = snapshot && snapshot.sessionQuery;
    if (!query) return "等待检测对象";
    if (query.mode === "phone") return query.batch_id ? `手机批次 ${query.batch_id}` : `手机照片 ${query.upload_id}`;
    return `${query.mode === "real" ? "真实" : "模拟"}扫描 ${query.scan_id}`;
  }

  function queryString(query) {
    const params = new URLSearchParams();
    Object.entries(query || {}).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") params.set(key, String(value));
    });
    return params.toString();
  }

  async function loadSession(force = false) {
    const query = bridge.sessionQuery();
    const key = scopeKey(query);
    if (!query) {
      local.session = null;
      local.sessionKey = "";
      renderHistory();
      return;
    }
    if (!force && (local.sessionLoading || (local.session && local.sessionKey === key))) return;
    local.sessionLoading = true;
    local.sessionKey = key;
    try {
      const response = await fetch(`/api/llm/chat/session?${queryString(query)}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `session ${response.status}`);
      if (local.sessionKey === key) local.session = payload;
    } catch (error) {
      local.session = null;
      dom.generationStatus.textContent = error && error.message ? error.message : String(error);
    } finally {
      local.sessionLoading = false;
      renderHistory();
    }
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderMarkdown(value) {
    const escaped = escapeHtml(value);
    return escaped
      .replace(/^### (.+)$/gm, "<h4>$1</h4>")
      .replace(/^## (.+)$/gm, "<h3>$1</h3>")
      .replace(/^# (.+)$/gm, "<h2>$1</h2>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
  }

  function appendMessageElement(role, content, meta, streaming = false) {
    const article = document.createElement("article");
    article.className = `workspace-chat-message ${role}${streaming ? " streaming" : ""}`;
    const header = document.createElement("header");
    header.innerHTML = `<strong>${role === "user" ? "你" : "AI"}</strong><span>${escapeHtml(meta || "")}</span>`;
    const body = document.createElement("div");
    body.className = "workspace-markdown";
    body.innerHTML = renderMarkdown(content);
    article.append(header, body);
    dom.history.append(article);
  }

  function renderHistory() {
    if (!dom.history) return;
    dom.history.innerHTML = "";
    const messages = local.session && Array.isArray(local.session.messages) ? local.session.messages : [];
    if (!messages.length && !local.pendingUser && !local.streamText) {
      const empty = document.createElement("p");
      empty.className = "workspace-empty";
      empty.textContent = "生成诊断报告或输入问题后，连续会话会显示在这里。";
      dom.history.append(empty);
    }
    for (const message of messages) {
      const meta = message.kind === "report"
        ? "诊断报告"
        : message.elapsed_ms
          ? `${Math.round(Number(message.elapsed_ms))}ms`
          : "";
      appendMessageElement(message.role, message.content || "", meta);
    }
    if (local.pendingUser) appendMessageElement("user", local.pendingUser, "正在发送");
    if (local.streamText || local.streamPhase) {
      appendMessageElement("assistant", local.streamText || local.streamPhase, local.streamPhase, true);
    }
    dom.history.scrollTop = dom.history.scrollHeight;
  }

  function currentReportKey(snapshot) {
    const report = snapshot && snapshot.lastReport;
    if (!report) return "";
    return [report.generated_at || "", report.report_path || "", report.elapsed_ms || ""].join(":");
  }

  function renderReport(snapshot) {
    const report = snapshot.lastReport;
    dom.reportContent.textContent = report && report.report_markdown
      ? report.report_markdown
      : "选择检测结果后可生成诊断报告。";
    dom.reportStatus.textContent = snapshot.reportBusy
      ? "正在生成"
      : report
        ? `${report.model || snapshot.llmModel} · ${Math.round(Number(report.elapsed_ms || 0))}ms`
        : snapshot.reportMessage || "尚未生成";
  }

  function copyPcbCanvas() {
    const snapshot = local.snapshot;
    if (!local.open || !snapshot || snapshot.mode === "phone" || !snapshot.canvas) return;
    const source = snapshot.canvas;
    const rect = dom.pcbCanvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width * (window.devicePixelRatio || 1)));
    const height = Math.max(1, Math.round(rect.height * (window.devicePixelRatio || 1)));
    if (dom.pcbCanvas.width !== width || dom.pcbCanvas.height !== height) {
      dom.pcbCanvas.width = width;
      dom.pcbCanvas.height = height;
    }
    const ctx = dom.pcbCanvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#f8fafc";
    ctx.fillRect(0, 0, width, height);
    if (!source.width || !source.height) return;
    const scale = Math.min(width / source.width, height / source.height);
    const drawWidth = source.width * scale;
    const drawHeight = source.height * scale;
    ctx.drawImage(source, (width - drawWidth) / 2, (height - drawHeight) / 2, drawWidth, drawHeight);
  }

  function createContextCheckbox(checked, label, onChange) {
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = checked;
    checkbox.setAttribute("aria-label", label);
    checkbox.addEventListener("change", () => onChange(checkbox.checked));
    return checkbox;
  }

  function renderDefectContext(snapshot) {
    dom.previewTitle.textContent = "PCB 检测示意图";
    dom.previewMeta.textContent = snapshot.defects.length ? `${snapshot.defects.length} 个最终缺陷` : "等待检测结果";
    dom.pcbCanvas.hidden = false;
    dom.phonePreview.hidden = true;
    dom.listTitle.textContent = "缺陷账本";
    dom.selectionCount.textContent = `已选 ${snapshot.selectedDefectKeys.length} 项`;
    dom.contextSummary.textContent = snapshot.selectedDefectKeys.length
      ? `已选择 ${snapshot.selectedDefectKeys.length} 个缺陷，将连同来源单格和整板图发送。`
      : "未勾选缺陷，将使用当前报告目标。";
    dom.contextList.innerHTML = "";
    const selected = new Set(snapshot.selectedDefectKeys);
    const defects = snapshot.defects.slice().reverse();
    if (!defects.length) {
      dom.contextList.innerHTML = '<p class="workspace-empty">暂无最终缺陷。</p>';
    }
    for (const defect of defects) {
      const row = document.createElement("div");
      row.className = `workspace-context-item${selected.has(defect.selection_key) ? " selected" : ""}${snapshot.currentDefectKey === defect.selection_key ? " focused" : ""}`;
      const checkbox = createContextCheckbox(
        selected.has(defect.selection_key),
        `选择 ${defect.label || defect.type || "缺陷"}`,
        (checked) => bridge.toggleDefect(defect.selection_key, checked),
      );
      const button = document.createElement("button");
      button.type = "button";
      button.innerHTML = `<strong>${escapeHtml(defect.label || defect.type || "unknown")}</strong><span>${Math.round(Number(defect.confidence || 0) * 100)}% · ${escapeHtml(defect.cell || "-")}</span>`;
      button.addEventListener("click", () => bridge.navigateDefect(defect.selection_key));
      row.append(checkbox, button);
      dom.contextList.append(row);
    }
    window.requestAnimationFrame(copyPcbCanvas);
  }

  function renderPhoneContext(snapshot) {
    const selected = new Set(snapshot.selectedPhoneUploadIds);
    const currentIds = new Set(snapshot.currentPhoneBatchItems || []);
    const current = snapshot.currentPhoneUpload;
    dom.previewTitle.textContent = "手机照片预览";
    dom.previewMeta.textContent = current ? current.name || current.upload_id : "请选择照片";
    dom.pcbCanvas.hidden = true;
    dom.phonePreview.hidden = !current;
    if (current) {
      dom.phonePreview.src = current.preview_url || "";
      dom.phonePreview.alt = current.name || current.upload_id;
    } else {
      dom.phonePreview.removeAttribute("src");
    }
    dom.listTitle.textContent = "本批次与历史照片";
    dom.selectionCount.textContent = selected.size > 9 ? `已选 ${selected.size} 张 · 最多 9 张` : `已选 ${selected.size} 张`;
    dom.contextSummary.textContent = selected.size
      ? selected.size > 9
        ? `已选择 ${selected.size} 张，聊天上下文最多 9 张，请减少选择。`
        : `已选择 ${selected.size} 张照片，将优先发送标注图。`
      : "未勾选照片，将使用当前预览照片。";
    dom.contextList.innerHTML = "";
    const ordered = snapshot.phoneUploads.slice().sort((left, right) => {
      const leftCurrent = currentIds.has(left.upload_id) ? 1 : 0;
      const rightCurrent = currentIds.has(right.upload_id) ? 1 : 0;
      if (leftCurrent !== rightCurrent) return rightCurrent - leftCurrent;
      return Date.parse(right.uploaded_at || "") - Date.parse(left.uploaded_at || "");
    });
    if (!ordered.length) dom.contextList.innerHTML = '<p class="workspace-empty">暂无手机上传照片。</p>';
    for (const item of ordered) {
      const row = document.createElement("div");
      row.className = `workspace-photo-item${selected.has(item.upload_id) ? " selected" : ""}${snapshot.selectedPhoneUploadId === item.upload_id ? " focused" : ""}`;
      const checkbox = createContextCheckbox(
        selected.has(item.upload_id),
        `选择 ${item.name || item.upload_id}`,
        (checked) => bridge.togglePhoneUpload(item.upload_id, checked),
      );
      const button = document.createElement("button");
      button.type = "button";
      const image = document.createElement("img");
      image.src = item.preview_url || "";
      image.alt = item.name || item.upload_id;
      const body = document.createElement("span");
      body.innerHTML = `<strong>${escapeHtml(item.name || item.upload_id)}</strong><small>${currentIds.has(item.upload_id) ? "本批次" : "历史"} · 缺陷 ${(item.defects || []).length}</small>`;
      button.append(image, body);
      button.addEventListener("click", () => bridge.selectPhoneUpload(item.upload_id));
      row.append(checkbox, button);
      dom.contextList.append(row);
    }
  }

  function renderContext(snapshot) {
    if (snapshot.mode === "phone") renderPhoneContext(snapshot);
    else renderDefectContext(snapshot);
  }

  function updateControls(snapshot) {
    dom.model.value = snapshot.llmModel;
    dom.thinking.checked = snapshot.llmThinkingEnabled;
    const busy = snapshot.reportBusy || Boolean(local.streamController);
    dom.model.disabled = busy;
    dom.thinking.disabled = busy;
    dom.generateReport.disabled = busy || !snapshot.llmReportConfigured || !snapshot.reportTarget;
    dom.downloadReport.disabled = busy || !snapshot.lastReport;
    dom.downloadChat.disabled = busy || !snapshot.sessionQuery;
    const tooManyPhoneImages = snapshot.mode === "phone" && snapshot.selectedPhoneUploadIds.length > 9;
    dom.send.disabled = busy || !snapshot.llmReportConfigured || !snapshot.sessionQuery || tooManyPhoneImages;
    dom.cancel.disabled = !local.streamController;
    dom.scope.textContent = scopeLabel(snapshot);
    dom.reportTarget.textContent = snapshot.reportTargetText || "请选择已检测单格、整板或手机结果";
    dom.contextStatus.textContent = snapshot.reportStatusText || "等待检测结果";
    const artifactControls = snapshot.artifactControls || {};
    dom.downloadArtifacts.textContent = artifactControls.exportLabel || "导出标注图";
    dom.downloadArtifacts.disabled = artifactControls.exportDisabled !== false;
    dom.selectMosaic.hidden = !artifactControls.showMosaicSelect;
    dom.selectMosaic.disabled = artifactControls.mosaicDisabled !== false;
  }

  function updateFromBridge(forceSession = false) {
    const snapshot = bridge.snapshot();
    const previousScope = scopeKey(local.snapshot && local.snapshot.sessionQuery);
    local.snapshot = snapshot;
    renderReport(snapshot);
    renderContext(snapshot);
    updateControls(snapshot);
    const nextScope = scopeKey(snapshot.sessionQuery);
    const nextReportKey = currentReportKey(snapshot);
    const reportChanged = nextReportKey && nextReportKey !== local.reportKey;
    local.reportKey = nextReportKey;
    if ((forceSession || previousScope !== nextScope || reportChanged) && !local.streamController) {
      loadSession(true);
    }
  }

  function setStreamState(active, phase = "") {
    local.streamPhase = phase;
    if (local.snapshot) updateControls(local.snapshot);
    dom.generationStatus.textContent = phase || (active ? "正在连接 AI" : "等待提问");
    if (local.minimized) dom.restoreStatus.textContent = active ? phase || "AI 正在生成" : "点击恢复";
  }

  async function sendChat() {
    if (local.streamController) return;
    const message = dom.input.value.trim();
    let payload;
    try {
      payload = bridge.buildChatPayload(message);
      if (!message && !(payload.selected_defects || []).length && !(payload.selected_upload_ids || []).length) {
        throw new Error("请输入问题或选择检测上下文。");
      }
      if ((payload.selected_upload_ids || []).length > 9) throw new Error("聊天上下文最多选择 9 张手机照片。");
    } catch (error) {
      dom.generationStatus.textContent = error && error.message ? error.message : String(error);
      return;
    }

    local.streamController = new AbortController();
    local.pendingUser = message || "请分析所选检测上下文。";
    local.streamText = "";
    dom.input.value = "";
    setStreamState(true, "正在准备检测上下文");
    renderHistory();
    try {
      const response = await fetch("/api/llm/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: local.streamController.signal,
      });
      if (!response.ok || !response.body) {
        const errorPayload = await response.json().catch(() => ({}));
        throw new Error(errorPayload.detail || `chat ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let completed = false;
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const parsed = helpers.parseSseFrames(buffer);
        buffer = parsed.rest;
        for (const event of parsed.events) {
          if (event.event === "phase") {
            const phase = event.data && event.data.phase;
            setStreamState(true, phase === "thinking" ? "深度思考中" : event.data.message || "正在生成回答");
          } else if (event.event === "delta") {
            local.streamText += String(event.data && event.data.content || "");
            setStreamState(true, "正在生成回答");
            renderHistory();
          } else if (event.event === "done") {
            completed = true;
            setStreamState(false, `完成 · ${Math.round(Number(event.data && event.data.elapsed_ms || 0))}ms`);
          } else if (event.event === "error") {
            throw new Error(event.data && event.data.message || "AI 聊天失败");
          }
        }
      }
      if (!completed) throw new Error("AI 流式连接提前中断，已保留当前收到的内容。 ");
      local.pendingUser = "";
      local.streamText = "";
      local.streamPhase = "";
      await loadSession(true);
    } catch (error) {
      if (error && error.name === "AbortError") {
        setStreamState(false, "已取消生成；未完成回答不会保存");
      } else {
        setStreamState(false, error && error.message ? error.message : String(error));
      }
      local.pendingUser = "";
      renderHistory();
    } finally {
      local.streamController = null;
      if (local.snapshot) updateControls(local.snapshot);
      dom.cancel.disabled = true;
    }
  }

  function cancelChat() {
    if (local.streamController) local.streamController.abort();
  }

  async function generateReport() {
    dom.generationStatus.textContent = "正在生成诊断报告";
    await bridge.generateReport();
    updateFromBridge(true);
  }

  function downloadChat() {
    const query = bridge.sessionQuery();
    if (!query) return;
    const anchor = document.createElement("a");
    anchor.href = `/api/llm/chat/download?${queryString(query)}`;
    anchor.download = "aoi_ai_conversation.md";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  function beginPointer(event, mode, edge) {
    if (event.button !== 0) return;
    local.pointer = {
      id: event.pointerId,
      mode,
      edge: edge || "",
      startX: event.clientX,
      startY: event.clientY,
      geometry: Object.assign({}, local.geometry || readGeometry()),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function movePointer(event) {
    if (!local.pointer || event.pointerId !== local.pointer.id) return;
    const dx = event.clientX - local.pointer.startX;
    const dy = event.clientY - local.pointer.startY;
    if (local.pointer.mode === "resize") {
      applyGeometry(helpers.resizeWorkspaceGeometry(
        local.pointer.geometry,
        local.pointer.edge,
        dx,
        dy,
        viewport(),
        { width: 900, height: 600 },
      ));
    } else {
      applyGeometry(Object.assign({}, local.pointer.geometry, {
        left: local.pointer.geometry.left + dx,
        top: local.pointer.geometry.top + dy,
      }));
    }
  }

  function endPointer(event) {
    if (!local.pointer || event.pointerId !== local.pointer.id) return;
    local.pointer = null;
    applyGeometry(local.geometry, true);
  }

  function beginColumnPointer(event, dividerIndex) {
    if (event.button !== 0 || stackedLayout()) return;
    const totalWidth = Math.max(
      1,
      dom.body.clientWidth - (COLUMN_DIVIDER_WIDTH * dom.columnDividers.length),
    );
    local.columnPointer = {
      id: event.pointerId,
      dividerIndex,
      startX: event.clientX,
      ratios: (local.columnRatios || readColumnRatios()).slice(),
      totalWidth,
      element: event.currentTarget,
    };
    event.currentTarget.classList.add("is-dragging");
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }

  function moveColumnPointer(event) {
    if (!local.columnPointer || event.pointerId !== local.columnPointer.id) return;
    applyColumnRatios(helpers.resizeWorkspaceColumnRatios(
      local.columnPointer.ratios,
      local.columnPointer.dividerIndex,
      event.clientX - local.columnPointer.startX,
      local.columnPointer.totalWidth,
      MIN_COLUMN_WIDTHS,
    ));
  }

  function endColumnPointer(event) {
    if (!local.columnPointer || event.pointerId !== local.columnPointer.id) return;
    local.columnPointer.element.classList.remove("is-dragging");
    local.columnPointer = null;
    applyColumnRatios(local.columnRatios, true);
  }

  function adjustColumnWithKeyboard(event, dividerIndex) {
    if (stackedLayout() || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    const totalWidth = Math.max(
      1,
      dom.body.clientWidth - (COLUMN_DIVIDER_WIDTH * dom.columnDividers.length),
    );
    const delta = event.key === "ArrowLeft" ? -16 : 16;
    applyColumnRatios(helpers.resizeWorkspaceColumnRatios(
      local.columnRatios || readColumnRatios(),
      dividerIndex,
      delta,
      totalWidth,
      MIN_COLUMN_WIDTHS,
    ), true);
    event.preventDefault();
  }

  dom.expand.addEventListener("click", openWorkspace);
  dom.minimize.addEventListener("click", minimizeWorkspace);
  dom.close.addEventListener("click", closeWorkspace);
  dom.restore.addEventListener("click", openWorkspace);
  dom.model.addEventListener("change", () => bridge.setModel(dom.model.value));
  dom.thinking.addEventListener("change", () => bridge.setThinking(dom.thinking.checked));
  dom.generateReport.addEventListener("click", generateReport);
  dom.downloadReport.addEventListener("click", () => bridge.downloadReport());
  dom.downloadChat.addEventListener("click", downloadChat);
  dom.downloadArtifacts.addEventListener("click", () => bridge.downloadArtifacts());
  dom.selectMosaic.addEventListener("click", () => bridge.selectMosaicReportTarget());
  dom.send.addEventListener("click", sendChat);
  dom.cancel.addEventListener("click", cancelChat);
  dom.input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      sendChat();
    }
  });
  dom.titlebar.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, select, input")) return;
    beginPointer(event, "move", "");
  });
  for (const handle of dom.window.querySelectorAll("[data-resize]")) {
    handle.addEventListener("pointerdown", (event) => beginPointer(event, "resize", handle.dataset.resize));
  }
  dom.columnDividers.forEach((divider) => {
    const dividerIndex = Number(divider.dataset.columnDivider);
    divider.addEventListener("pointerdown", (event) => beginColumnPointer(event, dividerIndex));
    divider.addEventListener("dblclick", () => applyColumnRatios(DEFAULT_COLUMN_RATIOS, true));
    divider.addEventListener("keydown", (event) => adjustColumnWithKeyboard(event, dividerIndex));
  });
  window.addEventListener("pointermove", movePointer);
  window.addEventListener("pointermove", moveColumnPointer);
  window.addEventListener("pointerup", endPointer);
  window.addEventListener("pointerup", endColumnPointer);
  window.addEventListener("pointercancel", endPointer);
  window.addEventListener("pointercancel", endColumnPointer);
  window.addEventListener("resize", () => {
    if (local.geometry) applyGeometry(local.geometry, true);
    applyColumnRatios(local.columnRatios || readColumnRatios());
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && local.open && !local.minimized) closeWorkspace();
  });

  if (typeof ResizeObserver === "function") {
    const previewObserver = new ResizeObserver(() => window.requestAnimationFrame(copyPcbCanvas));
    previewObserver.observe(dom.pcbCanvas.parentElement);
  }

  applyColumnRatios(readColumnRatios());
  bridge.subscribe(() => updateFromBridge(false));
}());
