WWWWSSWE

# 局域网访问说明

本项目的 Web 中控台由 FastAPI 在主控电脑或龙芯板本机运行，手机和其他电脑通过同一局域网访问。

## 启动

Windows:

```powershell
.\start_lan_server.ps1
```

Linux / 龙芯板:

```sh
sh ./start_lan_server.sh
```

等价命令:

```sh
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## 访问

打开网页右侧“系统状态”中的“局域网访问地址”，例如：

```text
http://10.22.43.5:8000/
```

手机和其他电脑必须连接同一 Wi-Fi/网线网络。若打不开，请检查防火墙是否放行 TCP 8000。

## 当前阶段

未接入真实滑台、相机、YOLO 权重时，请保持“Mock联调模式”勾选。取消 Mock 后，系统会先检查抓拍图片和权重是否就绪。
