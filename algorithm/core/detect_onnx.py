import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import yaml


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
DEFAULT_MODEL = ROOT / "runs" / "detect" / "pcb_defects_20260706_033031" / "weights" / "best.onnx"
DEFAULT_OUTPUT_DIR = ROOT / "algorithm" / "outputs" / "detections_onnx"


def load_label_maps() -> tuple[dict[int, str], dict[str, str]]:
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    names = {int(class_id): name for class_id, name in config["names"].items()}
    cn_names = config.get("cn_names", {})
    return names, cn_names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detect PCB soldering defects with an exported ONNX model.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to exported best.onnx.")
    parser.add_argument("--image", required=True, help="Path to input PCB image.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for result image and JSON.")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold.")
    return parser.parse_args()


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    height, width = image.shape[:2]
    scale = min(size / width, size / height)
    resized_width = int(round(width * scale))
    resized_height = int(round(height * scale))
    pad_x = (size - resized_width) / 2
    pad_y = (size - resized_height) / 2

    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    left = int(round(pad_x - 0.1))
    top = int(round(pad_y - 0.1))
    canvas[top : top + resized_height, left : left + resized_width] = resized
    return canvas, scale, (left, top)


def preprocess(image: np.ndarray, input_size: int) -> tuple[np.ndarray, float, tuple[float, float]]:
    padded, scale, pad = letterbox(image, input_size)
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    tensor = rgb.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))[None]
    return tensor, scale, pad


def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    xyxy = np.empty_like(boxes)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return xyxy


def postprocess(
    output: np.ndarray,
    image_shape: tuple[int, int],
    scale: float,
    pad: tuple[float, float],
    conf_threshold: float,
    iou_threshold: float,
) -> list[dict[str, object]]:
    predictions = np.squeeze(output)
    if predictions.ndim != 2:
        raise ValueError(f"Unexpected ONNX output shape after squeeze: {predictions.shape}")
    if predictions.shape[0] < predictions.shape[1]:
        predictions = predictions.T

    boxes = predictions[:, :4]
    class_scores = predictions[:, 4:]
    class_ids = np.argmax(class_scores, axis=1)
    confidences = class_scores[np.arange(class_scores.shape[0]), class_ids]
    keep = confidences >= conf_threshold
    if not np.any(keep):
        return []

    boxes = xywh_to_xyxy(boxes[keep])
    confidences = confidences[keep]
    class_ids = class_ids[keep]

    pad_x, pad_y = pad
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / scale

    height, width = image_shape
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width - 1)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height - 1)

    nms_boxes = []
    for x1, y1, x2, y2 in boxes:
        nms_boxes.append([int(x1), int(y1), int(max(0, x2 - x1)), int(max(0, y2 - y1))])

    indices = cv2.dnn.NMSBoxes(nms_boxes, confidences.tolist(), conf_threshold, iou_threshold)
    if len(indices) == 0:
        return []

    detections = []
    for index in np.array(indices).reshape(-1):
        x, y, w, h = nms_boxes[index]
        detections.append(
            {
                "class_id": int(class_ids[index]),
                "bbox": [x, y, x + w, y + h],
                "confidence": float(confidences[index]),
            }
        )
    return detections


def draw_detection(image: np.ndarray, bbox: list[int], label: str, confidence: float) -> None:
    x1, y1, x2, y2 = bbox
    text = f"{label} {confidence:.2f}"
    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
    cv2.putText(image, text, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 0), 2)


def main() -> None:
    args = parse_args()
    model_path = Path(args.model)
    image_path = Path(args.image)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    input_info = session.get_inputs()[0]
    input_size = int(input_info.shape[2])
    tensor, scale, pad = preprocess(image, input_size)
    output = session.run(None, {input_info.name: tensor})[0]

    names, cn_names = load_label_maps()
    raw_detections = postprocess(output, image.shape[:2], scale, pad, args.conf, args.iou)
    detections = []
    for raw in raw_detections:
        class_id = int(raw["class_id"])
        defect_type = names[class_id]
        label = cn_names.get(defect_type, defect_type)
        bbox = raw["bbox"]
        confidence = float(raw["confidence"])
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
