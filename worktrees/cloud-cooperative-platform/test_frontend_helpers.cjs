const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const helperPath = path.join(__dirname, "static", "app_helpers.js");
delete globalThis.AoiUiHelpers;
vm.runInThisContext(fs.readFileSync(helperPath, "utf8"), { filename: helperPath });
const helpers = globalThis.AoiUiHelpers;

assert.equal(
  helpers.preferredPhoneUploadUrl([
    "http://116.205.117.33:8020/phone",
    "http://192.168.0.124:8020/phone",
  ]),
  "http://116.205.117.33:8020/phone",
);
assert.equal(helpers.preferredPhoneUploadUrl([]), "");
assert.equal(helpers.preferredPhoneUploadUrl(null), "");

assert.deepEqual(
  helpers.resolveDefectSourceCell({
    cell: "2:3",
    annotated_image: "scan/r002_c003/annotated.jpg",
    raw_image: "scan/r002_c003/raw.jpg",
  }),
  {
    row: 2,
    col: 3,
    annotated_image: "scan/r002_c003/annotated.jpg",
    raw_image: "scan/r002_c003/raw.jpg",
  },
);

assert.deepEqual(
  helpers.resolveDefectSourceCell({
    cell: "mosaic",
    source_candidates: [
      { confidence: 0.61, source_cell: { row: 0, col: 1, annotated_image: "low.jpg" } },
      { confidence: 0.88, source_cell: { row: 1, col: 2, annotated_image: "best.jpg" } },
    ],
    support_cells: [{ row: 3, col: 3, annotated_image: "support.jpg" }],
  }),
  { row: 1, col: 2, annotated_image: "best.jpg", raw_image: "" },
);

assert.deepEqual(
  helpers.resolveDefectSourceCell({
    cell: "mosaic",
    support_cells: [{ row: 3, col: 0, raw_image: "support-raw.jpg" }],
  }),
  { row: 3, col: 0, annotated_image: "", raw_image: "support-raw.jpg" },
);

assert.equal(helpers.resolveDefectSourceCell({ cell: "mosaic", bbox: [1, 2, 3, 4] }), null);

assert.equal(helpers.phoneExperimentProfileFromPlatform("huawei_cloud"), "cloud_baseline");
assert.equal(helpers.phoneExperimentProfileFromPlatform("phone_browser"), "phone_link_sim");
assert.equal(helpers.phoneExperimentProfileFromPlatform("local_dnn"), "local_link_sim");

assert.deepEqual(
  helpers.phoneViewportRect(
    { x: 100, y: 50, w: 400, h: 200 },
    { scale: 2, offsetX: 0, offsetY: 0 },
  ),
  { x: -100, y: -50, w: 800, h: 400 },
);

const zoomed = helpers.zoomPhoneViewport(
  { scale: 1, offsetX: 0, offsetY: 0 },
  { x: 300, y: 150 },
  2,
  { x: 100, y: 50, w: 400, h: 200 },
  { width: 600, height: 300 },
);
assert.deepEqual(zoomed, { scale: 2, offsetX: 0, offsetY: 0 });

assert.deepEqual(
  helpers.panPhoneViewport(
    { scale: 2, offsetX: 0, offsetY: 0 },
    10000,
    10000,
    { x: 100, y: 50, w: 400, h: 200 },
    { width: 600, height: 300 },
  ),
  { scale: 2, offsetX: 620, offsetY: 270 },
);

assert.deepEqual(
  helpers.partitionPhoneUploads(
    [
      { upload_id: "current-2", batch_id: "batch-current", batch_index: 1 },
      { upload_id: "legacy", batch_id: null },
      { upload_id: "current-1", batch_id: "batch-current", batch_index: 0 },
      { upload_id: "old", batch_id: "batch-old", batch_index: 0 },
    ],
    "batch-current",
  ),
  {
    current: [
      { upload_id: "current-1", batch_id: "batch-current", batch_index: 0 },
      { upload_id: "current-2", batch_id: "batch-current", batch_index: 1 },
    ],
    history: [
      { upload_id: "legacy", batch_id: null },
      { upload_id: "old", batch_id: "batch-old", batch_index: 0 },
    ],
  },
);

assert.equal(helpers.phoneBatchSelectionError(1), "");
assert.equal(helpers.phoneBatchSelectionError(9), "");
assert.match(helpers.phoneBatchSelectionError(10), /9/);

assert.equal(
  helpers.latestPhoneUploadId([
    { upload_id: "middle", uploaded_at: "2026-07-21T12:00:00" },
    { upload_id: "latest", uploaded_at: "2026-07-21T12:01:00" },
    { upload_id: "oldest", uploaded_at: "2026-07-21T11:59:00" },
  ]),
  "latest",
);
assert.equal(helpers.latestPhoneUploadId([]), "");

assert.deepEqual(
  helpers.normalizePhoneUploadSelection(["a", "b", "a", "", null]),
  ["a", "b"],
);

assert.deepEqual(
  helpers.clampWorkspaceGeometry(
    { left: -80, top: -20, width: 1800, height: 1200 },
    { width: 1366, height: 768 },
  ),
  { left: 0, top: 0, width: 1366, height: 768 },
);

assert.deepEqual(
  helpers.resizeWorkspaceGeometry(
    { left: 100, top: 80, width: 1000, height: 650 },
    "nw",
    80,
    40,
    { width: 1600, height: 900 },
  ),
  { left: 180, top: 120, width: 920, height: 610 },
);

assert.deepEqual(
  helpers.normalizeWorkspaceColumnRatios([0.3, 0.4, 0.3]),
  [0.3, 0.4, 0.3],
);
assert.deepEqual(
  helpers.normalizeWorkspaceColumnRatios([0, Number.NaN, -1]),
  [0.3, 0.4, 0.3],
);

assert.deepEqual(
  helpers.resizeWorkspaceColumnRatios(
    [0.3, 0.4, 0.3],
    0,
    120,
    1200,
    [260, 320, 300],
  ),
  [0.4, 0.3, 0.3],
);
assert.deepEqual(
  helpers.resizeWorkspaceColumnRatios(
    [0.3, 0.4, 0.3],
    1,
    -120,
    1200,
    [260, 320, 300],
  ),
  [0.3, 0.3, 0.4],
);

const clampedColumns = helpers.resizeWorkspaceColumnRatios(
  [0.3, 0.4, 0.3],
  0,
  -1000,
  1200,
  [260, 320, 300],
);
assert.equal(Math.round(clampedColumns[0] * 1200), 260);
assert.equal(Math.round(clampedColumns.reduce((sum, value) => sum + value, 0) * 1000), 1000);

assert.deepEqual(
  helpers.defectChatReference({
    cell: "2:3",
    type: "short_circuit",
    bbox: [10, 20, 30, 40],
  }),
  { row: 2, col: 3, type: "short_circuit", bbox: [10, 20, 30, 40] },
);

assert.deepEqual(
  helpers.parseSseFrames(
    'event: phase\ndata: {"phase":"thinking"}\n\nevent: delta\ndata: {"content":"hello"}\n\npartial',
  ),
  {
    events: [
      { event: "phase", data: { phase: "thinking" } },
      { event: "delta", data: { content: "hello" } },
    ],
    rest: "partial",
  },
);

assert.equal(helpers.normalizeWorkspaceView("control"), "control");
assert.equal(helpers.normalizeWorkspaceView("unknown"), "balanced");
assert.equal(helpers.nextWorkspaceView("balanced", "scan"), "scan");
assert.equal(helpers.nextWorkspaceView("scan", "scan"), "balanced");
assert.equal(helpers.nextWorkspaceView("review", "control"), "control");

assert.equal(helpers.normalizeProgressPercent(-12), 0);
assert.equal(helpers.normalizeProgressPercent(64.6), 65);
assert.equal(helpers.normalizeProgressPercent(140), 100);
assert.equal(helpers.normalizeProgressPercent(Number.NaN), 0);

assert.equal(
  helpers.workspaceStatusTone({ websocketOnline: false, statusAvailable: true, requirementsReady: true }),
  "bad",
);
assert.equal(
  helpers.workspaceStatusTone({ websocketOnline: true, statusRequestFailed: true, statusAvailable: true, requirementsReady: true }),
  "bad",
);
assert.equal(
  helpers.workspaceStatusTone({ websocketOnline: true, statusAvailable: false, requirementsReady: false }),
  "warn",
);
assert.equal(
  helpers.workspaceStatusTone({ websocketOnline: true, statusAvailable: true, requirementsReady: false }),
  "warn",
);
assert.equal(
  helpers.workspaceStatusTone({ websocketOnline: true, statusAvailable: true, requirementsReady: true }),
  "good",
);

assert.deepEqual(
  helpers.diagnosisArtifactControls({
    mode: "real",
    running: false,
    hasMosaic: true,
    hasAnnotatedFrames: true,
    annotatedFrameCount: 16,
  }),
  {
    showMosaicSelect: true,
    mosaicDisabled: false,
    exportDisabled: false,
    exportLabel: "导出16张标注图",
  },
);

assert.deepEqual(
  helpers.diagnosisArtifactControls({
    mode: "simulation",
    running: true,
    hasMosaic: false,
    hasAnnotatedFrames: false,
  }),
  {
    showMosaicSelect: true,
    mosaicDisabled: true,
    exportDisabled: true,
    exportLabel: "导出16张标注图",
  },
);

assert.deepEqual(
  helpers.diagnosisArtifactControls({
    mode: "phone",
    running: false,
    phoneBatchReady: true,
    hasPhoneAnnotated: true,
  }),
  {
    showMosaicSelect: false,
    mosaicDisabled: true,
    exportDisabled: false,
    exportLabel: "导出批次标记图",
  },
);

console.log("frontend helper tests passed");
