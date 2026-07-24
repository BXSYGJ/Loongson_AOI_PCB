[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Add-DirectoryToArchive {
    param(
        [Parameter(Mandatory)]
        [System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)]
        [string]$SourceRoot,
        [Parameter(Mandatory)]
        [string]$EntryRoot,
        [string[]]$ExcludedNames = @()
    )

    $normalizedRoot = [System.IO.Path]::GetFullPath($SourceRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $relativeOffset = $normalizedRoot.Length + 1
    Get-ChildItem -LiteralPath $normalizedRoot -Recurse -File -Force | ForEach-Object {
        $relative = $_.FullName.Substring($relativeOffset)
        $segments = $relative -split '[\\/]'
        if (@($segments | Where-Object { $ExcludedNames -contains $_ }).Count -gt 0) {
            return
        }
        $entryName = $EntryRoot.TrimEnd('/') + '/' + $relative.Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Archive,
            $_.FullName,
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}

function Add-FileToArchive {
    param(
        [Parameter(Mandatory)]
        [System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)]
        [string]$SourcePath,
        [Parameter(Mandatory)]
        [string]$EntryName
    )

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "缺少打包文件: $SourcePath"
    }
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $Archive,
        $SourcePath,
        $EntryName,
        [System.IO.Compression.CompressionLevel]::Optimal
    ) | Out-Null
}

try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath(
        [System.IO.Path]::Combine($scriptDirectory, '..', '..')
    )
    $distDirectory = [System.IO.Path]::Combine($projectRoot, 'dist')
    $zipPath = [System.IO.Path]::Combine($distDirectory, 'loongson-aoi-huawei-full-ecs.zip')
    $cloudRoot = [System.IO.Path]::Combine($projectRoot, 'cloud-backend')
    $webRoot = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'cloud-cooperative-platform')
    $algorithmRoot = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai')

    if (-not (Test-Path -LiteralPath $distDirectory -PathType Container)) {
        New-Item -ItemType Directory -Path $distDirectory -Force | Out-Null
    }
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    $archive = [System.IO.Compression.ZipFile]::Open(
        $zipPath,
        [System.IO.Compression.ZipArchiveMode]::Create
    )
    try {
        foreach ($name in @('.env.example', '.gitignore', 'DEPLOY_HUAWEI_CLOUD.md', 'README.md', 'requirements.txt')) {
            Add-FileToArchive -Archive $archive `
                -SourcePath ([System.IO.Path]::Combine($cloudRoot, $name)) `
                -EntryName ('cloud-backend/' + $name)
        }
        foreach ($name in @('app', 'deploy', 'scripts', 'tests')) {
            Add-DirectoryToArchive -Archive $archive `
                -SourceRoot ([System.IO.Path]::Combine($cloudRoot, $name)) `
                -EntryRoot ('cloud-backend/' + $name) `
                -ExcludedNames @('__pycache__', '.pytest_cache', '.venv', '.data')
        }

        Add-DirectoryToArchive -Archive $archive `
            -SourceRoot $webRoot `
            -EntryRoot 'worktrees/cloud-cooperative-platform' `
            -ExcludedNames @('.git', '.env', '.venv', '.data', 'dist', '__pycache__', '.pytest_cache', 'outputs')

        Add-FileToArchive -Archive $archive `
            -SourcePath ([System.IO.Path]::Combine($algorithmRoot, 'algorithm', 'configs', 'pcb_defects.yaml')) `
            -EntryName 'worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml'
        Add-FileToArchive -Archive $archive `
            -SourcePath ([System.IO.Path]::Combine($algorithmRoot, 'runs', 'detect', 'pcb_defects', 'weights', 'best.onnx')) `
            -EntryName 'worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx'

        foreach ($name in @('configure_cloud_platform.sh', 'deploy_huawei_full_update.sh', 'restart_verify_full_cloud.sh')) {
            Add-FileToArchive -Archive $archive `
                -SourcePath ([System.IO.Path]::Combine($distDirectory, $name)) `
                -EntryName $name
        }
    }
    finally {
        $archive.Dispose()
    }

    $zip = Get-Item -LiteralPath $zipPath
    [PSCustomObject]@{
        ZipPath = $zip.FullName
        SizeMB = [Math]::Round($zip.Length / 1MB, 2)
        Sha256 = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
    }
}
catch {
    Write-Error "华为云部署包生成失败: $($_.Exception.Message)"
    exit 1
}
