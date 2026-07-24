from datetime import datetime
from pathlib import Path
import time
import cv2

from motor_controller import move_to_xy


CAMERA_SOURCE = 0
IMAGE_WIDTH = 1280
IMAGE_HEIGHT = 800
OUTPUT_DIR = "captured_images"
STABLE_DELAY = 1.0
WARMUP_FRAMES = 5


SCAN_POINTS = [
    (0, 0),
    (0, 24),
    (0, 48),
    (0, 72),
    (16, 0),
    (16, 24),
    (16, 48),
    (16, 72),
    (32, 0),
    (32, 24),
    (32, 48),
    (32, 72),
    (48, 0),
    (48, 24),
    (48, 48),
    (48,72)
]


def open_camera():
    cap = cv2.VideoCapture(CAMERA_SOURCE)
    if not cap.isOpened():
        raise RuntimeError("Could not open camera source {}".format(CAMERA_SOURCE))

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, IMAGE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, IMAGE_HEIGHT)

    actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print("Camera resolution: {}x{}".format(actual_width, actual_height))

    return cap


def capture_once(cap, output_dir, prefix):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(WARMUP_FRAMES):
        cap.read()

    ok, frame = cap.read()
    if not ok:
        raise RuntimeError("Failed to read frame from camera.")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    image_path = output_dir / "{}_{}.jpg".format(prefix, timestamp)

    if not cv2.imwrite(str(image_path), frame):
        raise RuntimeError("Failed to save image: {}".format(image_path))

    print("Saved:", image_path)
    return image_path


def main():
    cap = open_camera()
    saved_images = []

    try:
        for index, (x, y) in enumerate(SCAN_POINTS):
            print("Step {} / {}: move to x={}, y={}".format(index + 1, len(SCAN_POINTS), x, y))

            move_to_xy(x, y)

            print("Waiting for stage to stabilize...")
            time.sleep(STABLE_DELAY)

            image_path = capture_once(
                cap,
                OUTPUT_DIR,
                "step{:02d}_x{}_y{}".format(index, x, y),
            )
            saved_images.append(str(image_path))

        print("Scan finished. Total images:", len(saved_images))
        for path in saved_images:
            print(path)

    finally:
        move_to_xy(0,0)
        cap.release()



if __name__ == "__main__":
    main()
