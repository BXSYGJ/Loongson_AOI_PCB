# Loongson AOI PCB - Algorithm AI Branch

This branch contains the YOLO-based PCB defect detection workflow used by the
Loongson AOI project. It is responsible for dataset preparation, model
training, evaluation, single-image inference, and standalone camera inference.

The UI integration branch can reuse the trained weights and output format from
this branch.

The current UI branch also uses these weights for phone-upload detection. Phone
photos are converted to grayscale and then evaluated with tiled whole-board
inference so the original phone image is not simply resized down to one YOLO
input.

## Directory Layout

| Path | Purpose |
| --- | --- |
| `algorithm/configs/pcb_defects.yaml` | Dataset and class configuration |
| `algorithm/core/train.py` | Train YOLO on the PCB defect dataset |
| `algorithm/core/evaluate.py` | Evaluate trained weights on train/val/test splits |
| `algorithm/core/predict.py` | Batch prediction for images or folders |
| `algorithm/core/detect.py` | Single-image detection with `result.jpg` and `result.json` output |
| `algorithm/core/camera_detect.py` | Standalone live camera detection |
| `algorithm/core/camera_capture.py` | Capture camera images for field data |
| `algorithm/tools/*.py` | Dataset conversion, augmentation, grayscale conversion helpers |

Expected generated paths:

```text
datasets/pcb_defects/
runs/detect/pcb_defects*/weights/best.pt
algorithm/outputs/detections/result.jpg
algorithm/outputs/detections/result.json
algorithm/outputs/camera/latest.jpg
algorithm/outputs/camera/latest.json
```

Large datasets and trained weights are usually not committed directly. Keep
them in the expected local paths or pass explicit command-line arguments.

## Install Dependencies

```powershell
cd D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai
python -m pip install ultralytics opencv-python pyyaml
```

If this branch is used together with the UI branch, the UI can point to this
directory through:

```powershell
$env:LOONGSON_AOI_ALGORITHM_ROOT = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai"
$env:LOONGSON_AOI_WEIGHTS = "D:\AAAED\code\Loongson_AOI_PCB\worktrees\algorithm-ai\runs\detect\pcb_defects\weights\best.pt"
```

## Dataset Configuration

The default config is:

```text
algorithm/configs/pcb_defects.yaml
```

It expects this dataset layout:

```text
datasets/pcb_defects/
  images/
    train/
    val/
  labels/
    train/
    val/
```

The current class ids are:

| id | Type |
| ---: | --- |
| 0 | `dry_solder_joint` |
| 1 | `improper_installation` |
| 2 | `pcb_damage` |
| 3 | `short_circuit` |
| 4 | `insufficient_solder` |
| 5 | `reverse_component` |
| 6 | `excess_solder` |

## Train

```powershell
python .\algorithm\core\train.py
```

The script reads `algorithm/configs/pcb_defects.yaml`, writes a runtime config
under `algorithm/outputs/`, and creates YOLO runs under `runs/detect/`.

## Evaluate

Use the latest `best.pt` automatically:

```powershell
python .\algorithm\core\evaluate.py --split test --imgsz 960 --batch 8
```

Or pass weights explicitly:

```powershell
python .\algorithm\core\evaluate.py --weights .\runs\detect\pcb_defects\weights\best.pt --split val --imgsz 960
```

## Single-Image Detection

```powershell
python .\algorithm\core\detect.py `
  --weights .\runs\detect\pcb_defects\weights\best.pt `
  --image D:\path\to\pcb.jpg `
  --conf 0.5
```

Default outputs:

```text
algorithm/outputs/detections/result.jpg
algorithm/outputs/detections/result.json
```

The JSON format is compatible with the UI branch:

```json
[
  {
    "type": "short_circuit",
    "label": "short_circuit",
    "bbox": [10, 20, 120, 160],
    "confidence": 0.91
  }
]
```

## Phone-Photo Integration Notes

The training package currently uses square `640x640` PCB defect images. Phone
whole-board photos can be much wider, for example `1600x894`, so direct
whole-image inference may miss defects after resizing. The `ui-integration`
branch compensates by running tiled detection over phone uploads.

Default UI phone-tile settings:

```powershell
$env:LOONGSON_AOI_PHONE_TILE_SIZES = "640,800"
$env:LOONGSON_AOI_PHONE_TILE_OVERLAP = "0.5"
$env:LOONGSON_AOI_YOLO_CONF = "0.5"
$env:LOONGSON_AOI_YOLO_IMGSZ = "960"
```

For future model improvement, add representative phone whole-board photos to
the dataset and label the same defect classes. This will reduce the gap between
training crops and field photos.

## Standalone Camera Detection

For the OV9281 or another camera:

```powershell
python .\algorithm\core\camera_detect.py `
  --weights .\runs\detect\pcb_defects\weights\best.pt `
  --source 1 `
  --conf 0.5 `
  --imgsz 960
```

Notes:

- `--source 0` or `--source 1` selects the local camera index.
- Lower `--conf` when there are many missed defects.
- Raise `--conf` when there are many false positives.
- `--no-window` disables the local OpenCV preview window.
- `--save-video` writes an annotated video into `algorithm/outputs/camera/`.

## Dataset Tools

Convert LabelMe JSON labels:

```powershell
python .\algorithm\tools\labelme_to_yolo.py --json-dir D:\labels_json --output-dir D:\labels_yolo
```

Convert polygon labels to YOLO bbox labels:

```powershell
python .\algorithm\tools\polygon_labels_to_bbox.py
```

Create augmented training samples:

```powershell
python .\algorithm\tools\augment_dataset.py --copies 2 --seed 0
```

Convert images to 3-channel grayscale:

```powershell
python .\algorithm\tools\convert_images_to_grayscale.py --split train
```

## Validation

```powershell
python -m py_compile `
  .\algorithm\core\train.py `
  .\algorithm\core\evaluate.py `
  .\algorithm\core\predict.py `
  .\algorithm\core\detect.py `
  .\algorithm\core\camera_detect.py `
  .\algorithm\core\camera_capture.py `
  .\algorithm\tools\labelme_to_yolo.py `
  .\algorithm\tools\polygon_labels_to_bbox.py `
  .\algorithm\tools\augment_dataset.py `
  .\algorithm\tools\convert_images_to_grayscale.py
```

## Branch Relationship

- `algorithm-ai`: this YOLO training and inference workflow
- `ui-integration`: web UI that reuses the weights and detection JSON format
- `hardware-sm`: TB6600/XYZ slide-table driver
- `main`: kept untouched unless tested branches are merged later
