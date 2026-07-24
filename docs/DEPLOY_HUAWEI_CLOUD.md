# 华为云部署

华为云包同时包含 8010 云检测 API 与 8020 完整网站。服务器建议 Ubuntu 24.04、4 vCPU、8GB 内存。

## Windows 侧上传

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
pwsh -File .\deploy\huawei\upload_huawei.ps1 -HostAddress 1.92.113.191 -UserName root
```

脚本上传 `packages/Loongson_AOI_PCB_huawei_cloud.zip`，调用包内更新脚本并验证两个端口。更新时保留服务器 `.env`、虚拟环境和运行数据；新服务器可使用包内 `config/private` 初始化配置。

## 验证

- `http://1.92.113.191:8010/health`
- `http://1.92.113.191:8020/health`
- `http://1.92.113.191:8020/api/system/status`

OBS 桶为私有桶。AK/SK 只由 ECS 使用，不应下发给浏览器或小程序。
