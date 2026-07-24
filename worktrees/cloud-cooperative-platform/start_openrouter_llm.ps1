param(
    [Parameter(Mandatory = $true)]
    [string]$ApiKey,

    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$env:LOONGSON_AOI_LLM_API_KEY = $ApiKey
$env:LOONGSON_AOI_LLM_BASE_URL = "https://openrouter.ai/api/v1"
$env:LOONGSON_AOI_LLM_MODEL = "qwen/qwen2.5-vl-32b-instruct:free"
$env:LOONGSON_AOI_LLM_APP_TITLE = "Loongson AOI PCB"
$env:LOONGSON_AOI_MOTOR_DRY_RUN = "1"

Write-Host "Starting Loongson AOI with OpenRouter LLM report support..."
Write-Host "LLM model: $env:LOONGSON_AOI_LLM_MODEL"
Write-Host "URL: http://$HostAddress`:$Port/"

python -m uvicorn main:app --host $HostAddress --port $Port
