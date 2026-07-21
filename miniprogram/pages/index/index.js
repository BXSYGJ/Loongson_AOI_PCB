const app = getApp();

const MODES = [
  {
    key: "simulation",
    name: "模拟模式",
    tag: "图片 + 拼接",
    desc: "导入已有 PCB 图片，按蛇形网格模拟滑台检测。",
    hint: "小程序可把图片导入网站队列；完整网格扫描、拼图和整板复核建议在手机浏览器打开完整网站操作。"
  },
  {
    key: "real",
    name: "真实模式",
    tag: "滑台 + 相机",
    desc: "龙芯负责滑台移动、OV9281 拍照和逐格保存。",
    hint: "真实模式需要在运行网站的龙芯端执行 soft home，并由后端控制滑台和相机。小程序用于查看状态和进入移动网页。"
  },
  {
    key: "phone",
    name: "手机拍照",
    tag: "拍照上传",
    desc: "拍照或图库选择 PCB，上传到网站并按所选平台检测。",
    hint: "与手机打开 /phone 一致：上传图片、选择检测平台、整图检测、查看标注图和诊断报告。"
  }
];

const PLATFORMS = [
  { key: "huawei_cloud", name: "华为云", desc: "ECS + OBS + OpenCV DNN" },
  { key: "phone_browser", name: "手机", desc: "需另有手机浏览器 worker 在线" },
  { key: "local_dnn", name: "本地电脑/龙芯", desc: "运行网站后端的设备本地检测" }
];

function apiBase() {
  return String(app.globalData.consoleApiBase || "http://172.25.208.1:8020").replace(/\/$/, "");
}

function absoluteUrl(value) {
  const base = apiBase();
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^https?:\/\//i.test(text)) return text;
  if (text.startsWith("/")) return `${base}${text}`;
  return `${base}/api/artifact?path=${encodeURIComponent(text)}`;
}

function requestJson(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      ...options,
      header: Object.assign({ "Content-Type": "application/json" }, options.header || {}),
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const data = res.data || {};
          reject(new Error(data.detail || data.message || `HTTP ${res.statusCode}`));
        }
      },
      fail(err) {
        reject(new Error(err.errMsg || "request failed"));
      }
    });
  });
}

function requestBytes(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      ...options,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data);
        } else {
          const data = res.data || {};
          reject(new Error(data.detail || data.message || `HTTP ${res.statusCode}`));
        }
      },
      fail(err) {
        reject(new Error(err.errMsg || "request failed"));
      }
    });
  });
}

function chooseMedia(sourceType) {
  return new Promise((resolve, reject) => {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: [sourceType],
      sizeType: ["compressed"],
      success(res) {
        const file = res.tempFiles && res.tempFiles[0];
        if (!file || !file.tempFilePath) {
          reject(new Error("未选择图片"));
          return;
        }
        resolve(file);
      },
      fail(err) {
        reject(new Error(err.errMsg || "chooseMedia failed"));
      }
    });
  });
}

function readFileArrayBuffer(filePath) {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      success(res) {
        resolve(res.data);
      },
      fail(err) {
        reject(new Error(err.errMsg || "readFile failed"));
      }
    });
  });
}

function basename(filePath) {
  const parts = String(filePath || "").split(/[\\/]/);
  return parts[parts.length - 1] || `pcb_${Date.now()}.jpg`;
}

function modeByKey(key) {
  return MODES.find((item) => item.key === key) || MODES[0];
}

function platformByKey(key) {
  return PLATFORMS.find((item) => item.key === key) || PLATFORMS[0];
}

function platformLabel(key) {
  return platformByKey(key).name;
}

function formatConfidence(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num.toFixed(3) : String(value || "-");
}

Page({
  data: {
    modes: MODES,
    platforms: PLATFORMS,
    activeMode: "phone",
    activeModeMeta: modeByKey("phone"),
    detectionPlatform: "huawei_cloud",
    detectionPlatformMeta: platformByKey("huawei_cloud"),
    profile: "fast",
    profileText: "快速模式",
    consoleBase: apiBase(),
    fullWebUrl: `${apiBase()}/`,
    phoneWebUrl: `${apiBase()}/phone`,
    imagePath: "",
    filename: "",
    selectedUploadId: "",
    uploadLabels: ["暂无上传图片"],
    uploads: [],
    statusText: "请选择 PCB 图片",
    consoleStatusText: "未刷新",
    cloudStatusText: "未刷新",
    phoneWorkerText: "未刷新",
    localDnnText: "未刷新",
    llmText: "未刷新",
    cameraText: "未刷新",
    motorText: "未刷新",
    error: "",
    busy: false,
    progress: 0,
    annotatedUrl: "",
    rawUrl: "",
    resultJsonUrl: "",
    defects: [],
    defectCount: 0,
    inferenceMs: "-",
    detectionSourceText: "-",
    reportMarkdown: "",
    reportStatus: "检测完成后可生成诊断报告",
    logs: []
  },

  onLoad() {
    this.refreshSystemStatus();
    this.refreshUploads({ quiet: true });
  },

  log(message) {
    const time = new Date().toLocaleTimeString();
    this.setData({ logs: [`${time} ${message}`].concat(this.data.logs).slice(0, 50) });
  },

  setStatus(statusText, error = "") {
    this.setData({ statusText, error });
    this.log(statusText);
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
    this.log(`检测平台：${platformLabel(key)}`);
  },

  onProfileChange(event) {
    const profile = event.detail.value || "fast";
    this.setData({ profile, profileText: profile === "precision" ? "高精模式" : "快速模式" });
  },

  copyFullWebUrl() {
    this.copyText(this.data.fullWebUrl, "已复制完整网站地址");
  },

  copyPhoneWebUrl() {
    this.copyText(this.data.phoneWebUrl, "已复制 /phone 上传页地址");
  },

  copyText(text, message) {
    wx.setClipboardData({
      data: text,
      success: () => this.log(message)
    });
  },

  async refreshSystemStatus() {
    try {
      const status = await requestJson({ url: `${apiBase()}/api/system/status`, method: "GET" });
      const platforms = status.detection_platforms || {};
      const cloud = platforms.huawei_cloud || status.cloud_detection || {};
      const phone = platforms.phone_browser || status.phone_inference || {};
      const local = platforms.local_dnn || {};
      const llm = status.llm_report || {};
      const motor = status.motor || {};
      const camera = status.camera || {};
      this.setData({
        consoleStatusText: status.scan && status.scan.running ? "有任务运行" : "在线",
        cloudStatusText: cloud.configured ? `可用 · ${cloud.api_base || "已配置"}` : "未配置",
        phoneWorkerText: phone.ready ? `已连接 ${phone.ready_worker_count || 1}` : "未连接",
        localDnnText: local.ready || local.configured ? `可用 · ${local.backend || "OpenCV DNN"}` : (local.message || "未就绪"),
        llmText: llm.configured ? `已配置 · ${llm.model || "模型"}` : "未配置",
        cameraText: camera.opened ? `在线 · ${camera.source || ""}` : (camera.last_error ? `不可用 · ${camera.last_error}` : "未打开"),
        motorText: motor.connected ? "已连接" : (motor.driver_mode || "dry_run"),
      });
      this.log("系统状态已刷新");
    } catch (err) {
      this.setData({ consoleStatusText: `不可用：${err.message || err}` });
      this.log(`系统状态刷新失败：${err.message || err}`);
    }
  },

  async chooseFromCamera() {
    try {
      const file = await chooseMedia("camera");
      this.setSelectedFile(file.tempFilePath);
    } catch (err) {
      this.setStatus(err.message || "拍照失败", "1");
    }
  },

  async chooseFromAlbum() {
    try {
      const file = await chooseMedia("album");
      this.setSelectedFile(file.tempFilePath);
    } catch (err) {
      this.setStatus(err.message || "选择图库失败", "1");
    }
  },

  setSelectedFile(filePath) {
    const filename = basename(filePath);
    this.setData({
      imagePath: filePath,
      filename,
      statusText: "图片已选择，可以上传到网站队列",
      error: "",
      progress: 0,
      reportMarkdown: "",
      reportStatus: "检测完成后可生成诊断报告"
    });
    this.log(`已选择 ${filename}`);
  },

  async uploadSelectedToPhoneQueue() {
    if (!this.data.imagePath || this.data.busy) return;
    this.setData({ busy: true, progress: 15, error: "" });
    try {
      this.setStatus("正在上传到网站手机队列");
      const data = await readFileArrayBuffer(this.data.imagePath);
      const payload = await requestBytes({
        url: `${apiBase()}/api/phone/upload?filename=${encodeURIComponent(this.data.filename || "phone-pcb.jpg")}`,
        method: "POST",
        data,
        header: { "Content-Type": "application/octet-stream" }
      });
      const upload = typeof payload === "string" ? JSON.parse(payload) : payload;
      if (!upload || !upload.upload_id) {
        throw new Error("上传响应缺少 upload_id");
      }
      this.setData({
        selectedUploadId: upload.upload_id,
        progress: 45,
        rawUrl: absoluteUrl(upload.raw_image || upload.image_path || ""),
        statusText: "已上传，可开始检测"
      });
      await this.refreshUploads({ selectUploadId: upload.upload_id, quiet: true });
      this.log(`上传完成：${upload.upload_id}`);
    } catch (err) {
      this.setStatus(err.message || "上传失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  },

  async refreshUploads(options = {}) {
    try {
      const payload = await requestJson({ url: `${apiBase()}/api/phone/uploads`, method: "GET" });
      const uploads = Array.isArray(payload.uploads) ? payload.uploads : [];
      const selectedUploadId = options.selectUploadId
        || this.data.selectedUploadId
        || (uploads[0] && uploads[0].upload_id)
        || "";
      const uploadLabels = uploads.length
        ? uploads.map((item) => `${item.name || item.upload_id}${item.defects ? ` · 缺陷 ${item.defects.length}` : ""}`)
        : ["暂无上传图片"];
      const selected = uploads.find((item) => item.upload_id === selectedUploadId) || uploads[0] || null;
      this.setData({
        uploads,
        uploadLabels,
        selectedUploadId: selected ? selected.upload_id : "",
      });
      if (selected) this.applyUploadResult(selected, { keepStatus: true });
      if (!options.quiet) this.log(`上传队列已刷新：${uploads.length} 张`);
    } catch (err) {
      if (!options.quiet) this.setStatus(`上传队列读取失败：${err.message || err}`, "1");
    }
  },

  onUploadPickerChange(event) {
    const index = Number(event.detail.value || 0);
    const item = this.data.uploads[index] || null;
    if (!item) return;
    this.setData({ selectedUploadId: item.upload_id });
    this.applyUploadResult(item, { keepStatus: true });
    this.log(`选择上传图：${item.name || item.upload_id}`);
  },

  async deleteSelectedUpload() {
    const uploadId = this.data.selectedUploadId;
    if (!uploadId) return;
    try {
      await requestJson({ url: `${apiBase()}/api/phone/uploads/${encodeURIComponent(uploadId)}`, method: "DELETE" });
      this.setData({
        selectedUploadId: "",
        annotatedUrl: "",
        rawUrl: "",
        resultJsonUrl: "",
        defects: [],
        defectCount: 0,
        reportMarkdown: "",
      });
      await this.refreshUploads({ quiet: true });
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
      detectionSourceText: platformLabel(this.data.detectionPlatform)
    });
    try {
      this.setStatus(`正在调用${platformLabel(this.data.detectionPlatform)}检测`);
      const result = await requestJson({
        url: `${apiBase()}/api/phone/detect`,
        method: "POST",
        data: {
          upload_id: this.data.selectedUploadId,
          detection_profile: this.data.profile,
          detection_platform: this.data.detectionPlatform
        }
      });
      if (!result.ok) {
        throw new Error(result.message || result.error || "检测失败");
      }
      this.applyUploadResult(result);
      await this.refreshUploads({ selectUploadId: result.upload_id || this.data.selectedUploadId, quiet: true });
      this.setStatus(`检测完成：${(result.defects || []).length} 个缺陷`);
      this.setData({ progress: 100 });
    } catch (err) {
      this.setStatus(err.message || "检测失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  },

  applyUploadResult(payload, options = {}) {
    const defects = (payload.defects || []).map((item) => ({
      ...item,
      confidenceText: formatConfidence(item.confidence)
    }));
    const hasResult = payload.annotated_image || payload.result_json || defects.length;
    this.setData({
      rawUrl: absoluteUrl(payload.raw_image || payload.latest_image || payload.image_path || ""),
      annotatedUrl: absoluteUrl(payload.annotated_image || ""),
      resultJsonUrl: absoluteUrl(payload.result_json || ""),
      defects,
      defectCount: defects.length,
      inferenceMs: payload.inference_ms || "-",
      detectionSourceText: platformLabel(payload.detection_platform || this.data.detectionPlatform),
      statusText: options.keepStatus
        ? this.data.statusText
        : hasResult ? `检测完成：${defects.length} 个缺陷` : this.data.statusText,
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
      const report = await requestJson({
        url: `${apiBase()}/api/llm/report`,
        method: "POST",
        data: { target_type: "phone", upload_id: uploadId }
      });
      this.setData({
        reportMarkdown: report.report_markdown || "",
        reportStatus: `报告已生成 · ${report.model || "LLM"}`
      });
      this.log("LLM 诊断报告已生成");
    } catch (err) {
      this.setData({ reportStatus: `报告生成失败：${err.message || err}` });
    }
  },

  async uploadSimulationImage() {
    if (!this.data.imagePath || this.data.busy) {
      this.setStatus("请先选择一张模拟图片", "1");
      return;
    }
    this.setData({ busy: true, progress: 20 });
    try {
      const data = await readFileArrayBuffer(this.data.imagePath);
      const payload = await requestBytes({
        url: `${apiBase()}/api/simulation/upload?filename=${encodeURIComponent(this.data.filename || "simulation-pcb.jpg")}`,
        method: "POST",
        data,
        header: { "Content-Type": "application/octet-stream" }
      });
      const result = typeof payload === "string" ? JSON.parse(payload) : payload;
      this.setData({
        rawUrl: absoluteUrl(result.path || result.raw_image || ""),
        progress: 100
      });
      this.setStatus("模拟图片已导入网站，完整扫描请打开完整网站继续");
    } catch (err) {
      this.setStatus(err.message || "模拟图片导入失败", "1");
    } finally {
      this.setData({ busy: false });
    }
  }
});
