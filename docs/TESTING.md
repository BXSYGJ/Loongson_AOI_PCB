# 测试说明

## 一键测试

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
pwsh -File .\tools\test_all.ps1
```

脚本执行网站 pytest、算法 pytest、JavaScript 语法检查、前端辅助测试、Python 编译检查和关键模型存在性检查。电机强制使用 dry-run，不会访问 GPIO。

## 测试夹具

- `tests/fixtures/val_136_139`：四张验证图片及 YOLO 标签。
- `tests/fixtures/field_samples`：两张现场有缺陷样本和两张无缺陷负样本。
- `tests/fixtures/real_scan_artifact_6`：真实模式 16 图采集样本，用于拼接和导出回归。

完整购买数据集不在仓库中。训练前把数据集放到配置指定路径，或修改 `algorithm/configs/pcb_defects.yaml` 的 `path`。

## 硬件测试

硬件测试不属于自动测试。停止网站后，在龙芯上以 root 单独运行 `references/hardware/motor_xy_snake_test.py --dry-run`，确认路径后再人工运行实机测试。测试前必须确认滑台位于逻辑原点且 90×54mm 范围无障碍。
