(function initAoiUiHelpers(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AoiUiHelpers = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function createAoiUiHelpers() {
  function preferredPhoneUploadUrl(urls, currentOrigin = "") {
    const values = Array.isArray(urls) ? urls : [];
    const candidate = values.find((value) => typeof value === "string" && value.trim());
    const fallback = currentOrigin ? `${String(currentOrigin).replace(/\/$/, "")}/phone` : "";
    const value = candidate ? candidate.trim() : fallback;
    if (!value) return "";
    try {
      return currentOrigin ? new URL(value, currentOrigin).href : value;
    } catch (_error) {
      return value;
    }
  }

  function normalizedSourceCell(source) {
    if (!source || typeof source !== "object") return null;
    const row = Number(source.row);
    const col = Number(source.col);
    if (!Number.isInteger(row) || !Number.isInteger(col) || row < 0 || col < 0) return null;
    return {
      row,
      col,
      annotated_image: typeof source.annotated_image === "string" ? source.annotated_image : "",
      raw_image: typeof source.raw_image === "string" ? source.raw_image : "",
    };
  }

  function resolveDefectSourceCell(defect) {
    if (!defect || typeof defect !== "object") return null;
    const cellMatch = typeof defect.cell === "string" ? defect.cell.match(/^(\d+):(\d+)$/) : null;
    if (cellMatch) {
      return normalizedSourceCell({
        row: Number(cellMatch[1]),
        col: Number(cellMatch[2]),
        annotated_image: defect.annotated_image,
        raw_image: defect.raw_image,
      });
    }

    const direct = normalizedSourceCell(defect.source_cell);
    if (direct) return direct;

    const candidates = Array.isArray(defect.source_candidates)
      ? defect.source_candidates.slice().sort((left, right) => Number(right.confidence || 0) - Number(left.confidence || 0))
      : [];
    for (const candidate of candidates) {
      const source = normalizedSourceCell(candidate && candidate.source_cell);
      if (source) return source;
    }

    const supportCells = Array.isArray(defect.support_cells) ? defect.support_cells : [];
    for (const supportCell of supportCells) {
      const source = normalizedSourceCell(supportCell);
      if (source) return source;
    }
    return null;
  }

  function phoneExperimentProfileFromPlatform(platform) {
    const value = String(platform || "huawei_cloud").trim().toLowerCase();
    if (value === "phone_browser" || value === "phone_link_sim") return "phone_link_sim";
    if (value === "local_dnn" || value === "local_link_sim") return "local_link_sim";
    return "cloud_baseline";
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function normalizedPhoneViewport(viewport) {
    return {
      scale: Math.max(1, Math.min(8, finiteNumber(viewport && viewport.scale, 1))),
      offsetX: finiteNumber(viewport && viewport.offsetX, 0),
      offsetY: finiteNumber(viewport && viewport.offsetY, 0),
    };
  }

  function phoneViewportRect(baseRect, viewport) {
    const normalized = normalizedPhoneViewport(viewport);
    const width = finiteNumber(baseRect && baseRect.w, 0) * normalized.scale;
    const height = finiteNumber(baseRect && baseRect.h, 0) * normalized.scale;
    return {
      x: finiteNumber(baseRect && baseRect.x, 0) + normalized.offsetX - (width - finiteNumber(baseRect && baseRect.w, 0)) / 2,
      y: finiteNumber(baseRect && baseRect.y, 0) + normalized.offsetY - (height - finiteNumber(baseRect && baseRect.h, 0)) / 2,
      w: width,
      h: height,
    };
  }

  function clampPhoneViewport(viewport, baseRect, canvasSize, minVisible = 80) {
    const normalized = normalizedPhoneViewport(viewport);
    if (normalized.scale <= 1) return { scale: 1, offsetX: 0, offsetY: 0 };
    const rect = phoneViewportRect(baseRect, normalized);
    const visible = Math.max(24, finiteNumber(minVisible, 80));
    const minX = visible - rect.w;
    const maxX = finiteNumber(canvasSize && canvasSize.width, 0) - visible;
    const minY = visible - rect.h;
    const maxY = finiteNumber(canvasSize && canvasSize.height, 0) - visible;
    const x = Math.max(minX, Math.min(maxX, rect.x));
    const y = Math.max(minY, Math.min(maxY, rect.y));
    return {
      scale: normalized.scale,
      offsetX: x - finiteNumber(baseRect && baseRect.x, 0) + (rect.w - finiteNumber(baseRect && baseRect.w, 0)) / 2,
      offsetY: y - finiteNumber(baseRect && baseRect.y, 0) + (rect.h - finiteNumber(baseRect && baseRect.h, 0)) / 2,
    };
  }

  function zoomPhoneViewport(viewport, point, scaleFactor, baseRect, canvasSize) {
    const current = normalizedPhoneViewport(viewport);
    const currentRect = phoneViewportRect(baseRect, current);
    const nextScale = Math.max(1, Math.min(8, current.scale * finiteNumber(scaleFactor, 1)));
    const pointX = finiteNumber(point && point.x, currentRect.x + currentRect.w / 2);
    const pointY = finiteNumber(point && point.y, currentRect.y + currentRect.h / 2);
    const ratioX = currentRect.w > 0 ? (pointX - currentRect.x) / currentRect.w : 0.5;
    const ratioY = currentRect.h > 0 ? (pointY - currentRect.y) / currentRect.h : 0.5;
    const nextWidth = finiteNumber(baseRect && baseRect.w, 0) * nextScale;
    const nextHeight = finiteNumber(baseRect && baseRect.h, 0) * nextScale;
    const nextX = pointX - ratioX * nextWidth;
    const nextY = pointY - ratioY * nextHeight;
    return clampPhoneViewport(
      {
        scale: nextScale,
        offsetX: nextX - finiteNumber(baseRect && baseRect.x, 0) + (nextWidth - finiteNumber(baseRect && baseRect.w, 0)) / 2,
        offsetY: nextY - finiteNumber(baseRect && baseRect.y, 0) + (nextHeight - finiteNumber(baseRect && baseRect.h, 0)) / 2,
      },
      baseRect,
      canvasSize,
    );
  }

  function panPhoneViewport(viewport, deltaX, deltaY, baseRect, canvasSize) {
    const current = normalizedPhoneViewport(viewport);
    return clampPhoneViewport(
      {
        scale: current.scale,
        offsetX: current.offsetX + finiteNumber(deltaX, 0),
        offsetY: current.offsetY + finiteNumber(deltaY, 0),
      },
      baseRect,
      canvasSize,
    );
  }

  function partitionPhoneUploads(uploads, currentBatchId) {
    const batchId = String(currentBatchId || "").trim();
    const values = Array.isArray(uploads) ? uploads.slice() : [];
    const current = batchId
      ? values
          .filter((item) => String((item && item.batch_id) || "") === batchId)
          .sort((left, right) => Number(left.batch_index || 0) - Number(right.batch_index || 0))
      : [];
    const history = batchId
      ? values.filter((item) => String((item && item.batch_id) || "") !== batchId)
      : values;
    return { current, history };
  }

  function phoneBatchSelectionError(count, limit = 9) {
    const selected = Number(count);
    const maximum = Math.max(1, Number(limit) || 9);
    if (!Number.isInteger(selected) || selected < 1) return "请至少选择 1 张照片";
    if (selected > maximum) return `每次最多选择 ${maximum} 张照片`;
    return "";
  }

  function latestPhoneUploadId(uploads) {
    const values = Array.isArray(uploads) ? uploads.filter((item) => item && item.upload_id) : [];
    if (!values.length) return "";
    return values.slice().sort((left, right) => {
      const timeDifference = Date.parse(right.uploaded_at || "") - Date.parse(left.uploaded_at || "");
      if (Number.isFinite(timeDifference) && timeDifference !== 0) return timeDifference;
      return String(right.upload_id).localeCompare(String(left.upload_id));
    })[0].upload_id;
  }

  function normalizePhoneUploadSelection(uploadIds) {
    const seen = new Set();
    const normalized = [];
    for (const value of Array.isArray(uploadIds) ? uploadIds : []) {
      const uploadId = String(value || "").trim();
      if (!uploadId || seen.has(uploadId)) continue;
      seen.add(uploadId);
      normalized.push(uploadId);
    }
    return normalized;
  }

  function clampWorkspaceGeometry(geometry, viewport, minSize = { width: 900, height: 600 }) {
    const viewportWidth = Math.max(1, finiteNumber(viewport && viewport.width, 1));
    const viewportHeight = Math.max(1, finiteNumber(viewport && viewport.height, 1));
    const minimumWidth = Math.min(viewportWidth, Math.max(320, finiteNumber(minSize && minSize.width, 900)));
    const minimumHeight = Math.min(viewportHeight, Math.max(320, finiteNumber(minSize && minSize.height, 600)));
    const width = Math.max(minimumWidth, Math.min(viewportWidth, finiteNumber(geometry && geometry.width, viewportWidth * 0.92)));
    const height = Math.max(minimumHeight, Math.min(viewportHeight, finiteNumber(geometry && geometry.height, viewportHeight - 24)));
    const left = Math.max(0, Math.min(viewportWidth - width, finiteNumber(geometry && geometry.left, (viewportWidth - width) / 2)));
    const top = Math.max(0, Math.min(viewportHeight - height, finiteNumber(geometry && geometry.top, (viewportHeight - height) / 2)));
    return { left, top, width, height };
  }

  function resizeWorkspaceGeometry(geometry, edge, deltaX, deltaY, viewport, minSize) {
    const current = clampWorkspaceGeometry(geometry, viewport, minSize);
    const direction = String(edge || "").toLowerCase();
    const dx = finiteNumber(deltaX, 0);
    const dy = finiteNumber(deltaY, 0);
    let { left, top, width, height } = current;
    if (direction.includes("e")) width += dx;
    if (direction.includes("s")) height += dy;
    if (direction.includes("w")) {
      left += dx;
      width -= dx;
    }
    if (direction.includes("n")) {
      top += dy;
      height -= dy;
    }
    return clampWorkspaceGeometry({ left, top, width, height }, viewport, minSize);
  }

  function normalizeWorkspaceColumnRatios(ratios) {
    const defaults = [0.3, 0.4, 0.3];
    if (!Array.isArray(ratios) || ratios.length !== 3) return defaults.slice();
    const values = ratios.map((value) => Number(value));
    if (values.some((value) => !Number.isFinite(value) || value <= 0)) return defaults.slice();
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!Number.isFinite(total) || total <= 0) return defaults.slice();
    return values.map((value) => value / total);
  }

  function resizeWorkspaceColumnRatios(ratios, dividerIndex, deltaX, totalWidth, minWidths) {
    const normalized = normalizeWorkspaceColumnRatios(ratios);
    const width = Math.max(1, finiteNumber(totalWidth, 1));
    const minimums = Array.isArray(minWidths) && minWidths.length === 3
      ? minWidths.map((value) => Math.max(0, finiteNumber(value, 0)))
      : [260, 320, 300];
    const divider = Number(dividerIndex);
    if (divider !== 0 && divider !== 1) return normalized;

    const widths = normalized.map((value) => value * width);
    const leftIndex = divider;
    const rightIndex = divider + 1;
    const pairWidth = widths[leftIndex] + widths[rightIndex];
    const minLeft = Math.min(minimums[leftIndex], pairWidth);
    const minRight = Math.min(minimums[rightIndex], Math.max(0, pairWidth - minLeft));
    const requestedLeft = widths[leftIndex] + finiteNumber(deltaX, 0);
    widths[leftIndex] = Math.max(minLeft, Math.min(pairWidth - minRight, requestedLeft));
    widths[rightIndex] = pairWidth - widths[leftIndex];
    return widths.map((value) => value / width);
  }

  function normalizeWorkspaceView(view) {
    const value = String(view || "balanced").trim().toLowerCase();
    return ["balanced", "control", "scan", "review"].includes(value) ? value : "balanced";
  }

  function nextWorkspaceView(currentView, requestedView) {
    const current = normalizeWorkspaceView(currentView);
    const requested = normalizeWorkspaceView(requestedView);
    if (requested === "balanced") return "balanced";
    return current === requested ? "balanced" : requested;
  }

  function normalizeProgressPercent(value) {
    const percent = finiteNumber(value, 0);
    return Math.round(Math.max(0, Math.min(100, percent)));
  }

  function workspaceStatusTone(status) {
    const value = status && typeof status === "object" ? status : {};
    if (!value.websocketOnline || value.statusRequestFailed) return "bad";
    if (!value.statusAvailable || !value.requirementsReady) return "warn";
    return "good";
  }

  function diagnosisArtifactControls(options) {
    const value = options && typeof options === "object" ? options : {};
    const mode = String(value.mode || "").trim().toLowerCase();
    const running = Boolean(value.running);
    const showMosaicSelect = mode === "real" || mode === "simulation";
    if (mode === "phone") {
      const phoneBatchReady = Boolean(value.phoneBatchReady);
      return {
        showMosaicSelect: false,
        mosaicDisabled: true,
        exportDisabled: running || (!phoneBatchReady && !value.hasPhoneAnnotated),
        exportLabel: phoneBatchReady ? "导出批次标记图" : "导出标记图",
      };
    }
    const annotatedFrameCount = Math.max(1, Math.round(finiteNumber(value.annotatedFrameCount, 16)));
    return {
      showMosaicSelect,
      mosaicDisabled: !showMosaicSelect || running || !value.hasMosaic,
      exportDisabled: running || !value.hasAnnotatedFrames,
      exportLabel: `导出${annotatedFrameCount}张标注图`,
    };
  }

  function defectChatReference(defect) {
    const source = resolveDefectSourceCell(defect);
    const bbox = defect && (defect.bbox || defect.xyxy);
    const type = String(defect && (defect.type || defect.label || defect.name) || "").trim();
    if (!source || !type || !Array.isArray(bbox) || bbox.length !== 4) return null;
    const normalizedBbox = bbox.map((value) => Number(value));
    if (normalizedBbox.some((value) => !Number.isFinite(value))) return null;
    return { row: source.row, col: source.col, type, bbox: normalizedBbox };
  }

  function parseSseFrames(buffer) {
    const normalized = String(buffer || "").replace(/\r\n/g, "\n");
    const blocks = normalized.split("\n\n");
    const rest = blocks.pop() || "";
    const events = [];
    for (const block of blocks) {
      let event = "message";
      const dataLines = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
      }
      if (!dataLines.length) continue;
      const dataText = dataLines.join("\n");
      let data = dataText;
      try {
        data = JSON.parse(dataText);
      } catch (_error) {
        // Plain text SSE data is valid and remains a string.
      }
      events.push({ event, data });
    }
    return { events, rest };
  }

  return {
    preferredPhoneUploadUrl,
    resolveDefectSourceCell,
    phoneExperimentProfileFromPlatform,
    phoneViewportRect,
    clampPhoneViewport,
    zoomPhoneViewport,
    panPhoneViewport,
    partitionPhoneUploads,
    phoneBatchSelectionError,
    latestPhoneUploadId,
    normalizePhoneUploadSelection,
    clampWorkspaceGeometry,
    resizeWorkspaceGeometry,
    normalizeWorkspaceColumnRatios,
    resizeWorkspaceColumnRatios,
    normalizeWorkspaceView,
    nextWorkspaceView,
    normalizeProgressPercent,
    workspaceStatusTone,
    diagnosisArtifactControls,
    defectChatReference,
    parseSseFrames,
  };
}));
