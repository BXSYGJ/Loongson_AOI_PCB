import argparse
from datetime import datetime
from pathlib import Path
import time

import cv2

from train import ROOT


DEFAULT_OUTPUT_DIR = ROOT / "datasets" / "pcb_defects" / "images" / "field"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture static PCB images from a camera.")
    parser.add_argument("--source", default="0", help="Camera index or video source. Defaults to camera 0.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory to save captured images.")
    parser.add_argument("--prefix", default="field", help="Filename prefix.")
    parser.add_argument("--count", type=int, default=0, help="Auto-capture this many frames. 0 means manual mode.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between auto-captures.")
    parser.add_argument("--width", type=int, default=1280, help="Requested capture width.")
    parser.add_argument("--height", type=int, default=800, help="Requested capture height.")
    return parser.parse_args()


def parse_source(source: str) -> int | str:
    return int(source) if source.isdigit() else source


def save_frame(frame, output_dir: Path, prefix: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    output_path = output_dir / f"{prefix}_{timestamp}.jpg"
    cv2.imwrite(str(output_path), frame)
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

    saved = 0
    last_saved_path = None
    message_until = 0.0
    try:
        if args.count > 0:
            while saved < args.count:
                ok, frame = cap.read()
                if not ok:
                    raise RuntimeError("Failed to read frame from camera.")
                output_path = save_frame(frame, output_dir, args.prefix)
                saved += 1
                print(f"Saved {output_path}")
                time.sleep(args.interval)
            return

        print("Preview opened. Press SPACE or S to save, Q or ESC to quit.")
        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if time.time() < message_until and last_saved_path is not None:
                cv2.rectangle(frame, (10, 10), (520, 55), (0, 160, 0), -1)
                cv2.putText(
                    frame,
                    f"Saved: {last_saved_path.name}",
                    (20, 42),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                )

            cv2.imshow("camera capture", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord(" "), ord("s"), ord("S")):
                output_path = save_frame(frame, output_dir, args.prefix)
                last_saved_path = output_path
                message_until = time.time() + 1.5
                saved += 1
                print(f"Saved successfully: {output_path}")
            elif key in (ord("q"), ord("Q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"Saved {saved} images to {output_dir}")


if __name__ == "__main__":
    main()
