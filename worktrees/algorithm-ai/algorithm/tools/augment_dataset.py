import argparse
import random
import shutil
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.append(str(Path(__file__).resolve().parents[1] / "core"))
from train import ROOT


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
DATASET_ROOT = ROOT / "datasets" / "pcb_defects"
TRAIN_IMAGES = DATASET_ROOT / "images" / "train"
TRAIN_LABELS = DATASET_ROOT / "labels" / "train"


def adjust_brightness_contrast(image: np.ndarray) -> np.ndarray:
    alpha = random.uniform(0.75, 1.35)
    beta = random.randint(-25, 25)
    return cv2.convertScaleAbs(image, alpha=alpha, beta=beta)


def add_noise(image: np.ndarray) -> np.ndarray:
    sigma = random.uniform(3, 12)
    noise = np.random.normal(0, sigma, image.shape).astype(np.float32)
    noisy = image.astype(np.float32) + noise
    return np.clip(noisy, 0, 255).astype(np.uint8)


def slight_blur(image: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(image, (3, 3), 0)


def grayscale_bgr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def sharpen(image: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    return cv2.filter2D(image, -1, kernel)


def augment_image(image: np.ndarray, variant: int) -> np.ndarray:
    transforms = [
        adjust_brightness_contrast,
        add_noise,
        slight_blur,
        grayscale_bgr,
        sharpen,
    ]
    random.shuffle(transforms)
    augmented = image.copy()
    for transform in transforms[: 2 + (variant % 2)]:
        augmented = transform(augmented)
    return augmented


def image_files(path: Path) -> list[Path]:
    return sorted(file for file in path.iterdir() if file.suffix.lower() in IMAGE_EXTS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Augment PCB training images without changing YOLO bbox labels.")
    parser.add_argument("--copies", type=int, default=2, help="Augmented copies to create per source image.")
    parser.add_argument("--seed", type=int, default=0, help="Random seed.")
    parser.add_argument("--prefix", default="aug", help="Prefix for generated files.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing augmented files with same names.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    np.random.seed(args.seed)

    if not TRAIN_IMAGES.exists() or not TRAIN_LABELS.exists():
        raise FileNotFoundError("Expected datasets/pcb_defects/images/train and labels/train to exist.")

    created = 0
    skipped = 0
    for image_path in image_files(TRAIN_IMAGES):
        label_path = TRAIN_LABELS / f"{image_path.stem}.txt"
        if not label_path.exists():
            skipped += 1
            continue

        image = cv2.imread(str(image_path))
        if image is None:
            skipped += 1
            continue

        for idx in range(args.copies):
            output_stem = f"{args.prefix}_{idx + 1}_{image_path.stem}"
            output_image = TRAIN_IMAGES / f"{output_stem}{image_path.suffix.lower()}"
            output_label = TRAIN_LABELS / f"{output_stem}.txt"
            if not args.overwrite and (output_image.exists() or output_label.exists()):
                skipped += 1
                continue

            augmented = augment_image(image, idx)
            cv2.imwrite(str(output_image), augmented)
            shutil.copy2(label_path, output_label)
            created += 1

    print(f"Created {created} augmented image/label pairs. Skipped {skipped}.")


if __name__ == "__main__":
    main()
