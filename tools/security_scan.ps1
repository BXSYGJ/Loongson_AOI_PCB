[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

try {
    $scriptDirectory = [System.IO.Path]::GetDirectoryName($PSCommandPath)
    $projectRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($scriptDirectory, '..'))
    $forbiddenFileNames = @('project.private.config.json', 'id_rsa', 'id_ed25519', 'MEMORY.md')
    $forbiddenPatterns = @(
        'github_pat_[A-Za-z0-9_]+',
        'ghp_[A-Za-z0-9]+',
        '-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----'
    )
    $textExtensions = @('.py', '.ps1', '.sh', '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.ini', '.env', '.service', '.c', '.h', '.js', '.cjs', '.html', '.css')
    $excludedSegments = @('.git', '.venv', '__pycache__', '.pytest_cache', 'packages')
    $violations = [System.Collections.Generic.List[string]]::new()

    foreach ($file in Get-ChildItem -LiteralPath $projectRoot -Recurse -File -Force) {
        $relative = [System.IO.Path]::GetRelativePath($projectRoot, $file.FullName)
        $segments = $relative -split '[\\/]'
        if (@($segments | Where-Object { $excludedSegments -contains $_ }).Count -gt 0) {
            continue
        }
        if ($forbiddenFileNames -contains $file.Name) {
            $violations.Add("禁止文件：$relative")
        }
        if ($file.Length -ge 100MB) {
            $violations.Add("超过 GitHub 100MB 限制：$relative")
        }
        if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) {
            continue
        }
        $content = [System.IO.File]::ReadAllText($file.FullName)
        foreach ($pattern in $forbiddenPatterns) {
            if ($content -match $pattern) {
                $violations.Add("命中敏感模式：$relative")
                break
            }
        }
    }

    $remoteUrl = (& git -C $projectRoot remote get-url origin).Trim()
    if ($remoteUrl -match '@github\.com' -and $remoteUrl -match '://[^/]+:[^@]+@') {
        $violations.Add('origin URL 包含内嵌凭据。')
    }
    if ($violations.Count -gt 0) {
        throw ($violations -join [Environment]::NewLine)
    }
    Write-Output '安全扫描通过：未发现 GitHub PAT、SSH 私钥、个人微信配置、memory 或超过 100MB 的单文件。'
    Write-Output '项目运行密钥仅允许保存在 config/private 和部署包中。'
}
catch {
    Write-Error "安全扫描失败：$($_.Exception.Message)"
    exit 1
}
