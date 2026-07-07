$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$UiDir = Resolve-Path (Join-Path $ScriptDir "..\..")
$WorktreesDir = Split-Path -Parent $UiDir
$AlgorithmRoot = Join-Path $WorktreesDir "algorithm-ai"
$ConfigPath = Join-Path $AlgorithmRoot "algorithm\configs\pcb_defects.yaml"
$WeightsPath = Join-Path $AlgorithmRoot "runs\detect\pcb_defects\weights\best.onnx"
$DistDir = Join-Path $UiDir "dist"
$StageDir = Join-Path $DistDir "loongson_phone_bundle"
$ZipPath = Join-Path $DistDir "loongson_phone_bundle.zip"

if (!(Test-Path -LiteralPath $ConfigPath)) {
    throw "Missing config: $ConfigPath"
}
if (!(Test-Path -LiteralPath $WeightsPath)) {
    throw "Missing ONNX weights: $WeightsPath"
}

if (Test-Path -LiteralPath $StageDir) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

$BundleRoot = Join-Path $StageDir "Loongson_AOI_PCB"
$BundleUi = Join-Path $BundleRoot "worktrees\cloud-cooperative-platform"
$BundleAlgorithm = Join-Path $BundleRoot "worktrees\algorithm-ai"
New-Item -ItemType Directory -Force -Path $BundleUi | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BundleAlgorithm "algorithm\configs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $BundleAlgorithm "runs\detect\pcb_defects\weights") | Out-Null

$UiFiles = @(
    "main.py",
    "camera_trigger.py",
    "detection_router.py",
    "local_dnn_detector.py",
    "llm_reporter.py",
    "motor_controller.py",
    "motor_gpio_system_helper.py",
    "motor_test.py",
    "requirements.txt",
    "requirements-loongson.txt",
    "README.md",
    "LOONGSON_2K1000LA_PHONE_DEPLOY.md",
    "cloud_api_base.txt",
    "camera_source.txt",
    "start_lan_server.sh",
    "start_lan_server.ps1",
    "start_qwen_llm.ps1",
    "llm_config.local.ps1",
    "LICENSE"
)

foreach ($relative in $UiFiles) {
    $source = Join-Path $UiDir $relative
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $BundleUi $relative) -Force
    }
}

Copy-Item -LiteralPath (Join-Path $UiDir "static") -Destination (Join-Path $BundleUi "static") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $UiDir "deploy") -Destination (Join-Path $BundleUi "deploy") -Recurse -Force
Copy-Item -LiteralPath $ConfigPath -Destination (Join-Path $BundleAlgorithm "algorithm\configs\pcb_defects.yaml") -Force
Copy-Item -LiteralPath $WeightsPath -Destination (Join-Path $BundleAlgorithm "runs\detect\pcb_defects\weights\best.onnx") -Force

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $baseLength = $StageDir.TrimEnd("\", "/").Length + 1
    Get-ChildItem -LiteralPath $BundleRoot -Recurse -File | ForEach-Object {
        $entryName = $_.FullName.Substring($baseLength).Replace("\", "/")
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $_.FullName, $entryName) | Out-Null
    }
}
finally {
    $zip.Dispose()
}

$zip = Get-Item -LiteralPath $ZipPath
[PSCustomObject]@{
    ZipPath = $zip.FullName
    SizeMB = [Math]::Round($zip.Length / 1MB, 2)
    BoardUnzipTarget = "~/Documents"
    BoardUiDir = "~/Documents/Loongson_AOI_PCB/worktrees/cloud-cooperative-platform"
}
