# PCB Defect Detection Algorithm

本目录是项目的算法模块，当前使用 YOLOv8 完成 PCB 焊接缺陷训练与单图推理。

## 数据集

默认数据集配置位于：

```text
algorithm/configs/pcb_defects.yaml
```

当前配置使用项目内数据集：

```text
datasets/pcb_defects
  images/
    train/
    val/
  labels/
    train/
    val/
```

该数据目录由 `D:/BaiduNetdiskDownload/database/dataset` 覆盖而来，没有独立 `test` 划分，配置中暂时让 `test` 复用 `images/val`。

## 缺陷类别

| id | 英文类别              | 中文含义 |
| -- | --------------------- | -------- |
| 0  | dry_solder_joint      | 干焊点   |
| 1  | improper_installation | 安装不当 |
| 2  | pcb_damage            | PCB损伤  |
| 3  | short_circuit         | 短路     |
| 4  | insufficient_solder   | 少锡     |
| 5  | reverse_component     | 反向     |
| 6  | excess_solder         | 多锡     |

## 标注格式

当前新增数据集的标签是 YOLO polygon/segmentation 格式，每一行表示一个目标：

```text
class_id x1 y1 x2 y2 x3 y3 ...
```

坐标均为相对图片宽高归一化后的数值，范围为 `0` 到 `1`。如果只训练检测框模型，需要先把 polygon 转成 bbox，或确认训练框架会自动从 polygon 生成 bbox。

## 训练

安装依赖：

```powershell
pip install ultralytics opencv-python pyyaml
```

运行训练：

```powershell
python algorithm/scripts/train.py
```

训练脚本会读取 `algorithm/configs/pcb_defects.yaml`，生成：

```text
algorithm/outputs/pcb_defects.runtime.yaml
```

然后使用该运行时配置启动 YOLOv8 训练。

## 检测

训练完成后使用最佳权重检测单张图片：

```powershell
python algorithm/scripts/detect.py --weights runs/detect/pcb_defects/weights/best.pt --image path\to\test.jpg
```

默认输出：

```text
algorithm/outputs/detections/result.jpg
algorithm/outputs/detections/result.json
```

## 现场采集与增强训练工具

2026-08-14 起，本目录补充了面向龙芯教育派现场数据采集和增强训练的辅助工具。它们用于模型与数据集维护，不替代完整网站、华为云检测服务或现有的 `camera_trigger.py` 运行链路。

- `core/camera_capture_board.py`：从板端相机采集单张或定时 PCB 图片；仅在人工执行时访问相机。
- `core/detect_onnx.py`：使用 ONNX Runtime 进行离线单图诊断，适合开发机交叉验证；龙芯正式运行仍使用现有 OpenCV DNN 路径。
- `configs/pcb_defects_tuned.yaml`：现场样本增强后的独立数据集配置。
- `tools/integrate_field_to_dataset.py`：将已标注的现场图像复制并划分到训练、验证集。
- `tools/build_tuned_dataset.py`：在不修改原始数据集的前提下构建现场样本加权数据集。
- `tools/balance_single_class_augmented.py`：补充类别不平衡时的增强样本。

上述工具可能创建 `algorithm/outputs/`、`datasets/` 或训练产物；这些生成内容保持在 Git 忽略范围内。
