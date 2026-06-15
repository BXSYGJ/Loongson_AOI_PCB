import argparse
import json
from pathlib import Path

import cv2
import yaml
from ultralytics import YOLO


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
DEFAULT_OUTPUT_DIR = ROOT / "algorithm" / "outputs" / "detections"


def load_label_maps() -> tuple[dict[int, str], dict[str, str]]:
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    names = {int(class_id): name for class_id, name in config["names"].items()}
    cn_names = config.get("cn_names", {})
    return names, cn_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect PCB soldering defects in one image.")
    parser.add_argument("--weights", required=True, help="Path to trained YOLO weights, for example best.pt.")
    parser.add_argument("--image", required=True, help="Path to input PCB image.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for result image and JSON.")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    return parser.parse_args()


def draw_detection(image, bbox: list[int], label: str, confidence: float) -> None:
    x1, y1, x2, y2 = bbox
    text = f"{label} {confidence:.2f}"
    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
    cv2.putText(image, text, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 0), 2)


def main() -> None:
    args = parse_args()
    image_path = Path(args.image)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    names, cn_names = load_label_maps()
    model = YOLO(args.weights)
    results = model.predict(source=str(image_path), conf=args.conf, verbose=False)

    detections = []
    for box in results[0].boxes:
        class_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        defect_type = names[class_id]
        label = cn_names.get(defect_type, defect_type)
        bbox = [int(value) for value in box.xyxy[0].tolist()]

        detections.append(
            {
                "type": defect_type,
                "label": label,
                "bbox": bbox,
                "confidence": round(confidence, 4),
            }
        )
        draw_detection(image, bbox, defect_type, confidence)

    cv2.imwrite(str(output_dir / "result.jpg"), image)
    with (output_dir / "result.json").open("w", encoding="utf-8") as file:
        json.dump(detections, file, ensure_ascii=False, indent=2)

    print(json.dumps(detections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
