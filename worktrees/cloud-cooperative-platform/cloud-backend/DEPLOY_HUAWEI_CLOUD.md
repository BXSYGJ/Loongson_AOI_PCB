# 华为云 ECS + OBS + APIG 部署说明

第一阶段只部署“小程序上传检测”链路，不接滑台和 OV9281。

## 1. ECS 准备

在 ECS 上安装 Python、OpenCV、NumPy，并复制：

- `cloud-backend/`
- `worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx`
- `worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml`

安装依赖：

```sh
python -m pip install -r cloud-backend/requirements.txt
```

## 2. OBS 准备

创建 OBS bucket，并为 ECS 准备有最小权限的 AK/SK：

- 上传原图到 `cloud/jobs/<job_id>/raw.jpg`
- 读取原图
- 上传 `annotated.jpg`
- 上传 `result.json`
- 生成短期 GET/PUT 签名 URL

AK/SK 只放在 ECS 环境变量里，不写进小程序。

## 3. ECS 环境变量

```sh
export AOI_STORAGE_BACKEND=obs
export AOI_DETECTOR_BACKEND=onnx
export AOI_MODEL_PATH=/home/loongson/Loongson_AOI_PCB/worktrees/algorithm-ai/runs/detect/pcb_defects/weights/best.onnx
export AOI_CONFIG_PATH=/home/loongson/Loongson_AOI_PCB/worktrees/algorithm-ai/algorithm/configs/pcb_defects.yaml
export AOI_YOLO_CONF=0.5
export AOI_YOLO_IMGSZ=640
export AOI_ONNX_INPUT_IMGSZ=640
export HUAWEI_OBS_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
export HUAWEI_OBS_BUCKET=your-bucket
export HUAWEI_OBS_AK=your-ak
export HUAWEI_OBS_SK=your-sk
export AOI_PUBLIC_API_BASE=https://aoi-api.example.com
export AOI_PUBLIC_WS_BASE=wss://aoi-api.example.com
```

启动：

```sh
cd cloud-backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

## 3.1 ECS 后台常驻运行

调试验证通过后，不建议长期把 uvicorn 挂在 SSH 终端里运行。把 `.env` 写好并确认 `/health` 正常后，在 ECS 上执行：

```sh
cd /root/Loongson_AOI_PCB/cloud-backend
sh deploy/ecs/install-systemd.sh
```

常用管理命令：

```sh
systemctl status aoi-cloud --no-pager
systemctl restart aoi-cloud
journalctl -u aoi-cloud -f
```

如果修改了 `.env`，执行 `systemctl restart aoi-cloud` 使配置生效。

## 4. APIG / 反向代理

将公网 HTTPS 域名转发到 ECS `8010`：

- HTTP API：`/api/cloud/*`、`/health`
- WebSocket：`/ws/cloud/jobs/*`

如果使用 APIG，需要同时配置 HTTPS 和 WebSocket 转发。正式小程序必须使用 HTTPS/WSS 域名。

## 5. 微信小程序后台

在微信公众平台配置：

- request 合法域名：`https://aoi-api.example.com`
- socket 合法域名：`wss://aoi-api.example.com`
- uploadFile / downloadFile 合法域名：OBS 域名和 API 域名

然后把 `miniprogram/app.js` 里的地址改成 APIG 域名。

## 6. 验收

1. `GET /health` 返回 `status=ok`。
2. 小程序创建 job，得到 `upload_url`。
3. 图片上传到 OBS。
4. `POST /api/cloud/jobs/{job_id}/uploaded` 后任务进入 `detecting`。
5. WebSocket 收到 `result`。
6. OBS 中出现 `raw.jpg`、`annotated.jpg`、`result.json`。
