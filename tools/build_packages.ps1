[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$script:CommonExcludedNames = @(
    '.git',
    '__pycache__',
    '.pytest_cache',
    '.venv',
    '.data',
    'outputs',
    'dist',
    'packages',
    'runtime',
    'project.private.config.json',
    'motor_state.json'
)

function Assert-File {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少必需文件：$Path"
    }
}

function Test-ExcludedPath {
    param(
        [Parameter(Mandatory)][string]$RelativePath,
        [string[]]$AdditionalExcludedNames = @()
    )
    $names = @($script:CommonExcludedNames) + @($AdditionalExcludedNames)
    $segments = $RelativePath -split '[\\/]'
    foreach ($segment in $segments) {
        if ($names -contains $segment) {
            return $true
        }
        if ($segment.EndsWith('.pyc', [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Add-FileToZip {
    param(
        [Parameter(Mandatory)][System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)][string]$SourcePath,
        [Parameter(Mandatory)][string]$EntryName
    )
    Assert-File -Path $SourcePath
    $normalizedEntry = $EntryName.Replace('\', '/')
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $Archive,
        $SourcePath,
        $normalizedEntry,
        [System.IO.Compression.CompressionLevel]::Optimal
    ) | Out-Null
}

function Add-DirectoryToZip {
    param(
        [Parameter(Mandatory)][System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)][string]$SourceRoot,
        [Parameter(Mandatory)][string]$EntryRoot,
        [string[]]$AdditionalExcludedNames = @()
    )
    if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
        throw "缺少必需目录：$SourceRoot"
    }
    $normalizedSource = [System.IO.Path]::GetFullPath($SourceRoot)
    foreach ($file in Get-ChildItem -LiteralPath $normalizedSource -Recurse -File -Force) {
        $relative = [System.IO.Path]::GetRelativePath($normalizedSource, $file.FullName)
        if (Test-ExcludedPath -RelativePath $relative -AdditionalExcludedNames $AdditionalExcludedNames) {
            continue
        }
        $entry = $EntryRoot.TrimEnd('/') + '/' + $relative.Replace('\', '/')
        Add-FileToZip -Archive $Archive -SourcePath $file.FullName -EntryName $entry
    }
}

function Add-Utf8TextToZip {
    param(
        [Parameter(Mandatory)][System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)][string]$EntryName,
        [Parameter(Mandatory)][string]$Content
    )
    $entry = $Archive.CreateEntry($EntryName.Replace('\', '/'), [System.IO.Compression.CompressionLevel]::Optimal)
    $stream = $entry.Open()
    $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
    try {
        $writer.Write($Content)
    }
    finally {
        $writer.Dispose()
        $stream.Dispose()
    }
}

function Add-CommonPackageContent {
    param(
        [Parameter(Mandatory)][System.IO.Compression.ZipArchive]$Archive,
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$PackageRoot
    )
    foreach ($name in @('README.md', 'LICENSE', 'VERSION')) {
        Add-FileToZip -Archive $Archive -SourcePath ([System.IO.Path]::Combine($ProjectRoot, $name)) -EntryName ($PackageRoot + '/' + $name)
    }
    Add-DirectoryToZip -Archive $Archive -SourceRoot ([System.IO.Path]::Combine($ProjectRoot, 'docs')) -EntryRoot ($PackageRoot + '/docs')
    Add-DirectoryToZip -Archive $Archive -SourceRoot ([System.IO.Path]::Combine($ProjectRoot, 'config', 'private')) -EntryRoot ($PackageRoot + '/config/private')
}

function New-PackageManifest {
    param(
        [Parameter(Mandatory)][string]$Platform,
        [Parameter(Mandatory)][string]$SourceCommit,
        [Parameter(Mandatory)][string]$ModelHash,
        [Parameter(Mandatory)][string]$OnnxHash
    )
    $manifest = [ordered]@{
        project = 'Loongson_AOI_PCB'
        version = 'v1.0.0'
        platform = $Platform
        source_commit = $SourceCommit
        generated_at = [DateTimeOffset]::Now.ToString('o')
        models = [ordered]@{
            best_pt_sha256 = $ModelHash
            best_onnx_sha256 = $OnnxHash
            onnx_input = '1024x1024'
            onnx_precision = 'FP32'
        }
        safety = [ordered]@{
            automated_tests_use_motor_dry_run = $true
            real_stage_is_open_loop = $true
        }
    }
    return ($manifest | ConvertTo-Json -Depth 5)
}

function New-PlatformPackage {
    param(
        [Parameter(Mandatory)][ValidateSet('windows', 'huawei_cloud', 'loongson_2k1000la')][string]$Platform,
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$OutputPath,
        [Parameter(Mandatory)][string]$SourceCommit,
        [Parameter(Mandatory)][string]$BestPtHash,
        [Parameter(Mandatory)][string]$BestOnnxHash
    )
    if (Test-Path -LiteralPath $OutputPath) {
        Remove-Item -LiteralPath $OutputPath -Force
    }
    $packageRoot = 'Loongson_AOI_PCB'
    $webRoot = [System.IO.Path]::Combine($ProjectRoot, 'worktrees', 'cloud-cooperative-platform')
    $algorithmRoot = [System.IO.Path]::Combine($ProjectRoot, 'worktrees', 'algorithm-ai')
    $cloudRoot = [System.IO.Path]::Combine($ProjectRoot, 'cloud-backend')
    $miniprogramRoot = [System.IO.Path]::Combine($ProjectRoot, 'miniprogram')
    $configPath = [System.IO.Path]::Combine($algorithmRoot, 'algorithm', 'configs', 'pcb_defects.yaml')
    $onnxPath = [System.IO.Path]::Combine($algorithmRoot, 'runs', 'detect', 'pcb_defects', 'weights', 'best.onnx')

    $archive = [System.IO.Compression.ZipFile]::Open($OutputPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        Add-CommonPackageContent -Archive $archive -ProjectRoot $ProjectRoot -PackageRoot $packageRoot
        Add-DirectoryToZip -Archive $archive -SourceRoot $webRoot -EntryRoot ($packageRoot + '/worktrees/cloud-cooperative-platform')

        if ($Platform -eq 'windows') {
            Add-DirectoryToZip -Archive $archive -SourceRoot $algorithmRoot -EntryRoot ($packageRoot + '/worktrees/algorithm-ai')
            Add-DirectoryToZip -Archive $archive -SourceRoot $cloudRoot -EntryRoot ($packageRoot + '/cloud-backend')
            Add-DirectoryToZip -Archive $archive -SourceRoot $miniprogramRoot -EntryRoot ($packageRoot + '/miniprogram')
            Add-DirectoryToZip -Archive $archive -SourceRoot ([System.IO.Path]::Combine($ProjectRoot, 'deploy', 'windows')) -EntryRoot ($packageRoot + '/deploy/windows')
        }
        elseif ($Platform -eq 'huawei_cloud') {
            Add-FileToZip -Archive $archive -SourcePath $configPath -EntryName ($packageRoot + '/worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml')
            Add-FileToZip -Archive $archive -SourcePath $onnxPath -EntryName ($packageRoot + '/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx')
            Add-DirectoryToZip -Archive $archive -SourceRoot $cloudRoot -EntryRoot ($packageRoot + '/cloud-backend')
            Add-DirectoryToZip -Archive $archive -SourceRoot $miniprogramRoot -EntryRoot ($packageRoot + '/miniprogram')
            Add-DirectoryToZip -Archive $archive -SourceRoot ([System.IO.Path]::Combine($ProjectRoot, 'deploy', 'huawei')) -EntryRoot ($packageRoot + '/deploy/huawei')
            Add-FileToZip -Archive $archive -SourcePath ([System.IO.Path]::Combine($ProjectRoot, 'config', 'private', 'cloud-backend.env')) -EntryName ($packageRoot + '/cloud-backend/.env')
            Add-FileToZip -Archive $archive -SourcePath ([System.IO.Path]::Combine($ProjectRoot, 'config', 'private', 'cloud-web.env')) -EntryName ($packageRoot + '/worktrees/cloud-cooperative-platform/.env')
            Add-FileToZip -Archive $archive -SourcePath ([System.IO.Path]::Combine($ProjectRoot, 'config', 'private', 'llm_config.local.ps1')) -EntryName ($packageRoot + '/worktrees/cloud-cooperative-platform/llm_config.local.ps1')
        }
        else {
            Add-FileToZip -Archive $archive -SourcePath $configPath -EntryName ($packageRoot + '/worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml')
            Add-FileToZip -Archive $archive -SourcePath $onnxPath -EntryName ($packageRoot + '/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx')
            Add-DirectoryToZip -Archive $archive -SourceRoot ([System.IO.Path]::Combine($ProjectRoot, 'deploy', 'loongson')) -EntryRoot ($packageRoot + '/deploy/loongson')
            Add-FileToZip -Archive $archive -SourcePath ([System.IO.Path]::Combine($ProjectRoot, 'config', 'private', 'llm_config.local.ps1')) -EntryName ($packageRoot + '/worktrees/cloud-cooperative-platform/llm_config.local.ps1')
        }

        $manifest = New-PackageManifest -Platform $Platform -SourceCommit $SourceCommit -ModelHash $BestPtHash -OnnxHash $BestOnnxHash
        Add-Utf8TextToZip -Archive $archive -EntryName ($packageRoot + '/PACKAGE_MANIFEST.json') -Content $manifest
    }
    finally {
        $archive.Dispose()
    }
}

try {
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..'))
    $packagesDirectory = [System.IO.Path]::Combine($projectRoot, 'packages')
    $bestPt = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai', 'runs', 'detect', 'pcb_defects', 'weights', 'best.pt')
    $bestOnnx = [System.IO.Path]::Combine($projectRoot, 'worktrees', 'algorithm-ai', 'runs', 'detect', 'pcb_defects', 'weights', 'best.onnx')
    Assert-File -Path $bestPt
    Assert-File -Path $bestOnnx
    New-Item -ItemType Directory -Path $packagesDirectory -Force | Out-Null

    $sourceCommit = (& git -C $projectRoot rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw '无法读取当前 Git 提交。'
    }
    $bestPtHash = (Get-FileHash -LiteralPath $bestPt -Algorithm SHA256).Hash
    $bestOnnxHash = (Get-FileHash -LiteralPath $bestOnnx -Algorithm SHA256).Hash
    $packageNames = [ordered]@{
        windows = 'Loongson_AOI_PCB_windows.zip'
        huawei_cloud = 'Loongson_AOI_PCB_huawei_cloud.zip'
        loongson_2k1000la = 'Loongson_AOI_PCB_loongson_2k1000la.zip'
    }

    foreach ($platform in $packageNames.Keys) {
        $outputPath = [System.IO.Path]::Combine($packagesDirectory, $packageNames[$platform])
        New-PlatformPackage -Platform $platform -ProjectRoot $projectRoot -OutputPath $outputPath -SourceCommit $sourceCommit -BestPtHash $bestPtHash -BestOnnxHash $bestOnnxHash
        $item = Get-Item -LiteralPath $outputPath
        Write-Output ("已生成 {0}：{1:N2} MB" -f $item.Name, ($item.Length / 1MB))
    }

    $checksumLines = @()
    foreach ($name in $packageNames.Values) {
        $path = [System.IO.Path]::Combine($packagesDirectory, $name)
        $checksumLines += ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $name)
    }
    $checksumPath = [System.IO.Path]::Combine($packagesDirectory, 'checksums.sha256')
    [System.IO.File]::WriteAllLines($checksumPath, $checksumLines, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText([System.IO.Path]::Combine($packagesDirectory, 'VERSION'), "v1.0.0`n", [System.Text.UTF8Encoding]::new($false))
    Write-Output "v1.0.0 三平台部署包构建完成：$packagesDirectory"
}
catch {
    Write-Error "部署包构建失败：$($_.Exception.Message)"
    exit 1
}
