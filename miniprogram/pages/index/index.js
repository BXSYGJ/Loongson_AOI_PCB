const app = getApp();

const MODES = [
  {
    key: "simulation",
    name: "模拟模式",
    tag: "图片 + 拼接",
    desc: "导入已有 PCB 图片，交给网站模拟滑台检测流程。",
    hint: "小程序可把图片导入网站队列；完整网格扫描、拼图和复核建议在完整网站继续操作。"
  },
  {
    key: "real",
    name: "真实模式",
    tag: "滑台 + 相机",
    desc: "龙芯端负责滑台移动、OV9281 拍照和逐格保存。",
    hint: "真实模式需要在运行网站的龙芯端执行 soft home；小程序只显示状态和入口，不直接下发运动命令。"
  },
  {
    key: "phone",
    name: "手机拍照",
    tag: "拍照上传",
    desc: "拍照或从图库选择 PCB，上传到网站并按所选平台检测。",
    hint: "与手机浏览器 /phone 和完整网站手机拍照模块共享上传队列、检测结果和 LLM 报告。"
  }
];

const PLATFORMS = [
  { key: "huawei_cloud", name: "华为云", desc: "ECS + OBS + OpenCV DNN" },
  { key: "phone_browser", name: "手机", desc: "需要浏览器 /phone worker 在线" },
  { key: "local_dnn", name: "本地电脑/龙芯", desc: "运行网站后端的设备本地推理" }
];

const LLM_MODELS = [
  { id: "qwen3.7-plus", label: "Qwen3.7 Plus" },
  { id: "qwen3.6-flash", label: "Qwen3.6 Flash" },
  { id: "qwen3.7-max", label: "Qwen3.7 Max" }
];

function storedLlmModel() {
  const stored = wx.getStorageSync("aoiLlmModel") || "qwen3.7-plus";
  return LLM_MODELS.some((item) => item.id === stored) ? stored : "qwen3.7-plus";
}

function baseUrlFromApp() {
  return String(app.globalData.consoleApiBase || "http://116.205.117.33:8020").replace(/\/$/, "");
}

function absoluteUrl(base, value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^https?:\/\//i.test(text)) return text;
  if (text.startsWith("/")) return `${base}${text}`;
  return `${base}/api/artifact?path=${encodeURIComponent(text)}`;
}

function basename(filePath) {
  const parts = String(filePath || "").split(/[\\/]/);
  return parts[parts.length - 1] || `pcb_${Date.now()}.jpg`;
}

function formatConfidence(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(3) : String(value || "-");
}

function modeByKey(key) {
  return MODES.find((item) => item.key === key) || MODES[2];
}

function platformByKey(key) {
  return PLATFORMS.find((item) => item.key === key) || PLATFORMS[0];
}

function requestJson(url, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: options.method || "GET",
      data: options.data,
      header: Object.assign({ "Content-Type": "application/json" }, options.header || {}),
      timeout: options.timeout || 30000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
          return;
        }
        const data = res.data || {};
        reject(new Error(data.detail || data.message || `HTTP ${res.statusCode}`));
      },
      fail(err) {
        reject(new Error(err.errMsg || "request failed"));
      }
    });
  });
}

function requestBytes(url, data, contentType) {
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: "POST",
      data,
      header: { "Content-Type": contentType || "application/octet-stream" },
      timeout: 60000,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(typeof res.data === "string" ? JSON.parse(res.data) : res.data);
          return;
        }
        const payload = res.data || {};
        reject(new Error(payload.detail || payload.message || `HTTP ${res.statusCode}`));
      },
      fail(err) {
        reject(new Error(err.errMsg || "upload failed"));
      }
    });
  });
}

function chooseImages(sourceType) {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: 9,
      mediaType: ["image"],
      sourceType: [sourceType],
      sizeType: ["compressed"],
      success(res) {
        const files = Array.isArray(res.tempFiles) ? res.tempFiles.filter((file) => file && file.tempFilePath) : [];
        if (!files.length) {
          reject(new Error("未选择图片"));
          return;
        }
        resolve(files);
      },
      fail(err) {
        reject(new Error(err.errMsg || "选择图片失败"));
      }
    });
  });
}

function readFile(filePath) {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      success(res) {
        resolve(res.data);
      },
      fail(err) {
        reject(new Error(err.errMsg || "读取图片失败"));
      }
    });
  });
}

Page({
  data: {
    modes: MODES,
    platforms: PLATFORMS,
    activeMode: "phone",
    activeModeMeta: modeByKey("phone"),
    detectionPlatform: "huawei_cloud",
    detectionPlatformMeta: platformByKey("huawei_cloud"),
    profile: "precision",
    profileText: "高精模式",
    consoleBase: baseUrlFromApp(),
    consoleBaseInput: baseUrlFromApp(),
    fullWebUrl: `${baseUrlFromApp()}/`,
    phoneWebUrl: `${baseUrlFromApp()}/phone`,
    imagePath: "",
    filename: "",
    selectedFiles: [],
    currentBatchId: wx.getStorageSync("aoiCurrentPhoneBatchId") || "",
    batchResults: [],
    batchStatus: "",
    batchSummary: "尚未创建批次",
    batchRunning: false,
    selectedUploadId: "",
    uploadLabels: ["暂无上传图片"],
    uploads: [],
    statusText: "请选择 PCB 图片",
    error: "",
    busy: false,
    progress: 0,
    consoleStatusText: "未刷新",
    cloudStatusText: "未刷新",
    phoneWorkerText: "未刷新",
    localDnnText: "未刷新",
    llmText: "未刷新",
    cameraText: "未刷新",
    motorText: "未刷新",
    annotatedUrl: "",
    rawUrl: "",
    resultJsonUrl: "",
    defects: [],
    defectCount: 0,
    inferenceMs: "-",
    detectionSourceText: "-",
    reportMarkdown: "",
    reportStatus: "检测完成后可生成诊断报告",
    thinkingEnabled: wx.getStorageSync("aoiLlmThinkingEnabled") === true || wx.getStorageSync("aoiLlmThinkingEnabled") === "true",
    llmModels: LLM_MODELS,
    llmModelLabels: LLM_MODELS.map((item) => item.label),
    llmModel: storedLlmModel(),
    llmModelIndex: Math.max(0, LLM_MODELS.findIndex((item) => item.id === storedLlmModel())),
    reportDownloadReady: false,
    reportUploadId: "",
    logs: []
  },

  onLoad() {
    this.refreshSystemStatus();
    this.refreshUploads(true);
    this.refreshCurrentBatch(true);
  },

  onUnload() {
    clearTimeout(this._batchTimer);
  },

  apiBase() {
    return String(this.data.consoleBase || baseUrlFromApp()).replace(/\/$/, "");
  },

  setBase(base) {
    const clean = String(base || "").trim().replace(/\/$/, "");
    if (!clean) return;
    app.globalData.consoleApiBase = clean;
    this.setData({
      consoleBase: clean,
      consoleBaseInput: clean,
      fullWebUrl: `${clean}/`,
      phoneWebUrl: `${clean}/phone`
    });
  },

  onBaseInput(event) {
    this.setData({ consoleBaseInput: event.detail.value });
  },

  applyBaseInput() {
    this.setBase(this.data.consoleBaseInput);
    this.log(`后端地址已切换：${this.data.consoleBaseInput}`);
    this.refreshSystemStatus();
    this.refreshUploads(true);
  },

  log(message) {
    const time = new Date().toLocaleTimeString();
    this.setData({ logs: [`${time} ${message}`].concat(this.data.logs).slice(0, 60) });
  },

  setStatus(statusText, error = "") {
    this.setData({ statusText, error });
    this.log(statusText);
  },

  copyFullWebUrl() {
    this.copyText(this.data.fullWebUrl, "已复制完整网站地址");
  },

  copyPhoneWebUrl() {
    this.copyText(this.data.phoneWebUrl, "已复制 /phone 页面地址");
  },

  copyText(text, message) {
    wx.setClipboardData({
      data: text,
      success: () => this.log(message)
    });
  },

  chooseMode(event) {
    const key = event.currentTarget.dataset.mode || "phone";
    const meta = modeByKey(key);
    this.setData({ activeMode: key, activeModeMeta: meta, error: "" });
    this.log(`切换模式：${meta.name}`);
  },

  choosePlatform(event) {
    const key = event.currentTarget.dataset.platform || "huawei_cloud";
    this.setData({ detectionPlatform: key, detectionPlatformMeta: platformByKey(key) });
    this.log(`检测平台：${platformByKey(key).name}`);
  },

  onProfileChange(event) {
    const profile = event.detail.value || "precision";
    this.setData({
      profile,
      profileText: profile === "precision" ? "高精模式" : "快速模式"
    });
  },

  async refreshSystemStatus() {
    try {
      const status = await requestJson(`${this.apiBase()}/api/system/status`);
      const platforms = status.detection_platforms || {};
      const cloud = platforms.huawei_cloud || status.cloud_detection || {};
      const phone = platforms.phone_browser || status.phone_inference || {};
      const local = platforms.local_dnn || {};
      const llm = status.llm_report || {};
      const motor = status.motor || {};
      const camera = status.camera || {};
      this.setData({
        consoleStatusText: status.scan && status.scan.running ? "有任务运行" : "在线",
        cloudStatusText: cloud.configured ? `可用 ${cloud.api_base || ""}` : "未配置",
        phoneWorkerText: phone.ready ? `已连接 ${phone.ready_worker_count || 1}` : "未连接",
        localDnnText: local.ready || local.configured ? `可用 ${local.backend || "OpenCV DNN"}` : (local.message || "未就绪"),
        llmText: llm.configured ? `已配置 ${llm.model || ""}` : "未配置",
        cameraText: camera.opened ? `在线 ${camera.source || ""}` : (camera.last_error || "未打开"),
        motorText: motor.connected ? "已连接" : (motor.driver_mode || "dry_run")
      });
      this.log("系统状态已刷新");
    } catch (err) {
      this.setData({ consoleStatusText: `不可用：${err.message || err}` });
      this.log(`系统状态刷新失败：${err.message || err}`);
    }
  },

  async chooseFromCamera() {
    try {
      const files = await chooseImages("camera", 1);
      this.setSelectedFiles(files);
    } catch (err) {
      this.setStatus(err.message || "拍照失败", "1");
    }
  },

  async chooseFromAlbum() {
    try {
      const files = await chooseImages("album", 9);
      this.setSelectedFiles(files);
    } catch (err) {
      this.setStatus(err.message || "选择图库失败", "1");
    }
  },

  setSelectedFile(filePath) {
    const filename = basename(filePath);
    this.setData({
      imagePath: filePath,
      filename,
      statusText: "图片已选择，可上传到网站队列",
      error: "",
      progress: 0,
      annotatedUrl: "",
      defects: [],
      defectCount: 0,
      reportMarkdown: "",
      reportStatus: "检测完成后可生成诊断报告"
    });
    this.log(`已选择 ${filename}`);
  },

  setSelectedFiles(files) {
    const selectedFiles = Array.from(files || []).slice(0, 9).map((file, index) => ({
      tempFilePath: file.tempFilePath,
      filename: basename(file.tempFilePath) || `pcb_${index + 1}.jpg`,
      size: Number(file.size || 0)
    }));
    const first = selectedFiles[0] || null;
    this.setData({
      selectedFiles,
      imagePath: first ? first.tempFilePath : "",
      filename: first ? first.filename : "",
      statusText: selectedFiles.length ? `已选择 ${selectedFiles.length} 张照片，可创建新批次` : "请选择 PCB 图片",
      error: "",
      progress: 0,
      annotatedUrl: "",
      defects: [],
      defectCount: 0,
      reportMarkdown: "",
      reportStatus: "检测完成后可生成诊断报告"
    });
    this.log(`本次已选择 ${selectedFiles.length} 张照片`);
  },

  async uploadSelectedToPhoneQueue() {
    const selectedFiles = Array.isArray(this.data.selectedFiles) ? this.data.selectedFiles : [];
    if (!selectedFiles.length || this.data.busy) return;
    this.setData({ busy: true, progress: 15, error: "" });
    try {
      const batch = await requestJson(`${this.apiBase()}/api/phone/batches`, {
        method: "POST",
        data: { source: "wechat_miniprogram" }
      });
      const batchId = batch.batch_id;
      wx.setStorageSync("aoiCurrentPhoneBatchId", batchId);
      this.setData({ currentBatchId: batchId, batchStatus: "uploading", batchSummary: "正在上传" });
      let uploaded = 0;
      let failed = 0;
      for (let index = 0; index < selectedFiles.length; index += 1) {
        const file = selectedFiles[index];
        try {
          this.setStatus(`正在上传 ${index + 1}/${selectedFiles.length}：${file.filename}`);
          const data = await readFile(file.tempFilePath);
          const query = [
            `filename=${encodeURIComponent(file.filename || `phone-${index + 1}.jpg`)}`,
            `batch_id=${encodeURIComponent(batchId)}`,
            `batch_index=${index}`
          ].join("&");
          await requestBytes(`${this.apiBase()}/api/phone/upload?${query}`, data, "application/octet-stream");
          uploaded += 1;
        } catch (err) {
          failed += 1;
          await requestJson(`${this.apiBase()}/api/phone/batches/${encodeURIComponent(batchId)}/upload-error`, {
            method: "POST",
            data: {
              batch_index: index,
              filename: file.filename,
              error: err.message || String(err)
            }
          }).catch(() => null);
        }
        this.setData({ progress: Math.round(((index + 1) / selectedFiles.length) * 70) });
      }
      this.setData({ selectedFiles: [], imagePath: "", filename: "" });
      await this.refreshCurrentBatch(true);
      await this.refreshUploads(true);
      this.setStatus(failed ? `上传完成：成功 ${uploaded} 张，失败 ${failed} 张` : `本次 ${uploaded} 张上传完成`, failed ? "1" : "");
      this.log(`批次上传完成：${batchId}`);
    } catch (err) {
      this.setStatus(err.message || "上传失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  },

  async uploadSimulationImage() {
    if (!this.data.imagePath || this.data.busy) {
      this.setStatus("请先选择一张模拟图片", "1");
      return;
    }
    this.setData({ busy: true, progress: 20, error: "" });
    try {
      const data = await readFile(this.data.imagePath);
      const result = await requestBytes(
        `${this.apiBase()}/api/simulation/upload?filename=${encodeURIComponent(this.data.filename || "simulation-pcb.jpg")}`,
        data,
        "application/octet-stream"
      );
      this.setData({
        rawUrl: absoluteUrl(this.apiBase(), result.path || result.raw_image || ""),
        progress: 100
      });
      this.setStatus("模拟图片已导入网站，完整扫描请打开完整网站继续");
    } catch (err) {
      this.setStatus(err.message || "模拟图片导入失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  },

  batchViewItems(batch) {
    return (batch.items || []).map((item) => ({
      ...item,
      displayIndex: Number(item.batch_index || 0) + 1,
      imageUrl: absoluteUrl(this.apiBase(), item.annotated_image || item.raw_image || ""),
      rawImageUrl: absoluteUrl(this.apiBase(), item.raw_image || ""),
      annotatedImageUrl: absoluteUrl(this.apiBase(), item.annotated_image || ""),
      statusText: this.phoneBatchStatusLabel(item.status),
      defectCount: (item.defects || []).length,
      defectSummary: (item.defects || []).slice(0, 4).map((defect) => `${defect.label || defect.type} ${Math.round(Number(defect.confidence || 0) * 100)}%`).join("；") || "暂无缺陷结果"
    }));
  },

  phoneBatchStatusLabel(status) {
    const labels = {
      uploading: "上传中", ready: "待检测", queued: "排队中", detecting: "检测中",
      done: "已完成", partial_failed: "部分失败", interrupted: "已中断",
      uploaded: "待检测", detected: "已完成", detection_error: "检测失败", upload_error: "上传失败"
    };
    return labels[String(status || "")] || String(status || "待上传");
  },

  async refreshCurrentBatch(quiet = false) {
    const batchId = this.data.currentBatchId || wx.getStorageSync("aoiCurrentPhoneBatchId") || "";
    if (!batchId) {
      this.setData({ batchResults: [], batchStatus: "", batchSummary: "尚未创建批次", batchRunning: false });
      return;
    }
    try {
      const batch = await requestJson(`${this.apiBase()}/api/phone/batches/${encodeURIComponent(batchId)}`);
      const running = ["queued", "detecting"].includes(batch.status);
      const batchResults = this.batchViewItems(batch);
      this.setData({
        currentBatchId: batchId,
        batchResults,
        batchStatus: batch.status,
        batchSummary: `${batchResults.length}/9 张 · ${this.phoneBatchStatusLabel(batch.status)} · 已完成 ${batch.completed_count || 0}`,
        batchRunning: running
      });
      if (running) {
        clearTimeout(this._batchTimer);
        this._batchTimer = setTimeout(() => this.refreshCurrentBatch(true), 1000);
      }
    } catch (err) {
      if (!quiet) this.setStatus(`批次读取失败：${err.message || err}`, "1");
    }
  },

  async detectCurrentBatch() {
    const batchId = this.data.currentBatchId;
    if (!batchId || this.data.batchRunning) return;
    this.setData({ batchRunning: true });
    try {
      await requestJson(`${this.apiBase()}/api/phone/batches/${encodeURIComponent(batchId)}/detect`, {
        method: "POST",
        data: {
          detection_profile: this.data.profile,
          detection_platform: this.data.detectionPlatform
        }
      });
      this.setStatus("本次照片已进入顺序检测");
      await this.refreshCurrentBatch(true);
    } catch (err) {
      this.setData({ batchRunning: false });
      this.setStatus(`批量检测启动失败：${err.message || err}`, "1");
    }
  },

  deleteCurrentBatch() {
    const batchId = this.data.currentBatchId;
    if (!batchId || this.data.batchRunning) return;
    wx.showModal({
      title: "删除本次批次",
      content: "将删除本次上传的全部照片和检测结果。",
      success: async (result) => {
        if (!result.confirm) return;
        try {
          await requestJson(`${this.apiBase()}/api/phone/batches/${encodeURIComponent(batchId)}`, { method: "DELETE" });
          wx.removeStorageSync("aoiCurrentPhoneBatchId");
          this.setData({ currentBatchId: "", batchResults: [], batchStatus: "", batchSummary: "尚未创建批次" });
          await this.refreshUploads(true);
          this.setStatus("本次批次已删除");
        } catch (err) {
          this.setStatus(`批次删除失败：${err.message || err}`, "1");
        }
      }
    });
  },

  selectBatchItem(event) {
    const uploadId = event.currentTarget.dataset.upload;
    const item = this.data.batchResults.find((entry) => entry.upload_id === uploadId);
    if (!item) return;
    this.setData({ selectedUploadId: uploadId });
    this.applyUploadResult(item, true);
  },

  async detectBatchItem(event) {
    const uploadId = event.currentTarget.dataset.upload;
    if (!uploadId || this.data.batchRunning) return;
    try {
      const result = await requestJson(`${this.apiBase()}/api/phone/detect`, {
        method: "POST",
        data: {
          upload_id: uploadId,
          detection_profile: this.data.profile,
          detection_platform: this.data.detectionPlatform
        },
        timeout: 120000
      });
      this.applyUploadResult(result, false);
      await this.refreshCurrentBatch(true);
    } catch (err) {
      this.setStatus(`单张检测失败：${err.message || err}`, "1");
    }
  },

  async deleteBatchItem(event) {
    const uploadId = event.currentTarget.dataset.upload;
    if (!uploadId || this.data.batchRunning) return;
    try {
      await requestJson(`${this.apiBase()}/api/phone/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
      await this.refreshCurrentBatch(true);
      await this.refreshUploads(true);
      this.setStatus("照片已删除");
    } catch (err) {
      this.setStatus(`删除失败：${err.message || err}`, "1");
    }
  },

  exportBatchAnnotated(event) {
    const uploadId = event.currentTarget.dataset.upload;
    if (!uploadId) return;
    wx.downloadFile({
      url: `${this.apiBase()}/api/phone/uploads/${encodeURIComponent(uploadId)}/annotated`,
      success: (download) => {
        if (download.statusCode !== 200) {
          this.setStatus("标记图下载失败", "1");
          return;
        }
        wx.saveImageToPhotosAlbum({
          filePath: download.tempFilePath,
          success: () => this.setStatus("标记图已保存到相册"),
          fail: (err) => this.setStatus(`保存失败：${err.errMsg || err}`, "1")
        });
      },
      fail: (err) => this.setStatus(`下载失败：${err.errMsg || err}`, "1")
    });
  },

  async refreshUploads(quiet = false, selectUploadId = "") {
    try {
      const payload = await requestJson(`${this.apiBase()}/api/phone/uploads`);
      const allUploads = Array.isArray(payload.uploads) ? payload.uploads : [];
      const uploads = allUploads.filter((item) => String(item.batch_id || "") !== String(this.data.currentBatchId || ""));
      const selectedUploadId = selectUploadId || this.data.selectedUploadId || (uploads[0] && uploads[0].upload_id) || "";
      const uploadLabels = uploads.length
        ? uploads.map((item) => `${item.name || item.upload_id}${item.defects ? ` 缺陷 ${item.defects.length}` : ""}`)
        : ["暂无上传图片"];
      const selected = uploads.find((item) => item.upload_id === selectedUploadId) || uploads[0] || null;
      this.setData({
        uploads,
        uploadLabels,
        selectedUploadId: selected ? selected.upload_id : ""
      });
      if (selected) this.applyUploadResult(selected, true);
      if (!quiet) this.log(`上传队列已刷新：${uploads.length} 张`);
    } catch (err) {
      if (!quiet) this.setStatus(`上传队列读取失败：${err.message || err}`, "1");
    }
  },

  onUploadPickerChange(event) {
    const index = Number(event.detail.value || 0);
    const item = this.data.uploads[index] || null;
    if (!item) return;
    this.setData({ selectedUploadId: item.upload_id });
    this.applyUploadResult(item, true);
    this.log(`选择上传图：${item.name || item.upload_id}`);
  },

  async deleteSelectedUpload() {
    const uploadId = this.data.selectedUploadId;
    if (!uploadId) return;
    try {
      await requestJson(`${this.apiBase()}/api/phone/uploads/${encodeURIComponent(uploadId)}`, { method: "DELETE" });
      this.setData({
        selectedUploadId: "",
        annotatedUrl: "",
        rawUrl: "",
        resultJsonUrl: "",
        defects: [],
        defectCount: 0,
        reportMarkdown: "",
        progress: 0
      });
      await this.refreshUploads(true);
      this.log("已删除选中图片");
    } catch (err) {
      this.setStatus(`删除失败：${err.message || err}`, "1");
    }
  },

  async startDetection() {
    if (!this.data.selectedUploadId || this.data.busy) {
      this.setStatus("请先上传或选择一张 PCB 图片", "1");
      return;
    }
    this.setData({
      busy: true,
      progress: 55,
      error: "",
      annotatedUrl: "",
      defects: [],
      defectCount: 0,
      reportMarkdown: "",
      detectionSourceText: platformByKey(this.data.detectionPlatform).name
    });
    try {
      this.setStatus(`正在调用${platformByKey(this.data.detectionPlatform).name}检测`);
      const result = await requestJson(`${this.apiBase()}/api/phone/detect`, {
        method: "POST",
        data: {
          upload_id: this.data.selectedUploadId,
          detection_profile: this.data.profile,
          detection_platform: this.data.detectionPlatform
        },
        timeout: 120000
      });
      if (!result.ok) throw new Error(result.message || result.error || "检测失败");
      this.applyUploadResult(result, false);
      await this.refreshUploads(true, result.upload_id || this.data.selectedUploadId);
      this.setStatus(`检测完成：${(result.defects || []).length} 个缺陷`);
      this.setData({ progress: 100 });
    } catch (err) {
      this.setStatus(err.message || "检测失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  },

  applyUploadResult(payload, keepStatus = false) {
    const defects = (payload.defects || []).map((item) => ({
      type: item.type,
      label: item.label || item.type,
      bbox: item.bbox || [],
      confidence: item.confidence,
      confidenceText: formatConfidence(item.confidence)
    }));
    const source = payload.detection_platform || payload.inference_location || this.data.detectionPlatform;
    this.setData({
      rawUrl: absoluteUrl(this.apiBase(), payload.raw_image || payload.latest_image || payload.image_path || ""),
      annotatedUrl: absoluteUrl(this.apiBase(), payload.annotated_image || ""),
      resultJsonUrl: absoluteUrl(this.apiBase(), payload.result_json || ""),
      defects,
      defectCount: defects.length,
      inferenceMs: payload.inference_ms || "-",
      detectionSourceText: platformByKey(source).name || source,
      statusText: keepStatus ? this.data.statusText : `检测完成：${defects.length} 个缺陷`
    });
  },

  async generateReport() {
    const uploadId = this.data.selectedUploadId;
    if (!uploadId) {
      this.setData({ reportStatus: "请先选择已检测图片" });
      return;
    }
    this.setData({ reportStatus: "正在生成 LLM 诊断报告..." });
    try {
      const report = await requestJson(`${this.apiBase()}/api/llm/report`, {
        method: "POST",
        data: {
          target_type: "phone",
          upload_id: uploadId,
          model: this.data.llmModel,
          enable_thinking: Boolean(this.data.thinkingEnabled)
        },
        timeout: 120000
      });
      this.setData({
        reportMarkdown: report.report_markdown || "",
        reportStatus: `报告已生成 ${report.model || ""}${report.executed_model && report.executed_model !== report.model ? ` → ${report.executed_model}` : ""} · ${report.thinking_enabled ? "深度思考" : "快速响应"} · ${Math.round(Number(report.elapsed_ms || 0))}ms`,
        reportDownloadReady: true,
        reportUploadId: uploadId
      });
      this.log("LLM 诊断报告已生成");
    } catch (err) {
      this.setData({ reportStatus: `报告生成失败：${err.message || err}` });
    }
  },

  onThinkingModeChange(event) {
    const enabled = Boolean(event.detail.value);
    wx.setStorageSync("aoiLlmThinkingEnabled", enabled);
    this.setData({
      thinkingEnabled: enabled,
      reportStatus: enabled ? "深度思考已开启" : "深度思考已关闭，优先生成速度"
    });
  },

  onLlmModelChange(event) {
    const index = Math.max(0, Math.min(LLM_MODELS.length - 1, Number(event.detail.value || 0)));
    const model = LLM_MODELS[index];
    wx.setStorageSync("aoiLlmModel", model.id);
    this.setData({
      llmModel: model.id,
      llmModelIndex: index,
      reportStatus: `诊断模型已切换为 ${model.label}`
    });
  },

  exportDiagnosisReport() {
    const uploadId = this.data.selectedUploadId;
    if (!uploadId || !this.data.reportDownloadReady || this.data.reportUploadId !== uploadId) {
      this.setData({ reportStatus: "请先为当前图片生成诊断报告" });
      return;
    }
    const query = `target_type=phone&upload_id=${encodeURIComponent(uploadId)}`;
    const url = `${this.apiBase()}/api/llm/report/download?${query}`;
    wx.downloadFile({
      url,
      timeout: 60000,
      success: (download) => {
        if (download.statusCode !== 200) {
          this.setData({ reportStatus: `报告下载失败：HTTP ${download.statusCode}` });
          return;
        }
        wx.showActionSheet({
          itemList: ["保存报告", "转发报告"],
          success: (choice) => {
            if (choice.tapIndex === 1 && typeof wx.shareFileMessage === "function") {
              wx.shareFileMessage({
                filePath: download.tempFilePath,
                fileName: `phone_${uploadId}_diagnosis_report.md`,
                fail: (err) => this.setData({ reportStatus: `转发失败：${err.errMsg || err}` })
              });
              return;
            }
            wx.saveFile({
              tempFilePath: download.tempFilePath,
              success: (saved) => {
                this.setData({ reportStatus: `报告已保存：${saved.savedFilePath}` });
                this.log("LLM 诊断报告已保存");
              },
              fail: (err) => this.setData({ reportStatus: `保存失败：${err.errMsg || err}` })
            });
          }
        });
      },
      fail: (err) => this.setData({ reportStatus: `报告下载失败：${err.errMsg || err}` })
    });
  }
});
