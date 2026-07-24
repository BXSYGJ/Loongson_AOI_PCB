[CmdletBinding()]
param(
    [string]$PythonCommand = 'python',
    [switch]$IncludeCloudBackend
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..', '..'))
    $venvDirectory = [System.IO.Path]::Combine($projectRoot, '.venv')
    $venvPython = [System.IO.Path]::Combine($venvDirectory, 'Scripts', 'python.exe')
    $webRequirements = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'cloud-cooperative-platform', 'requirements.txt')
    $cloudRequirements = [System.IO.Path]::Combine($projectRoot, 'cloud-backend', 'requirements.txt')

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        & $PythonCommand -m venv $venvDirectory
        if ($LASTEXITCODE -ne 0) {
            throw '创建 Windows Python 虚拟环境失败。'
        }
    }

    & $venvPython -m pip install --upgrade pip --timeout 120 --retries 10
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'pip 升级失败，继续使用虚拟环境内现有 pip。'
    }

    & $venvPython -m pip install -r $webRequirements --timeout 120 --retries 10
    if ($LASTEXITCODE -ne 0) {
        throw '安装完整网站依赖失败。'
    }

    if ($IncludeCloudBackend) {
        & $venvPython -m pip install -r $cloudRequirements --timeout 120 --retries 10
        if ($LASTEXITCODE -ne 0) {
            throw '安装华为云后端依赖失败。'
        }
    }

    & $venvPython -c "import cv2, fastapi, numpy, uvicorn; print('OpenCV', cv2.__version__, 'DNN', hasattr(cv2, 'dnn')); print('Windows dependencies OK')"
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 依赖导入检查失败。'
    }

    Write-Output "v1.0.0 Windows 环境安装完成：$venvDirectory"
}
catch {
    Write-Error "Windows 环境安装失败：$($_.Exception.Message)"
    exit 1
}
