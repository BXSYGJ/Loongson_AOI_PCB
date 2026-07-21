# 龙芯教育派 2K1000LA 模拟模式优先部署说明

本说明用于第一阶段验收：在龙芯教育派 2K1000LA 上运行 AOI 网站，并优先使用“模拟模式”导入已知 PCB 图片进行 OpenCV DNN + ONNX 检测。此阶段不接 OV9281、不控制滑台，电机固定为 dry-run。

第一轮请使用 `640x640` 或长边不超过 `960` 的 PCB 图片，并把 PCB 长宽填为 `30mm x 30mm`，让模拟扫描只生成 1 个 FOV cell。这样可以先验证网站、ONNX 推理、UI 事件流和结果文件生成，不会像手机整板 tiled 检测那样把 2K1000LA 压满。

## 1. 在 Windows 上生成传输包

在 PowerShell 中执行：

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB\worktrees\ui-integration
.\deploy\loongson\make_loongson_phone_bundle.ps1
```

脚本会生成：

```text
dist\loongson_phone_bundle.zip
```

把这个 zip 通过 FinalShell 或 U 盘传到龙芯板的 `~/Documents`。

## 2. 在龙芯板上解压

在 FinalShell 终端中执行：

```sh
cd ~/Documents
unzip -o loongson_phone_bundle.zip
cd ~/Documents/Loongson_AOI_PCB/worktrees/ui-integration
```

解压后关键路径应为：

```text
~/Documents/Loongson_AOI_PCB/worktrees/ui-integration/main.py
~/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml
~/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx
```

## 3. 安装网站依赖

使用你已有的 Python 3.12 虚拟环境：

```sh
source ~/aoi_venv_312/bin/activate
python -c "import cv2, numpy; print(cv2.__version__); print(hasattr(cv2, 'dnn'))"
```

然后安装 FastAPI/Uvicorn：

```sh
cd ~/Documents/Loongson_AOI_PCB/worktrees/ui-integration
sh deploy/loongson/install_phone_deps.sh
```

如果你的 venv 不在 `~/aoi_venv_312`，先指定：

```sh
export AOI_VENV=/path/to/your/venv
sh deploy/loongson/install_phone_deps.sh
```

## 4. 板端运行前检查

```sh
cd ~/Documents/Loongson_AOI_PCB/worktrees/ui-integration
sh deploy/loongson/check_phone_runtime.sh
```

期望看到：

```text
Runtime check passed.
```

其中 detector 状态应包含：

```json
{
  "backend": "onnx_opencv",
  "weights_exists": true,
  "onnx_input_imgsz": 640
}
```

## 5. 启动网站

```sh
cd ~/Documents/Loongson_AOI_PCB/worktrees/ui-integration
sh deploy/loongson/run_phone_server.sh
```

脚本默认设置：

```text
LOONGSON_AOI_DETECTOR_BACKEND=onnx
LOONGSON_AOI_YOLO_CONF=0.5
LOONGSON_AOI_YOLO_IMGSZ=640
LOONGSON_AOI_ONNX_INPUT_IMGSZ=640
LOONGSON_AOI_MOTOR_DRY_RUN=1
LOONGSON_AOI_BOARD_SAFE_MODE=1
LOONGSON_AOI_OPENCV_THREADS=1
LOONGSON_AOI_SIMULATION_MAX_DIMENSION=960
LOONGSON_AOI_SIMULATION_JPEG_QUALITY=82
```

脚本会使用 `nice -n 10` 低优先级启动 Uvicorn，尽量避免 ONNX 推理时把 SSH/FinalShell 挤掉。

在板端本机验证：

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/system/status
```

## 6. 模拟模式优先验收

查看板端 IP：

```sh
hostname -I
```

电脑端完整网站：

```text
http://<龙芯板IP>:8000/
```

推荐验收流程：

1. 打开完整网站，进入“模拟模式”。
2. 导入一张已知 PCB 图片，第一轮建议使用 `640x640` 或长边不超过 `960` 的 val 集图片。
3. PCB 长宽填 `30mm x 30mm`。
4. 点击开始检测。
5. 等待流程完成：导入图 → 单格填图 → 拼接复原 → 一次 ONNX 推理 → UI 显示整板结果。
6. 检测期间另开一个 FinalShell 观察：
   ```sh
   ps -o pid,%cpu,%mem,etime,rss,cmd -p <uvicorn_pid>
   free -h
   ```

输出目录：

```text
~/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/algorithm/outputs/scans/<scan_id>/
  manifest.json
  r000_c000/raw.png
  mosaic/mosaic_raw.png
  mosaic/mosaic_annotated.jpg
  mosaic/mosaic_result.json
```

## 7. 手机拍照模块暂缓

手机和电脑连接同一个局域网后仍可访问：

```text
http://<龙芯板IP>:8000/phone
```

但 2K1000LA 第一阶段不要优先使用手机整板图检测。手机图即使压缩后仍可能触发多次 tiled ONNX 推理，容易让板子长时间满负载。等模拟模式稳定后，再调低手机上传尺寸和 tile 策略。

手机模块输出目录：

```text
~/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/algorithm/outputs/mobile_uploads/<upload_id>/
  raw.jpg
  annotated.jpg
  result.json
  metadata.json
```

## 8. 常见问题

### 手机打不开网页

确认网站是用 `0.0.0.0:8000` 启动，而不是 `127.0.0.1:8000`。确认手机和龙芯板在同一局域网，并使用龙芯板 IP 访问。

### 模拟模式导入大图后仍然很慢

确认状态接口中模拟模式安全参数已经生效：

```sh
curl http://127.0.0.1:8000/api/system/status
```

期望看到 `simulation.runtime.max_dimension` 为 `960`，`algorithm.runtime.opencv_threads` 为 `1`。如果仍然慢，第一轮请手动换成 `640x640` 的 val 集图片，并保持 PCB 长宽 `30mm x 30mm`。

### `weights_exists` 为 false

检查：

```sh
ls ~/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx
```

如果路径不同，启动前手动指定：

```sh
export LOONGSON_AOI_WEIGHTS=/absolute/path/to/best.onnx
```

### 高精模式没有使用 960

当前 `best.onnx` 是固定 `640x640` 输入，OpenCV DNN 不能直接喂 `960x960`。网站会自动跳过不兼容的 960，保留多 tile 复核。若以后需要 960，需要重新导出匹配或 dynamic ONNX。

### 电机/GPIO 显示 dry-run

这是第一阶段的预期行为。真实滑台联调时再把 `LOONGSON_AOI_MOTOR_DRY_RUN` 设为 `0`，并配置 TB6600 GPIO 偏移量。
