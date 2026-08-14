import argparse
import json
import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = ROOT / "runs" / "detect" / "pcb_defects_20260706_033031" / "weights" / "best.onnx"
DEFAULT_OUTPUT_DIR = ROOT / "algorithm" / "outputs" / "web_detections"

CLASS_NAMES = {
    0: "dry_solder_joint",
    1: "improper_installation",
    2: "pcb_damage",
    3: "short_circuit",
    4: "insufficient_solder",
    5: "reverse_component",
    6: "excess_solder",
}


def letterbox(image, size):
    h, w = image.shape[:2]
    scale = min(float(size) / w, float(size) / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    left = int(round((size - nw) / 2 - 0.1))
    top = int(round((size - nh) / 2 - 0.1))
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

    h, w = image_shape
    nms_boxes = []
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
        class_id = int(class_ids[index])
        detections.append(
            {
                "type": CLASS_NAMES[class_id],
                "class_id": class_id,
                "bbox": [x, y, x + bw, y + bh],
                "confidence": round(float(confidences[index]), 4),
            }
        )
    return detections


def draw_detection(image, detection):
    x1, y1, x2, y2 = detection["bbox"]
    label = "{} {:.2f}".format(detection["type"], detection["confidence"])
    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
    cv2.putText(image, label, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 0), 2)


class Detector:
    def __init__(self, model_path, imgsz=1024):
        self.model_path = str(model_path)
        self.imgsz = imgsz
        self.net = cv2.dnn.readNetFromONNX(self.model_path)

    def detect(self, image_path, output_dir, conf=0.25, iou=0.45):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError("Could not read image: {}".format(image_path))

        padded, scale, pad_x, pad_y = letterbox(image, self.imgsz)
        blob = cv2.dnn.blobFromImage(padded, 1.0 / 255.0, (self.imgsz, self.imgsz), swapRB=True, crop=False)
        self.net.setInput(blob)
        output = self.net.forward()

        detections = postprocess(output, image.shape[:2], scale, pad_x, pad_y, conf, iou)
        for detection in detections:
            draw_detection(image, detection)

        result_image = output_dir / "result.jpg"
        result_json = output_dir / "result.json"
        cv2.imwrite(str(result_image), image)
        with result_json.open("w", encoding="utf-8") as file:
            json.dump(detections, file, ensure_ascii=False, indent=2)

        return {
            "detections": detections,
            "result_image": str(result_image),
            "result_json": str(result_json),
        }


def create_app(model_path=DEFAULT_MODEL, output_dir=DEFAULT_OUTPUT_DIR, imgsz=1024):
    app = FastAPI(title="PCB Defect Detection API")
    detector = Detector(model_path, imgsz=imgsz)
    output_dir = Path(output_dir)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": str(model_path)}

    @app.post("/detect")
    async def detect(file: UploadFile = File(...), conf: float = 0.25, iou: float = 0.45):
        request_id = uuid.uuid4().hex
        request_dir = output_dir / request_id
        request_dir.mkdir(parents=True, exist_ok=True)

        suffix = Path(file.filename or "image.jpg").suffix or ".jpg"
        image_path = request_dir / ("input" + suffix)
        with image_path.open("wb") as out_file:
            shutil.copyfileobj(file.file, out_file)

        try:
            result = detector.detect(image_path, request_dir, conf=conf, iou=iou)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

        return {
            "request_id": request_id,
            "input_image": str(image_path),
            **result,
        }

    return app


def parse_args():
    parser = argparse.ArgumentParser(description="Board-side PCB defect detection HTTP API.")
    parser.add_argument("--host", default="0.0.0.0", help="Server host.")
    parser.add_argument("--port", type=int, default=8000, help="Server port.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to best.onnx.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for uploaded images and results.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Model input size.")
    return parser.parse_args()


def main():
    args = parse_args()
    import uvicorn

    app = create_app(model_path=Path(args.model), output_dir=Path(args.output_dir), imgsz=args.imgsz)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
