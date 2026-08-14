import argparse
from pathlib import Path
from datetime import datetime

import yaml
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
RUNTIME_DATA_CONFIG = ROOT / "algorithm" / "outputs" / "pcb_defects.runtime.yaml"


def resolve_dataset_path(config_path: str | Path, base_dir: Path | None = None) -> Path:
    path = Path(config_path)
    if path.is_absolute():
        return path
    return ((base_dir or DATA_CONFIG.parent) / path).resolve()


def has_images(path: Path) -> bool:
    image_exts = ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.tif", "*.tiff", "*.webp")
    return any(path.glob(pattern) for pattern in image_exts)


def build_runtime_data_config(data_config: str | Path = DATA_CONFIG) -> Path:
    data_config = Path(data_config)
    runtime_data_config = ROOT / "algorithm" / "outputs" / f"{data_config.stem}.runtime.yaml"
    with data_config.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    dataset_root = resolve_dataset_path(config["path"], data_config.parent)
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

    runtime_data_config.parent.mkdir(parents=True, exist_ok=True)
    with runtime_data_config.open("w", encoding="utf-8") as file:
        yaml.safe_dump(runtime_config, file, allow_unicode=True, sort_keys=False)

    if not has_val_images:
        print("Warning: no validation images found, using images/train as val for this small smoke test.")

    return runtime_data_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train YOLO PCB defect model.")
    parser.add_argument("--data", default=str(DATA_CONFIG), help="Dataset YAML config.")
    parser.add_argument("--model", default=str(ROOT / "yolov8n.pt"), help="Initial model or checkpoint.")
    parser.add_argument("--epochs", type=int, default=100, help="Training epochs.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Image size.")
    parser.add_argument("--batch", type=int, default=8, help="Batch size.")
    parser.add_argument("--name", default=None, help="Run name. Defaults to timestamped pcb_defects.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime_data_config = build_runtime_data_config(args.data)
    model = YOLO(str(Path(args.model).resolve()))
    run_name = args.name or f"pcb_defects_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    model.train(
        data=str(runtime_data_config),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        project=str(ROOT / "runs" / "detect"),
        name=run_name,
        exist_ok=False,
    )


if __name__ == "__main__":
    main()
