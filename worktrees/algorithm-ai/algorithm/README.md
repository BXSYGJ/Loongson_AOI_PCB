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
