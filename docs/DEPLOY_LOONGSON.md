# 龙芯教育派部署

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
