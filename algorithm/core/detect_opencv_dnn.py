import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import yaml


ROOT = Path(__file__).resolve().parents[2]
DATA_CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
DEFAULT_MODEL = ROOT / "runs" / "detect" / "pcb_defects_20260706_033031" / "weights" / "best.onnx"
DEFAULT_OUTPUT_DIR = ROOT / "algorithm" / "outputs" / "detections_dnn"


def load_label_maps():
    with DATA_CONFIG.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    names = {int(class_id): name for class_id, name in config["names"].items()}
    return names, config.get("cn_names", {})


def parse_args():
    parser = argparse.ArgumentParser(description="Detect PCB defects with OpenCV DNN and ONNX.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to best.onnx.")
    parser.add_argument("--image", required=True, help="Path to input image.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for result image and JSON.")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold.")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Model input size.")
    return parser.parse_args()


def letterbox(image, size):
    h, w = image.shape[:2]
    scale = min(float(size) / w, float(size) / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    left, top = int(round((size - nw) / 2 - 0.1)), int(round((size - nh) / 2 - 0.1))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[top : top + nh, left : left + nw] = resized
    return canvas, scale, left, top


def postprocess(output, image_shape, scale, pad_x, pad_y, conf_thres, iou_thres):
    pred = np.squeeze(output)
    if pred.shape[0] < pred.shape[1]:
        pred = pred.T

    boxes_xywh = pred[:, :4]
    scores = pred[:, 4:]
    class_ids = np.argmax(scores, axis=1)
    confidences = scores[np.arange(scores.shape[0]), class_ids]
    keep = confidences >= conf_thres
    if not np.any(keep):
        return []

    boxes_xywh = boxes_xywh[keep]
    confidences = confidences[keep]
    class_ids = class_ids[keep]

    nms_boxes = []
    h, w = image_shape
    for cx, cy, bw, bh in boxes_xywh:
        x1 = (cx - bw / 2 - pad_x) / scale
        y1 = (cy - bh / 2 - pad_y) / scale
        x2 = (cx + bw / 2 - pad_x) / scale
        y2 = (cy + bh / 2 - pad_y) / scale
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w - 1, int(x2)), min(h - 1, int(y2))
        nms_boxes.append([x1, y1, max(0, x2 - x1), max(0, y2 - y1)])

    indices = cv2.dnn.NMSBoxes(nms_boxes, confidences.tolist(), conf_thres, iou_thres)
    detections = []
    for index in np.array(indices).reshape(-1):
        x, y, bw, bh = nms_boxes[index]
        detections.append(
            {
                "class_id": int(class_ids[index]),
                "bbox": [x, y, x + bw, y + bh],
                "confidence": float(confidences[index]),
            }
        )
    return detections


def draw_detection(image, bbox, label, confidence):
    x1, y1, x2, y2 = bbox
    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
    cv2.putText(
        image,
        "{} {:.2f}".format(label, confidence),
        (x1, max(y1 - 8, 16)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (0, 180, 0),
        2,
    )


def main():
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image = cv2.imread(args.image)
    if image is None:
        raise FileNotFoundError("Could not read image: {}".format(args.image))

    padded, scale, pad_x, pad_y = letterbox(image, args.imgsz)
    blob = cv2.dnn.blobFromImage(padded, 1.0 / 255.0, (args.imgsz, args.imgsz), swapRB=True, crop=False)

    net = cv2.dnn.readNetFromONNX(args.model)
    net.setInput(blob)
    output = net.forward()

    names, cn_names = load_label_maps()
    raw_detections = postprocess(output, image.shape[:2], scale, pad_x, pad_y, args.conf, args.iou)

    detections = []
    for raw in raw_detections:
        defect_type = names[int(raw["class_id"])]
        label = cn_names.get(defect_type, defect_type)
        bbox = raw["bbox"]
        confidence = float(raw["confidence"])
        detections.append({"type": defect_type, "label": label, "bbox": bbox, "confidence": round(confidence, 4)})
        draw_detection(image, bbox, defect_type, confidence)

    cv2.imwrite(str(output_dir / "result.jpg"), image)
    with (output_dir / "result.json").open("w", encoding="utf-8") as file:
        json.dump(detections, file, ensure_ascii=False, indent=2)
    print(json.dumps(detections, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
