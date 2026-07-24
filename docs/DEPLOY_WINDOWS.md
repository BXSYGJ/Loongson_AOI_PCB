# Windows 部署

## 环境

- Windows 11
- PowerShell 7
- Python 3.12
- OpenCV、NumPy、FastAPI、Uvicorn

## 安装与启动

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Expand-Archive -LiteralPath .\packages\Loongson_AOI_PCB_windows.zip -DestinationPath .\runtime\windows -Force
Set-Location -LiteralPath .\runtime\windows\Loongson_AOI_PCB
pwsh -File .\deploy\windows\install_windows.ps1
pwsh -File .\deploy\windows\run_windows.ps1
```

打开 `http://127.0.0.1:8020/`。默认设置 `LOONGSON_AOI_MOTOR_DRY_RUN=1`。局域网访问时给 `run_windows.ps1` 传入 `-HostAddress 0.0.0.0`，并只开放可信局域网防火墙规则。

Windows 相机可在网站中选择，或通过 `camera_source.txt` 指定 `0`、`1`。USB 相机异常时可设置 `LOONGSON_AOI_CAMERA_BACKEND=DSHOW`。
