# 基于龙芯教育派的 PCB 智能 AOI 系统

当前交接版本：**v1.0.0**

本仓库是项目唯一维护主线，包含 Windows、华为云 ECS、龙芯教育派 2K1000LA、微信小程序、YOLOv8 训练与 OpenCV DNN 推理代码。

## 系统能力

- 两种产品操作模式：真实模式、手机拍照模式；模拟算法与兼容 API 仅保留用于内部测试。
- 真实模式：固定 4×4 蛇形扫描，滑台移动、OV9281 拍照、逐格检测、16 图拼接和标注图导出。
- 检测实验档位按“本地（龙芯）→ 手机 → 华为云”排列，默认本地（龙芯）；三个档位实际推理均由华为云执行。手机拍照模式保留链路时延实验，真实模式不增加延时。
- 手机批量上传：一次最多 9 张，支持批量检测、精确删除、标注图 ZIP 导出。
- AI 诊断：Qwen3.7 Plus、Qwen3.6 Flash、Qwen3.7 Max，支持深度思考、Markdown 报告、SSE 连续聊天和多图/多缺陷上下文。
- 龙芯本地推理：Python 3.12 + OpenCV DNN + FP32 ONNX，不依赖 PyTorch、Ultralytics 或 PyYAML。

## 目录

| 路径 | 内容 |
| --- | --- |
| `worktrees/cloud-cooperative-platform` | 完整 FastAPI 网站、前端、相机、滑台、拼接和 LLM 工作台 |
| `worktrees/algorithm-ai` | 训练、评估、数据转换、模型和算法测试 |
| `cloud-backend` | 华为云 ECS + OBS 检测服务 |
| `miniprogram` | 微信小程序工程 |
| `loong_detect` | 龙芯 OpenCV DNN 独立验证代码 |
| `motor_source_code` | 队友原始滑台控制参考代码 |
| `tests/fixtures` | 精简验证图片、现场样本和 16 图扫描样本 |
| `references` | 硬件手册、竞赛题目、运动测试和基准结果 |
| `config/private` | 私有仓库内的项目运行配置与密钥 |
| `packages` | v1.0.0 三平台部署 ZIP 与 SHA256 |

## 三个平台部署包

> **龙芯部署包注意：** 当前龙芯教育派 2K1000LA 的正式部署包是
> `D:/AAAED/code/Loongson_AOI_PCB/worktrees/main/packages/Loongson_AOI_PCB_loongson_2k1000la.zip`
>（仓库内相对路径为 `packages/Loongson_AOI_PCB_loongson_2k1000la.zip`）。
> **不要使用**旧路径 `D:\AAAED\code\Loongson_AOI_PCB\dist\loongson_phone_bundle.zip`；该文件属于早期 phone bundle，不能替代当前完整龙芯部署包。
>
> **教育派触摸屏浏览器：** 已针对系统自带的 Midori 7.0（WebKitGTK 2.26.4）增加兼容处理。实体教育派应在 Midori 中打开 `http://127.0.0.1:8000/`，由本地服务控制滑台和 OV9281，再调用华为云 8010 推理；不要用华为云 `http://116.205.117.33:8020/` 代替本地控制页面。详细说明见 [龙芯教育派部署文档](docs/DEPLOY_LOONGSON.md#midori-7-触摸屏说明)。
> 教育派启动脚本默认启用队友新增的轻量触摸屏首页；完整版仍可通过 `http://127.0.0.1:8000/full` 打开，手机批量上传页为 `http://127.0.0.1:8000/phone`。扫描进度会同步广播到所有已连接的控制页面，只有发起扫描的页面断开时才请求停止该次扫描。

| 包 | 用途 |
| --- | --- |
| `packages/Loongson_AOI_PCB_windows.zip` | Windows 11 + Python 3.12 本地开发、网站和模型验证 |
| `packages/Loongson_AOI_PCB_huawei_cloud.zip` | Ubuntu ECS 上部署 8010 云检测服务和 8020 完整网站 |
| `packages/Loongson_AOI_PCB_loongson_2k1000la.zip` | 龙芯教育派网站、OV9281、滑台和本地 ONNX 推理 |

部署前执行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
pwsh -File .\tools\verify_packages.ps1
```

## 快速入口

- Windows：[docs/DEPLOY_WINDOWS.md](docs/DEPLOY_WINDOWS.md)
- 华为云：[docs/DEPLOY_HUAWEI_CLOUD.md](docs/DEPLOY_HUAWEI_CLOUD.md)
- 龙芯教育派：[docs/DEPLOY_LOONGSON.md](docs/DEPLOY_LOONGSON.md)
- 龙芯 FinalShell 手动步骤：[deploy/loongson/MANUAL_DEPLOY.md](deploy/loongson/MANUAL_DEPLOY.md)
- 队友接手：[docs/HANDOFF.md](docs/HANDOFF.md)
- 完整测试：[docs/TESTING.md](docs/TESTING.md)
- 故障排查：[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## 模型版本

- `best.pt`：2026-07-16 现场增强训练结果。
- `best.onnx`：由上述权重导出的 FP32、固定 1024×1024 ONNX。
- 类别：`dry_solder_joint`、`improper_installation`、`pcb_damage`、`short_circuit`、`insufficient_solder`、`reverse_component`、`excess_solder`。
- 默认检测阈值：`0.3`。

## 安全说明

仓库必须保持私有。`config/private` 包含项目运行密钥，但不包含 GitHub PAT、SSH 私钥、SSH 口令或微信个人开发者配置。真实模式为开环滑台控制，运行前必须确认原点、行程、接线和急停条件；自动化测试始终使用 motor dry-run。

## 版本

`v1.0.0` 是首次统一主线交接版本。旧功能分支保留作为历史记录，后续开发以 `main` 为准。
