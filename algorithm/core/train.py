from pathlib import Path
from datetime import datetime

import yaml
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
RUNTIME_DATA_CONFIG = ROOT / "algorithm" / "outputs" / "pcb_defects.runtime.yaml"


def resolve_dataset_path(config_path: str | Path) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    return (DATA_CONFIG.parent / path).resolve()


def has_images(path: Path) -> bool:
    image_exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.tif", "*.tiff", "*.webp")
    return any(path.glob(pattern) for pattern in image_exts)


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
    model = YOLO(str(ROOT / "yolov8n.pt"))
    run_name = f"pcb_defects_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model.train(
        data=str(runtime_data_config),
        epochs=100,
        imgsz=960,
        batch=8,
        project=str(ROOT / "runs" / "detect"),
        name=run_name,
        exist_ok=False,
    )


if __name__ == "__main__":
    main()
