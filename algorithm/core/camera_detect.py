import argparse
import json
import time
from pathlib import Path

import cv2
from ultralytics import YOLO

from detect import draw_detection, load_label_maps
from evaluate import latest_best_weights
from train import ROOT


DEFAULT_OUTPUT_DIR = ROOT / "algorithm" / "outputs" / "camera"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-time PCB defect detection from a camera or video.")
    parser.add_argument(
        "--weights",
        default=None,
        help="Path to best.pt. Defaults to the newest runs/detect/pcb_defects*/weights/best.pt.",
    )
    parser.add_argument("--source", default="0", help="Camera index, video path, or stream URL. Defaults to camera 0.")
    parser.add_argument("--conf", type=float, default=0.5, help="Confidence threshold.")
    parser.add_argument("--imgsz", type=int, default=1024, help="Inference image size.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for latest JSON and snapshots.")
    parser.add_argument("--save-video", action="store_true", help="Save annotated video to output-dir.")
    parser.add_argument("--no-window", action="store_true", help="Do not show the live preview window.")
    return parser.parse_args()


def parse_source(source: str) -> int | str:
    return int(source) if source.isdigit() else source


def main() -> None:
    args = parse_args()
    weights = Path(args.weights).resolve() if args.weights else latest_best_weights()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    names, cn_names = load_label_maps()
    model = YOLO(str(weights))
    cap = cv2.VideoCapture(parse_source(args.source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {args.source}")

    writer = None
    if args.save_video:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        video_path = output_dir / "camera_result.mp4"
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(video_path), fourcc, fps, (width, height))

    latest_json = output_dir / "latest.json"
    snapshot_path = output_dir / "latest.jpg"
    frame_id = 0
    prev_time = time.time()

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            frame_id += 1
            results = model.predict(frame, conf=args.conf, imgsz=args.imgsz, verbose=False)
            detections = []

            for box in results[0].boxes:
                class_id = int(box.cls[0].item())
                confidence = float(box.conf[0].item())
                defect_type = names[class_id]
                label = cn_names.get(defect_type, defect_type)
                bbox = [int(value) for value in box.xyxy[0].tolist()]
                detections.append(
                    {
                        "frame": frame_id,
                        "type": defect_type,
                        "label": label,
                        "bbox": bbox,
                        "confidence": round(confidence, 4),
                    }
                )
                draw_detection(frame, bbox, defect_type, confidence)

            now = time.time()
            fps = 1.0 / max(now - prev_time, 1e-6)
            prev_time = now
            cv2.putText(frame, f"FPS {fps:.1f}", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 180, 0), 2)

            with latest_json.open("w", encoding="utf-8") as file:
                json.dump(detections, file, ensure_ascii=False, indent=2)
            cv2.imwrite(str(snapshot_path), frame)

            if writer is not None:
                writer.write(frame)

            if not args.no_window:
                cv2.imshow("PCB defect detection", frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if not args.no_window:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
