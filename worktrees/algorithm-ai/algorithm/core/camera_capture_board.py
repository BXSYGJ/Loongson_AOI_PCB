import argparse
from datetime import datetime
from pathlib import Path
import time

import cv2


DEFAULT_OUTPUT_DIR = Path("captured_images")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture PCB images from a board-side camera.")
    parser.add_argument("--source", default="0", help="Camera index, video path, or stream URL. Defaults to camera 0.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory to save captured images.")
    parser.add_argument("--prefix", default="pcb", help="Filename prefix.")
    parser.add_argument("--count", type=int, default=0, help="Number of frames to capture. 0 means manual mode.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between captures.")
    parser.add_argument("--warmup", type=int, default=5, help="Frames to discard before saving.")
    parser.add_argument("--width", type=int, default=1280, help="Requested capture width.")
    parser.add_argument("--height", type=int, default=800, help="Requested capture height.")
    parser.add_argument("--show", action="store_true", help="Show preview window. Use only when GUI is available.")
    return parser.parse_args()


def parse_source(source: str):
    return int(source) if source.isdigit() else source


def save_frame(frame, output_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    output_path = output_dir / f"{prefix}_{timestamp}.jpg"
    if not cv2.imwrite(str(output_path), frame):
        raise RuntimeError(f"Failed to save image: {output_path}")
    return output_path


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(parse_source(args.source))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera source: {args.source}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Requested resolution: {args.width}x{args.height}")
    print(f"Actual resolution: {actual_width}x{actual_height}")

    try:
        for _ in range(max(args.warmup, 0)):
            cap.read()

        saved = 0
        if args.count == 0:
            print("Manual mode. Press SPACE or S to save, Q or ESC to quit.")
            while True:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Failed to read frame from camera.")

                cv2.imshow("camera capture", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord(" "), ord("s"), ord("S")):
                    output_path = save_frame(frame, output_dir, args.prefix)
                    saved += 1
                    print(f"Saved {output_path}")
                elif key in (ord("q"), ord("Q"), 27):
                    break
            print(f"Saved {saved} images to {output_dir}")
            return

        while saved < args.count:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Failed to read frame from camera.")

            if args.show:
                cv2.imshow("camera capture", frame)
                cv2.waitKey(1)

            output_path = save_frame(frame, output_dir, args.prefix)
            saved += 1
            print(f"Saved {output_path}")

            if saved < args.count:
                time.sleep(args.interval)
    finally:
        cap.release()
        if args.show:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
