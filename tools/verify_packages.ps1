[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..'))
    $packagesDirectory = [System.IO.Path]::Combine($projectRoot, 'packages')
    $checksumPath = [System.IO.Path]::Combine($packagesDirectory, 'checksums.sha256')
    $requiredByPackage = [ordered]@{
        'Loongson_AOI_PCB_windows.zip' = @(
            'Loongson_AOI_PCB/VERSION',
            'Loongson_AOI_PCB/PACKAGE_MANIFEST.json',
            'Loongson_AOI_PCB/deploy/windows/run_windows.ps1',
            'Loongson_AOI_PCB/worktrees/cloud-cooperative-platform/main.py',
            'Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.pt',
            'Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx'
        )
        'Loongson_AOI_PCB_huawei_cloud.zip' = @(
            'Loongson_AOI_PCB/VERSION',
            'Loongson_AOI_PCB/PACKAGE_MANIFEST.json',
            'Loongson_AOI_PCB/deploy/huawei/install_or_update.sh',
            'Loongson_AOI_PCB/cloud-backend/app/main.py',
            'Loongson_AOI_PCB/worktrees/cloud-cooperative-platform/main.py',
            'Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx'
        )
        'Loongson_AOI_PCB_loongson_2k1000la.zip' = @(
            'Loongson_AOI_PCB/VERSION',
            'Loongson_AOI_PCB/PACKAGE_MANIFEST.json',
            'Loongson_AOI_PCB/worktrees/cloud-cooperative-platform/main.py',
            'Loongson_AOI_PCB/worktrees/cloud-cooperative-platform/llm_chat.py',
            'Loongson_AOI_PCB/worktrees/cloud-cooperative-platform/motor_gpio_system_helper.py',
            'Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx'
        )
    }

    if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
        throw '缺少 packages/checksums.sha256。'
    }
    $expectedHashes = @{}
    foreach ($line in Get-Content -LiteralPath $checksumPath -Encoding UTF8) {
        if ($line -match '^([0-9a-fA-F]{64})\s{2}(.+)$') {
            $expectedHashes[$Matches[2]] = $Matches[1].ToUpperInvariant()
        }
    }

    foreach ($packageName in $requiredByPackage.Keys) {
        $packagePath = [System.IO.Path]::Combine($packagesDirectory, $packageName)
        if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
            throw "缺少部署包：$packageName"
        }
        $actualHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash
        if (-not $expectedHashes.ContainsKey($packageName) -or $expectedHashes[$packageName] -ne $actualHash) {
            throw "$packageName 的 SHA256 与清单不一致。"
        }

        $archive = [System.IO.Compression.ZipFile]::OpenRead($packagePath)
        try {
            $entryNames = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
            foreach ($entry in $archive.Entries) {
                [void]$entryNames.Add($entry.FullName)
                if ($entry.FullName.StartsWith('/') -or $entry.FullName.Contains('../') -or $entry.FullName.Contains(':')) {
                    throw "$packageName 包含不安全路径：$($entry.FullName)"
                }
            }
            foreach ($requiredEntry in $requiredByPackage[$packageName]) {
                if (-not $entryNames.Contains($requiredEntry)) {
                    throw "$packageName 缺少：$requiredEntry"
                }
            }
        }
        finally {
            $archive.Dispose()
        }
        Write-Output "校验通过：$packageName"
    }
    Write-Output 'v1.0.0 三个平台部署包和 SHA256 全部有效。'
}
catch {
    Write-Error "部署包校验失败：$($_.Exception.Message)"
    exit 1
}
