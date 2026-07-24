import argparse
from pathlib import Path
import sys

import cv2

sys.path.append(str(Path(__file__).resolve().parents[1] / "core"))
from train import ROOT


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def image_files(path: Path) -> list[Path]:
    return sorted(file for file in path.iterdir() if file.suffix.lower() in IMAGE_EXTS)


def convert_image(path: Path) -> bool:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return False

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return bool(cv2.imwrite(str(path), gray_bgr))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert dataset images to grayscale 3-channel images in place.")
    parser.add_argument(
        "--split",
        default="train",
        choices=("train", "val", "test"),
        help="Dataset split under datasets/pcb_defects/images to convert.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    image_dir = ROOT / "datasets" / "pcb_defects" / "images" / args.split
    if not image_dir.exists():
        raise FileNotFoundError(f"Image split not found: {image_dir}")

    converted = 0
    failed = 0
    for path in image_files(image_dir):
        if convert_image(path):
            converted += 1
        else:
            failed += 1

    print(f"Converted {converted} images in {image_dir}. Failed {failed}.")


if __name__ == "__main__":
    main()
