import argparse
from datetime import datetime
from pathlib import Path

import yaml
from ultralytics import YOLO

from evaluate import latest_best_weights
from train import DATA_CONFIG, ROOT, build_runtime_data_config, resolve_dataset_path


def default_test_source() -> Path:
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    dataset_root = resolve_dataset_path(config["path"])
    return (dataset_root / config.get("test", config.get("val", "images/val"))).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run PCB defect detection and save result images.")
    parser.add_argument(
        "--weights",
        default=None,
        help="Path to best.pt. Defaults to the newest runs/detect/pcb_defects*/weights/best.pt.",
    )
    parser.add_argument("--source", default=None, help="Image, directory, or glob to detect. Defaults to test images.")
    parser.add_argument("--conf", type=float, default=0.5, help="Confidence threshold.")
    parser.add_argument("--imgsz", type=int, default=960, help="Inference image size.")
    parser.add_argument("--name", default=None, help="Output run name. Defaults to pcb_defects_predict_<timestamp>.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_runtime_data_config()
    weights = Path(args.weights).resolve() if args.weights else latest_best_weights()
    source = Path(args.source).resolve() if args.source else default_test_source()
    project = (ROOT / "runs" / "detect").resolve()
    run_name = args.name or f"pcb_defects_predict_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    model = YOLO(str(weights))
    model.predict(
        source=str(source),
        conf=args.conf,
        imgsz=args.imgsz,
        project=str(project),
        name=run_name,
        exist_ok=False,
        save=True,
    )


if __name__ == "__main__":
    main()
