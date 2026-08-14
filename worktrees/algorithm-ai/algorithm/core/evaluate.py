import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

from train import ROOT, build_runtime_data_config
from train import DATA_CONFIG


def latest_best_weights() -> Path:
    weights = sorted(
        (ROOT / "runs" / "detect").glob("pcb_defects*/weights/best.pt"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not weights:
        raise FileNotFoundError("No best.pt found under runs/detect/pcb_defects*/weights/")
    return weights[0].resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate trained YOLO weights on PCB defect data.")
    parser.add_argument(
        "--weights",
        default=None,
        help="Path to best.pt. Defaults to the newest runs/detect/pcb_defects*/weights/best.pt.",
    )
    parser.add_argument("--data", default=str(DATA_CONFIG), help="Dataset YAML config.")
    parser.add_argument("--split", default="test", choices=("train", "val", "test"), help="Dataset split to evaluate.")
    parser.add_argument("--imgsz", type=int, default=960, help="Evaluation image size.")
    parser.add_argument("--batch", type=int, default=8, help="Evaluation batch size.")
    parser.add_argument("--name", default="pcb_defects_test", help="Output run name.")
    parser.add_argument(
        "--plots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Save Ultralytics plots such as val_batch*_pred.jpg.",
    )
    parser.add_argument("--exist-ok", action="store_true", help="Allow reusing an existing output directory.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    runtime_data_config = build_runtime_data_config(args.data).resolve()
    weights = Path(args.weights).resolve() if args.weights else latest_best_weights()
    project = (ROOT / "runs" / "detect").resolve()
    run_name = args.name
    output_dir = project / run_name
    if output_dir.exists():
        shutil.rmtree(output_dir)

    model = YOLO(str(weights))
    metrics = model.val(
        data=str(runtime_data_config),
        split=args.split,
        imgsz=args.imgsz,
        batch=args.batch,
        project=str(project),
        name=run_name,
        plots=args.plots,
        exist_ok=True,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as file:
        json.dump({key: float(value) for key, value in metrics.results_dict.items()}, file, indent=2)
    print(f"Metrics saved to {metrics_path}")


if __name__ == "__main__":
    main()
