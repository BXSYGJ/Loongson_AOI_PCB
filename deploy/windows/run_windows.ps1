[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()]
    [string]$HostAddress = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8020,
    [switch]$SkipPrivateConfig
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Import-EnvironmentFile {
    param([Parameter(Mandatory)][string]$Path)

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith('#')) {
            continue
        }
        if ($line.StartsWith('export ')) {
            $line = $line.Substring(7)
        }
        $separator = $line.IndexOf('=')
        if ($separator -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            throw "配置包含非法环境变量名：$name"
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..', '..'))
    $webDirectory = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'cloud-cooperative-platform')
    $venvPython = [System.IO.Path]::Combine($projectRoot, '.venv', 'Scripts', 'python.exe')
    $privateEnvironment = [System.IO.Path]::Combine($projectRoot, 'config', 'private', 'cloud-web.env')
    $modelPath = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai', 'runs', 'detect', 'pcb_defects', 'weights', 'best.onnx')
    $algorithmRoot = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai')

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw '未找到 .venv，请先运行 deploy/windows/install_windows.ps1。'
    }
    if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
        throw "未找到 ONNX 模型：$modelPath"
    }
    if (-not $SkipPrivateConfig -and (Test-Path -LiteralPath $privateEnvironment -PathType Leaf)) {
        Import-EnvironmentFile -Path $privateEnvironment
    }

    $env:LOONGSON_AOI_ALGORITHM_ROOT = $algorithmRoot
    $env:LOONGSON_AOI_WEIGHTS = $modelPath
    $env:LOONGSON_AOI_LOCAL_DNN_MODEL = $modelPath
    $env:LOONGSON_AOI_DETECTOR_BACKEND = 'onnx'
    $env:LOONGSON_AOI_MOTOR_DRY_RUN = '1'
    $env:LOONGSON_AOI_ONNX_INPUT_IMGSZ = '1024'

    Write-Output "启动 v1.0.0 Windows 网站：http://$HostAddress`:$Port/"
    Write-Output '电机状态：dry-run（不会访问 GPIO 或驱动滑台）'
    Push-Location -LiteralPath $webDirectory
    try {
        & $venvPython -m uvicorn main:app --host $HostAddress --port $Port
        if ($LASTEXITCODE -ne 0) {
            throw "Uvicorn 异常退出，代码 $LASTEXITCODE。"
        }
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error "Windows 网站启动失败：$($_.Exception.Message)"
    exit 1
}
