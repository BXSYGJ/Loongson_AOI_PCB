param(
    [string]$ApiKey = "",
    [string]$ApiKeyCsv = "D:\AAAED\默认业务空间-apiKey-6015270.csv",
    [string]$WorkspaceId = "",
    [string]$Model = "qwen3.7-plus",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

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

$LocalConfig = Join-Path $PSScriptRoot "llm_config.local.ps1"
if (Test-Path -LiteralPath $LocalConfig) {
    . $LocalConfig
}

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    $ApiKey = Get-ApiKeyFromCsv -Path $ApiKeyCsv
}

if ([string]::IsNullOrWhiteSpace($WorkspaceId) -and (Test-Path -LiteralPath $ApiKeyCsv)) {
    $WorkspaceId = Get-WorkspaceIdFromCsv -Path $ApiKeyCsv
}

$env:LOONGSON_AOI_LLM_API_KEY = $ApiKey
$env:LOONGSON_AOI_LLM_MODEL = $Model
$env:LOONGSON_AOI_LLM_APP_TITLE = "Loongson AOI PCB"
$env:LOONGSON_AOI_MOTOR_DRY_RUN = "1"

$env:LOONGSON_AOI_LLM_BASE_URL = $BaseUrl

Write-Host "Starting Loongson AOI with Alibaba Bailian LLM report support..."
Write-Host "LLM base: $env:LOONGSON_AOI_LLM_BASE_URL"
Write-Host "LLM model: $env:LOONGSON_AOI_LLM_MODEL"
Write-Host "URL: http://$HostAddress`:$Port/"

python -m uvicorn main:app --host $HostAddress --port $Port
