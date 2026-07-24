[CmdletBinding()]
param(
    [string]$PythonCommand = 'python'
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][scriptblock]$Action
    )
    Write-Output "开始：$Description"
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败，退出代码 $LASTEXITCODE。"
    }
    Write-Output "通过：$Description"
}

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..'))
    $webRoot = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'cloud-cooperative-platform')
    $algorithmRoot = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai')
    $cloudRoot = [System.IO.Path]::Combine($projectRoot, 'cloud-backend')
    $node = (Get-Command -Name 'node.exe' -ErrorAction Stop).Source
    $env:LOONGSON_AOI_MOTOR_DRY_RUN = '1'
    $env:LOONGSON_AOI_OPENCV_THREADS = '1'

    Push-Location -LiteralPath $webRoot
    try {
        Invoke-Checked -Description '完整网站 Python 测试' -Action { & $PythonCommand -m pytest -q }
        Invoke-Checked -Description '前端辅助函数测试' -Action { & $node test_frontend_helpers.cjs }
        Invoke-Checked -Description '前端 JavaScript 语法检查' -Action { & $node --check ([System.IO.Path]::Combine($webRoot, 'static', 'app.js')) }
    }
    finally {
        Pop-Location
    }

    Push-Location -LiteralPath $algorithmRoot
    try {
        Invoke-Checked -Description '算法模块测试' -Action { & $PythonCommand -m pytest -q }
    }
    finally {
        Pop-Location
    }

    $compileFiles = @(
        [System.IO.Path]::Combine($webRoot, 'main.py'),
        [System.IO.Path]::Combine($webRoot, 'camera_trigger.py'),
        [System.IO.Path]::Combine($webRoot, 'local_dnn_detector.py'),
        [System.IO.Path]::Combine($webRoot, 'llm_reporter.py'),
        [System.IO.Path]::Combine($webRoot, 'llm_chat.py'),
        [System.IO.Path]::Combine($webRoot, 'motor_controller.py'),
        [System.IO.Path]::Combine($webRoot, 'motor_gpio_system_helper.py'),
        [System.IO.Path]::Combine($cloudRoot, 'app', 'main.py'),
        [System.IO.Path]::Combine($cloudRoot, 'app', 'detector.py')
    )
    Invoke-Checked -Description '核心 Python 模块编译检查' -Action { & $PythonCommand -m py_compile @compileFiles }
    Write-Output 'v1.0.0 软件测试完成；测试过程未访问 GPIO。'
}
catch {
    Write-Error "全量测试失败：$($_.Exception.Message)"
    exit 1
}
