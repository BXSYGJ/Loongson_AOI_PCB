import argparse
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.append(str(Path(__file__).resolve().parent))
from labelme_to_yolo import convert_one, load_class_ids


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "datasets" / "pcb_defects"
TARGET = ROOT / "datasets" / "pcb_defects_tuned"
CONFIG = ROOT / "algorithm" / "configs" / "pcb_defects_tuned.yaml"
TMP_FIELD_LABELS = ROOT / "algorithm" / "outputs" / "field_yolo_labels_tuned"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
REVERSE_COMPONENT_ID = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a field-weighted PCB dataset without modifying the original dataset."
    )
    parser.add_argument("--field-val-ratio", type=float, default=0.2, help="Field images reserved for validation.")
    parser.add_argument("--field-copies", type=int, default=2, help="Augmented copies per field training image.")
    parser.add_argument(
        "--reverse-keep-ratio",
        type=float,
        default=0.25,
        help="Keep ratio for non-field training samples containing reverse_component.",
    )
    parser.add_argument("--seed", type=int, default=7, help="Deterministic random seed.")
    parser.add_argument("--overwrite", action="store_true", help="Recreate target dataset if it exists.")
    return parser.parse_args()


def reset_dir(path: Path, overwrite: bool) -> None:
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"{path} exists. Use --overwrite to rebuild it.")
        shutil.rmtree(path)
    for split in ("train", "val"):
        (path / "images" / split).mkdir(parents=True, exist_ok=True)
        (path / "labels" / split).mkdir(parents=True, exist_ok=True)


def image_for_stem(image_dir: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTS:
        path = image_dir / f"{stem}{ext}"
        if path.exists():
            return path
    return None


def read_class_ids(label_path: Path) -> set[int]:
    ids = set()
    for line in label_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts:
            ids.add(int(float(parts[0])))
    return ids


def copy_pair(image_path: Path, label_path: Path, image_out: Path, label_out: Path, stem: str | None = None) -> None:
    target_stem = stem or image_path.stem
    shutil.copy2(image_path, image_out / f"{target_stem}{image_path.suffix.lower()}")
    shutil.copy2(label_path, label_out / f"{target_stem}.txt")


def adjust_brightness_contrast(image: np.ndarray, rng: random.Random) -> np.ndarray:
    alpha = rng.uniform(0.78, 1.28)
    beta = rng.randint(-20, 20)
    return cv2.convertScaleAbs(image, alpha=alpha, beta=beta)


def add_noise(image: np.ndarray, rng: random.Random) -> np.ndarray:
    sigma = rng.uniform(2, 9)
    noise = np.random.normal(0, sigma, image.shape).astype(np.float32)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def sharpen(image: np.ndarray) -> np.ndarray:
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    return cv2.filter2D(image, -1, kernel)


def augment_field_image(image: np.ndarray, variant: int, rng: random.Random) -> np.ndarray:
    augmented = adjust_brightness_contrast(image, rng)
    if variant % 2 == 0:
        augmented = add_noise(augmented, rng)
    else:
        augmented = sharpen(augmented)
    return augmented


def write_augmented_pair(
    image_path: Path,
    label_path: Path,
    image_out: Path,
    label_out: Path,
    copies: int,
    rng: random.Random,
) -> int:
    image = cv2.imread(str(image_path))
    if image is None:
        return 0

    created = 0
    for idx in range(copies):
        stem = f"fieldaug{idx + 1}_{image_path.stem}"
        output_image = image_out / f"{stem}{image_path.suffix.lower()}"
        output_label = label_out / f"{stem}.txt"
        augmented = augment_field_image(image, idx, rng)
        if cv2.imwrite(str(output_image), augmented):
            shutil.copy2(label_path, output_label)
            created += 1
    return created


def deterministic_field_split(index: int, total: int, val_ratio: float) -> str:
    val_count = max(1, round(total * val_ratio))
    return "val" if index < val_count else "train"


def count_split(label_dir: Path) -> tuple[int, Counter]:
    counter = Counter()
    files = 0
    for label_path in label_dir.glob("*.txt"):
        files += 1
        for line in label_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if parts:
                counter[int(float(parts[0]))] += 1
    return files, counter


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    reset_dir(TARGET, args.overwrite)
    TMP_FIELD_LABELS.mkdir(parents=True, exist_ok=True)
    class_ids = load_class_ids()

    target_train_images = TARGET / "images" / "train"
    target_train_labels = TARGET / "labels" / "train"
    target_val_images = TARGET / "images" / "val"
    target_val_labels = TARGET / "labels" / "val"

    kept_original = 0
    pruned_reverse = 0
    for label_path in sorted((SOURCE / "labels" / "train").glob("*.txt")):
        if label_path.stem.startswith("field_") or label_path.stem.startswith("fieldaug"):
            continue
        image_path = image_for_stem(SOURCE / "images" / "train", label_path.stem)
        if image_path is None:
            continue
        class_set = read_class_ids(label_path)
        if REVERSE_COMPONENT_ID in class_set and rng.random() > args.reverse_keep_ratio:
            pruned_reverse += 1
            continue
        copy_pair(image_path, label_path, target_train_images, target_train_labels)
        kept_original += 1

    field_train = 0
    field_val = 0
    field_augmented = 0
    missing_json = 0
    field_images = [
        image_path
        for image_path in sorted((SOURCE / "images" / "field").iterdir())
        if image_path.suffix.lower() in IMAGE_EXTS and image_path.with_suffix(".json").exists()
    ]
    for index, image_path in enumerate(field_images):
        if image_path.suffix.lower() not in IMAGE_EXTS:
            continue
        json_path = image_path.with_suffix(".json")
        if not json_path.exists():
            missing_json += 1
            continue
        convert_one(json_path, TMP_FIELD_LABELS, class_ids)
        label_path = TMP_FIELD_LABELS / f"{image_path.stem}.txt"
        split = deterministic_field_split(index, len(field_images), args.field_val_ratio)
        if split == "val":
            copy_pair(image_path, label_path, target_val_images, target_val_labels)
            field_val += 1
        else:
            copy_pair(image_path, label_path, target_train_images, target_train_labels)
            field_train += 1
            field_augmented += write_augmented_pair(
                image_path,
                label_path,
                target_train_images,
                target_train_labels,
                args.field_copies,
                rng,
            )

    with (ROOT / "algorithm" / "configs" / "pcb_defects.yaml").open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    tuned_config = {
        "path": "../../datasets/pcb_defects_tuned",
        "train": "images/train",
        "val": "images/val",
        "test": "images/val",
        "names": config["names"],
        "cn_names": config.get("cn_names", {}),
    }
    CONFIG.write_text(yaml.safe_dump(tuned_config, allow_unicode=True, sort_keys=False), encoding="utf-8")

    train_files, train_counts = count_split(target_train_labels)
    val_files, val_counts = count_split(target_val_labels)
    print(f"Created {TARGET}")
    print(f"Original non-field kept={kept_original}, reverse-pruned={pruned_reverse}")
    print(f"Field train={field_train}, field augmented={field_augmented}, field val={field_val}, missing_json={missing_json}")
    print(f"Train label files={train_files}, instances={sum(train_counts.values())}, counts={dict(sorted(train_counts.items()))}")
    print(f"Val label files={val_files}, instances={sum(val_counts.values())}, counts={dict(sorted(val_counts.items()))}")
    print(f"Config written to {CONFIG}")


if __name__ == "__main__":
    main()
