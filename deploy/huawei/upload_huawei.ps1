[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9.-]+$')]
    [string]$HostAddress,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$UserName = 'root',
    [string]$IdentityFile = '',
    [switch]$UploadOnly
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Resolve-OpenSshTool {
    param(
        [Parameter(Mandatory)]
        [ValidateSet('scp.exe', 'ssh.exe')]
        [string]$Name
    )

    $command = Get-Command -Name $Name -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $systemTool = [System.IO.Path]::Combine(
        $env:SystemRoot,
        'System32',
        'OpenSSH',
        $Name
    )
    if (Test-Path -LiteralPath $systemTool -PathType Leaf) {
        return $systemTool
    }

    throw "未找到 OpenSSH 工具：$Name"
}

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..', '..'))
    $packagePath = [System.IO.Path]::Combine($projectRoot, 'packages', 'Loongson_AOI_PCB_huawei_cloud.zip')
    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
        throw "未找到华为云部署包：$packagePath"
    }

    $scp = Resolve-OpenSshTool -Name 'scp.exe'
    $ssh = Resolve-OpenSshTool -Name 'ssh.exe'
    $target = "$UserName@$HostAddress"
    $commonArguments = @()
    if (-not [string]::IsNullOrWhiteSpace($IdentityFile)) {
        $resolvedIdentity = (Resolve-Path -LiteralPath $IdentityFile).Path
        $commonArguments += @('-i', $resolvedIdentity)
    }

    $scpArguments = @($commonArguments) + @($packagePath, "$target`:/root/Loongson_AOI_PCB_huawei_cloud.zip")
    & $scp @scpArguments
    if ($LASTEXITCODE -ne 0) {
        throw '华为云部署包上传失败。'
    }
    Write-Output 'v1.0.0 华为云部署包上传完成。'

    if (-not $UploadOnly) {
        $remoteCommand = "command -v unzip >/dev/null 2>&1 || (apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y unzip); unzip -p /root/Loongson_AOI_PCB_huawei_cloud.zip Loongson_AOI_PCB/deploy/huawei/install_or_update.sh > /root/install_aoi_v1.sh && chmod 700 /root/install_aoi_v1.sh && /root/install_aoi_v1.sh /root/Loongson_AOI_PCB_huawei_cloud.zip"
        $sshArguments = @($commonArguments) + @($target, $remoteCommand)
        & $ssh @sshArguments
        if ($LASTEXITCODE -ne 0) {
            throw 'ECS 替换、重启或健康检查失败。'
        }
        Write-Output "v1.0.0 华为云部署验证完成：http://$HostAddress`:8020/"
    }
}
catch {
    Write-Error "华为云发布失败：$($_.Exception.Message)"
    exit 1
}
