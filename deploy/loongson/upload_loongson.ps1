[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9.-]+$')]
    [string]$HostAddress,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$UserName = 'loongson',
    [string]$IdentityFile = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..', '..'))
    $packagePath = [System.IO.Path]::Combine($projectRoot, 'packages', 'Loongson_AOI_PCB_loongson_2k1000la.zip')
    if (-not (Test-Path -LiteralPath $packagePath -PathType Leaf)) {
        throw "未找到龙芯部署包：$packagePath"
    }

    $scp = (Get-Command -Name 'scp.exe' -ErrorAction Stop).Source
    $target = "$UserName@$HostAddress"
    $arguments = @()
    if (-not [string]::IsNullOrWhiteSpace($IdentityFile)) {
        $arguments += @('-i', (Resolve-Path -LiteralPath $IdentityFile).Path)
    }
    $arguments += @($packagePath, "$target`:/home/$UserName/Documents/Loongson_AOI_PCB_loongson_2k1000la.zip")
    & $scp @arguments
    if ($LASTEXITCODE -ne 0) {
        throw '龙芯部署包上传失败。'
    }
    Write-Output 'v1.0.0 龙芯部署包上传完成。'
    Write-Output "请在龙芯端将压缩包解压到 /home/$UserName/Documents，并先执行 dry-run 检查。"
}
catch {
    Write-Error "龙芯发布失败：$($_.Exception.Message)"
    exit 1
}
