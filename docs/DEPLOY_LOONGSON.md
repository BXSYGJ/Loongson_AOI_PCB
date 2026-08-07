# 龙芯教育派部署

> **必须确认包名：** 当前正式部署包为
> `D:/AAAED/code/Loongson_AOI_PCB/worktrees/main/packages/Loongson_AOI_PCB_loongson_2k1000la.zip`，
> 即仓库内的 `packages/Loongson_AOI_PCB_loongson_2k1000la.zip`。
> `D:\AAAED\code\Loongson_AOI_PCB\dist\loongson_phone_bundle.zip` 是旧版 phone bundle，内容和目录结构均不等同于当前完整部署包，请勿上传或用于本页步骤。

## FinalShell 手动上传

本项目不自动向龙芯板上传。请在 FinalShell 中把下列文件拖入龙芯板：

```text
packages/Loongson_AOI_PCB_loongson_2k1000la.zip
```

建议上传到：

```text
/home/loongson/Documents/Loongson_AOI_PCB_loongson_2k1000la.zip
```

随后在 FinalShell 终端按 [deploy/loongson/MANUAL_DEPLOY.md](../deploy/loongson/MANUAL_DEPLOY.md) 执行停止旧服务、备份、解压、检查和启动。最终网站目录为：

```text
/home/loongson/Documents/Loongson_AOI_PCB/worktrees/cloud-cooperative-platform
```

## 启动约束

- 网站 Python：`/home/loongson/aoi_venv_312/bin/python`。
- GPIO helper：`/usr/bin/python3`，使用系统 libgpiod v1。
- 真实 GPIO 通常需要 root。
- OV9281 默认相机源为 `0`。
- 当前轴映射：`physical_x_motor_y_physical_y_motor_x`。
- 本地 ONNX 输入为 1024×1024，OpenCV 线程数为 1。

先执行包内运行时检查和 motor dry-run。真实运动前人工把滑台放到约定原点，网页执行软回零后再开始扫描。解压动作不会自动启动网站或滑台。

## Midori 7 触摸屏说明

教育派当前确认的浏览器环境为：

- Midori `7.0-2.1`；
- WebKitGTK `2.26.4`；
- LoongArch64。

当前完整网站已经为该旧版内核提供 HTML 转义、AI SSE 非流式降级、单指 Touch Events 和鼠标拖动回退，并避免依赖 `String.prototype.replaceAll()`。不要自行替换 Midori 或单独升级 WebKitGTK，以免破坏龙芯系统现有 GTK、GStreamer 和 LoongArch64 软件包依赖。

在教育派触摸屏上直接操作时，Midori 必须访问：

```text
http://127.0.0.1:8000/
```

此时网页和 WebSocket 连接教育派本地 FastAPI，由本地进程控制滑台、GPIO 和 OV9281，相片再发送到华为云 `http://116.205.117.33:8010` 推理。`http://116.205.117.33:8020/` 是华为云上的远程网站，不能代替本地 `8000` 页面控制实体滑台。

使用时还需注意：

- 真实扫描使用后端 OV9281，不依赖 Midori 的浏览器相机权限。
- 更新部署包后应清理 Midori 页面缓存或重新启动浏览器，确保加载带 `20260807-midori7` 版本号的新脚本。
- 扫描期间不要关闭页面、切换地址、锁屏或让系统休眠；控制 WebSocket 断开时，后端会请求停止当前扫描。
- 触摸屏网页“停止”按钮不能替代物理急停；真实运动前仍需检查原点、行程和障碍物。
- 实机首次验收依次确认：首页显示“WebSocket 在线”、系统状态识别后端相机、手机图片可单指拖动、缺陷列表可显示中文、AI 工作台能生成或完整返回回答。

同一局域网内的其他设备需要访问教育派时，才使用 `http://<龙芯IP>:8000/`；教育派本机触摸屏始终优先使用 `127.0.0.1`，避免网络地址变化影响控制连接。
