# 龙芯教育派 v1.0.0 手动部署

> **部署前检查：** 本文只适用于
> `D:/AAAED/code/Loongson_AOI_PCB/worktrees/main/packages/Loongson_AOI_PCB_loongson_2k1000la.zip`。
> 不要误用 `D:\AAAED\code\Loongson_AOI_PCB\dist\loongson_phone_bundle.zip`；后者是早期旧包，不包含当前完整交接版本所需的统一目录和部署内容。

## 1. FinalShell 上传

在 FinalShell 文件管理器中，把 `Loongson_AOI_PCB_loongson_2k1000la.zip` 上传到：

```text
/home/loongson/Documents/Loongson_AOI_PCB_loongson_2k1000la.zip
```

## 2. 停止旧网站

在 FinalShell 终端进入 root，并停止可能占用 8000 端口的旧服务：

```sh
su
systemctl stop aoi-web 2>/dev/null || true
systemctl stop aoi-cloud 2>/dev/null || true
pkill -f "uvicorn main:app" 2>/dev/null || true
ss -ltnp | grep ':8000' || true
exit
```

如果最后一条端口检查仍显示进程，先使用 `ps -fp <PID>` 确认进程，再对明确的 PID 执行 `kill <PID>`。不要使用没有 PID 的 `kill -9`。

## 3. 备份并解压

```sh
cd /home/loongson/Documents
if [ -d Loongson_AOI_PCB ]; then cp -a Loongson_AOI_PCB "Loongson_AOI_PCB.backup.$(date +%Y%m%d_%H%M%S)"; fi
unzip -o Loongson_AOI_PCB_loongson_2k1000la.zip -d /home/loongson/Documents
```

解压后确认：

```sh
cat /home/loongson/Documents/Loongson_AOI_PCB/VERSION
ls -l /home/loongson/Documents/Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx
```

版本应为 `v1.0.0`。

## 4. 检查 Python 环境

```sh
cd /home/loongson/Documents/Loongson_AOI_PCB/worktrees/cloud-cooperative-platform
source /home/loongson/aoi_venv_312/bin/activate
sh deploy/loongson/check_phone_runtime.sh
```

如果缺少网站依赖：

```sh
sh deploy/loongson/install_phone_deps.sh
```

## 5. 先用 dry-run 启动

```sh
cd /home/loongson/Documents/Loongson_AOI_PCB/worktrees/cloud-cooperative-platform
sudo -E env \
  AOI_VENV=/home/loongson/aoi_venv_312 \
  AOI_HOST=0.0.0.0 \
  AOI_PORT=8000 \
  LOONGSON_AOI_CAMERA_SOURCE=0 \
  LOONGSON_AOI_MOTOR_DRY_RUN=1 \
  LOONGSON_AOI_HOME_MODE=soft \
  LOONGSON_AOI_DETECTOR_BACKEND=onnx \
  LOONGSON_AOI_ONNX_INPUT_IMGSZ=1024 \
  LOONGSON_AOI_OPENCV_THREADS=1 \
  LOONGSON_AOI_CLOUD_API_BASE=http://116.205.117.33:8010 \
  sh deploy/loongson/run_phone_server.sh
```

教育派本机 Midori 7.0 访问 `http://127.0.0.1:8000/`；同一局域网内的其他设备才访问 `http://<龙芯IP>:8000/`。先确认首页显示“WebSocket 在线”、系统状态可加载且当前为 motor dry-run。不要用华为云 `http://116.205.117.33:8020/` 代替本地页面控制实体滑台。浏览器兼容与触摸屏注意事项见 [龙芯教育派部署文档](../../docs/DEPLOY_LOONGSON.md#midori-7-触摸屏说明)。

## 6. 真实模式启动

只有在滑台已处于约定原点、行程内无障碍、GPIO 和急停条件已确认后，才把 `LOONGSON_AOI_MOTOR_DRY_RUN` 改为 `0`，并补充：

```text
LOONGSON_AOI_MOTOR_GPIO_BACKEND=system_python
LOONGSON_AOI_SYSTEM_GPIO_PYTHON=/usr/bin/python3
LOONGSON_AOI_MOTOR_AXIS_MAPPING=physical_x_motor_y_physical_y_motor_x
```

真实模式为开环控制。出现 `position_trusted=false`、运动中断或机械卡滞后，必须人工确认滑台位置并重新软回零，不能直接继续扫描。
