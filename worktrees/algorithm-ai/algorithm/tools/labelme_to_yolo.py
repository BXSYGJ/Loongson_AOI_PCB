import argparse
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Labelme rectangle JSON files to YOLO txt labels.")
    parser.add_argument("--json-dir", required=True, help="Directory containing Labelme JSON files.")
    parser.add_argument("--output-dir", required=True, help="Directory for generated YOLO txt labels.")
    return parser.parse_args()


def load_class_ids() -> dict[str, int]:
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return {name: int(class_id) for class_id, name in config["names"].items()}


def rectangle_to_yolo(points: list[list[float]], image_width: int, image_height: int) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    x_center = ((x_min + x_max) / 2) / image_width
    y_center = ((y_min + y_max) / 2) / image_height
    width = (x_max - x_min) / image_width
    height = (y_max - y_min) / image_height
    return x_center, y_center, width, height


def convert_one(json_path: Path, output_dir: Path, class_ids: dict[str, int]) -> None:
    with json_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    image_width = int(data["imageWidth"])
    image_height = int(data["imageHeight"])
    lines = []

    for shape in data.get("shapes", []):
        label = shape["label"]
        if label not in class_ids:
            allowed = ", ".join(class_ids)
            raise ValueError(f"{json_path.name}: unknown label '{label}'. Allowed labels: {allowed}")

        points = shape["points"]
        x_center, y_center, width, height = rectangle_to_yolo(points, image_width, image_height)
        lines.append(f"{class_ids[label]} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}")

    output_path = output_dir / f"{json_path.stem}.txt"
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def main() -> None:
    args = parse_args()
    json_dir = Path(args.json_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    class_ids = load_class_ids()
    json_paths = sorted(json_dir.glob("*.json"))
    if not json_paths:
        raise FileNotFoundError(f"No Labelme JSON files found in {json_dir}")

    for json_path in json_paths:
        convert_one(json_path, output_dir, class_ids)

    print(f"Converted {len(json_paths)} Labelme files to {output_dir}")


if __name__ == "__main__":
    main()
