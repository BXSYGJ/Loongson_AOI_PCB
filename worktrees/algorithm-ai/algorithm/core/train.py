import os
from pathlib import Path
from datetime import datetime

import yaml

os.environ.setdefault("YOLO_OFFLINE", "true")

from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
RUNTIME_DATA_CONFIG = ROOT / "algorithm" / "outputs" / "pcb_defects.runtime.yaml"
PRETRAINED_MODEL = ROOT / "yolov8n.pt"
IMAGE_PATTERNS = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.tif", "*.tiff", "*.webp")


def resolve_dataset_path(config_path: str | Path) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    return (DATA_CONFIG.parent / path).resolve()


def has_images(path: Path) -> bool:
    return any(path.glob(pattern) for pattern in IMAGE_PATTERNS)


def count_images(path: Path) -> int:
    return sum(1 for pattern in IMAGE_PATTERNS for _ in path.glob(pattern))


def validate_training_inputs(model_path: Path, data_config_path: Path) -> dict[str, int]:
    if not model_path.is_file():
        raise FileNotFoundError(
            f"Local pretrained model not found: {model_path}. "
            "Place yolov8n.pt at this path before training; automatic downloads are disabled."
        )
    if not data_config_path.is_file():
        raise FileNotFoundError(f"Dataset config not found: {data_config_path}")

    with data_config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    dataset_root = Path(config["path"])
    if not dataset_root.is_absolute():
        dataset_root = (data_config_path.parent / dataset_root).resolve()

    train_images = dataset_root / config.get("train", "images/train")
    val_images = dataset_root / config.get("val", "images/val")
    train_count = count_images(train_images)
    val_count = count_images(val_images)
    if train_count == 0:
        raise FileNotFoundError(f"No training images found in: {train_images}")
    if val_count == 0:
        raise FileNotFoundError(f"No validation images found in: {val_images}")

    return {"train_images": train_count, "val_images": val_count}


def build_runtime_data_config() -> Path:
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    dataset_root = resolve_dataset_path(config["path"])
    train_split = config.get("train", "images/train")
    val_split = config.get("val", "images/val")
    test_split = config.get("test", val_split)
    train_images = dataset_root / train_split
    val_images = dataset_root / val_split
    test_images = dataset_root / test_split
    has_val_images = has_images(val_images)

    if not has_images(train_images):
        raise FileNotFoundError(f"No training images found in: {train_images}")

    runtime_config = {
        "path": str(dataset_root),
        "train": train_split,
        "val": val_split if has_val_images else train_split,
        "test": test_split if has_images(test_images) else val_split,
        "names": config["names"],
    }

    RUNTIME_DATA_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with RUNTIME_DATA_CONFIG.open("w", encoding="utf-8") as file:
        yaml.safe_dump(runtime_config, file, allow_unicode=True, sort_keys=False)

    if not has_val_images:
        print("Warning: no validation images found, using images/train as val for this small smoke test.")

    return RUNTIME_DATA_CONFIG


def main() -> None:
    runtime_data_config = build_runtime_data_config()
    image_counts = validate_training_inputs(PRETRAINED_MODEL, runtime_data_config)
    print(
        f"Training inputs ready: model={PRETRAINED_MODEL}, "
        f"train_images={image_counts['train_images']}, "
        f"val_images={image_counts['val_images']}"
    )
    model = YOLO(str(PRETRAINED_MODEL))
    run_name = f"pcb_defects_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model.train(
        data=str(runtime_data_config),
        epochs=100,
        imgsz=960,
        batch=-1,
        device=0,
        workers=0,
        cache=False,
        mosaic=1.0,
        amp=False,
        project=str(ROOT / "runs" / "detect"),
        name=run_name,
        exist_ok=False,
    )


if __name__ == "__main__":
    main()
