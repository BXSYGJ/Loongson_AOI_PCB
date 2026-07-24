param(
    [string]$ApiKey = "",
    [string]$ApiKeyCsv = "",
    [string]$WorkspaceId = "",
    [string]$Model = "qwen3.7-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Get-ApiKeyFromCsv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "API Key CSV not found: $Path"
    }
    $row = Import-Csv -LiteralPath $Path | Select-Object -First 1
    if (-not $row) {
        throw "API Key CSV is empty: $Path"
    }
    foreach ($property in $row.PSObject.Properties) {
        $value = [string]$property.Value
        if ($value.StartsWith("sk-")) {
            return $value
        }
    }
    throw "Could not find an sk-* API key in CSV: $Path"
}

function Get-WorkspaceIdFromCsv {
    param([string]$Path)
    $row = Import-Csv -LiteralPath $Path | Select-Object -First 1
    foreach ($property in $row.PSObject.Properties) {
        if ($property.Name -match "^\d{4,}$") {
            return $property.Name
        }
    }
    return ""
}

try {
    $localConfig = [System.IO.Path]::Combine($PSScriptRoot, "llm_config.local.ps1")
    if (Test-Path -LiteralPath $localConfig) {
        . $localConfig
    }

    if ([string]::IsNullOrWhiteSpace($ApiKey) -and -not [string]::IsNullOrWhiteSpace($ApiKeyCsv)) {
        $ApiKey = Get-ApiKeyFromCsv -Path $ApiKeyCsv
    }
    if ([string]::IsNullOrWhiteSpace($ApiKey)) {
        throw "未配置 LLM API Key。请使用 llm_config.local.ps1、环境变量或 -ApiKey。"
    }
    if ([string]::IsNullOrWhiteSpace($WorkspaceId) -and -not [string]::IsNullOrWhiteSpace($ApiKeyCsv) -and (Test-Path -LiteralPath $ApiKeyCsv)) {
        $WorkspaceId = Get-WorkspaceIdFromCsv -Path $ApiKeyCsv
    }

    $env:LOONGSON_AOI_LLM_API_KEY = $ApiKey
    $env:LOONGSON_AOI_LLM_MODEL = $Model
    $env:LOONGSON_AOI_LLM_APP_TITLE = "Loongson AOI PCB"
    $env:LOONGSON_AOI_MOTOR_DRY_RUN = "1"
    $env:LOONGSON_AOI_LLM_BASE_URL = $BaseUrl

    Write-Output "启动 Loongson AOI v1.0.0 LLM 网站：http://$HostAddress`:$Port/"
    Write-Output "LLM 模型：$env:LOONGSON_AOI_LLM_MODEL"
    & python -m uvicorn main:app --host $HostAddress --port $Port
    if ($LASTEXITCODE -ne 0) {
        throw "Uvicorn 异常退出，代码 $LASTEXITCODE。"
    }
}
catch {
    Write-Error "LLM 网站启动失败：$($_.Exception.Message)"
    exit 1
}
