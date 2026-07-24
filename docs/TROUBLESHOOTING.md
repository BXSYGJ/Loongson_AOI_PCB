# 故障排查

## 网站无法启动

- 检查端口占用：使用 `Get-NetTCPConnection` 和 `Get-Process`。
- 检查 `llm_chat.py`、`best.onnx`、`pcb_defects.yaml` 是否随包存在。
- 龙芯必须确认启动脚本使用 `/home/loongson/aoi_venv_312`，不是 `/root/aoi_venv_312`。

## OpenCV DNN Reshape 或 Concat 错误

模型是固定 1024×1024 输入。确认 `LOONGSON_AOI_ONNX_INPUT_IMGSZ=1024`，不要向固定模型传入 640、960 或非 letterbox 输入。

## 滑台间歇不动

当前控制是开环的，程序只能验证输出脉冲，不能证明机械位移。独立运动脚本日志正常但机械不动时，应检查 TB6600、24V 电源、公共地、PUL/DIR 电平裕量、端子和机械阻力。普通短程能运行不能排除边缘电气问题。

出现 `position_trusted=false` 时不要绕过保护。人工确认物理原点后执行软回零。自动测试和普通电脑调试必须保持 motor dry-run。

## 手机页面不同步

电脑端“刷新队列”会选择最新有效上传记录。批量检测最多选择 9 张；批量删除不限数量，但检测中的记录不能删除。

## LLM 报告 500

检查私有配置、模型白名单、网络和 120 秒超时。报告图片或 result JSON 缺失时后端不会调用模型。聊天使用 SSE，浏览器中止后不保存未完成回答。
