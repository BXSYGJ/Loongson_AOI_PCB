$ErrorActionPreference = "Stop"

$ApiBase = if ($env:AOI_PUBLIC_API_BASE) { $env:AOI_PUBLIC_API_BASE.TrimEnd("/") } else { "http://127.0.0.1:8010" }
$ImagePath = if ($args.Length -gt 0) { $args[0] } else { "" }

if (-not $ImagePath) {
  throw "Usage: .\scripts\check-local-flow.ps1 D:\path\to\pcb.jpg"
}
if (-not (Test-Path -LiteralPath $ImagePath)) {
  throw "Image not found: $ImagePath"
}

$filename = Split-Path -Leaf $ImagePath
$create = Invoke-RestMethod -Method Post -Uri "$ApiBase/api/cloud/jobs" -ContentType "application/json" -Body (@{
  filename = $filename
  profile = "fast"
  client_type = "powershell_check"
} | ConvertTo-Json)

Invoke-RestMethod -Method Put -Uri $create.upload_url -InFile $ImagePath -ContentType "image/jpeg" | Out-Null

Invoke-RestMethod -Method Post -Uri "$ApiBase/api/cloud/jobs/$($create.job_id)/uploaded" -ContentType "application/json" -Body (@{
  object_key = $create.object_key
  size_bytes = (Get-Item -LiteralPath $ImagePath).Length
} | ConvertTo-Json) | Out-Null

$result = $null
for ($i = 0; $i -lt 30; $i++) {
  $result = Invoke-RestMethod -Method Get -Uri "$ApiBase/api/cloud/jobs/$($create.job_id)"
  if ($result.status -in @("done", "error")) {
    break
  }
  Start-Sleep -Milliseconds 300
}

$result | ConvertTo-Json -Depth 8
