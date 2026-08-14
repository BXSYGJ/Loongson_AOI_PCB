import argparse
import shutil
from pathlib import Path

from labelme_to_yolo import convert_one, load_class_ids


ROOT = Path(__file__).resolve().parents[2]
FIELD_DIR = ROOT / "datasets" / "pcb_defects" / "images" / "field"
TRAIN_IMAGE_DIR = ROOT / "datasets" / "pcb_defects" / "images" / "train"
VAL_IMAGE_DIR = ROOT / "datasets" / "pcb_defects" / "images" / "val"
TRAIN_LABEL_DIR = ROOT / "datasets" / "pcb_defects" / "labels" / "train"
VAL_LABEL_DIR = ROOT / "datasets" / "pcb_defects" / "labels" / "val"
TMP_LABEL_DIR = ROOT / "algorithm" / "outputs" / "field_yolo_labels"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert field Labelme annotations and merge them into train/val.")
    parser.add_argument("--val-ratio", type=float, default=0.2, help="Fraction of field samples copied into val.")
    parser.add_argument("--seed-mod", type=int, default=10, help="Deterministic split modulus.")
    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="Keep source field images/json files unchanged. Default behavior is also copy, not move.",
    )
    return parser.parse_args()


def deterministic_bucket(stem: str, seed_mod: int) -> int:
    return sum(ord(ch) for ch in stem) % seed_mod


def pick_split(stem: str, val_ratio: float, seed_mod: int) -> str:
    val_cutoff = max(1, round(seed_mod * val_ratio))
    return "val" if deterministic_bucket(stem, seed_mod) < val_cutoff else "train"


def main() -> None:
    args = parse_args()
    class_ids = load_class_ids()

    TRAIN_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    VAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TRAIN_LABEL_DIR.mkdir(parents=True, exist_ok=True)
    VAL_LABEL_DIR.mkdir(parents=True, exist_ok=True)
    TMP_LABEL_DIR.mkdir(parents=True, exist_ok=True)

    images = sorted([path for path in FIELD_DIR.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTS])
    if not images:
        raise FileNotFoundError(f"No field images found in {FIELD_DIR}")

    copied_train = 0
    copied_val = 0
    missing_json = []

    for image_path in images:
        json_path = FIELD_DIR / f"{image_path.stem}.json"
        if not json_path.exists():
            missing_json.append(image_path.name)
            continue

        convert_one(json_path, TMP_LABEL_DIR, class_ids)
        label_path = TMP_LABEL_DIR / f"{image_path.stem}.txt"
        split = pick_split(image_path.stem, args.val_ratio, args.seed_mod)

        if split == "val":
            target_image = VAL_IMAGE_DIR / image_path.name
            target_label = VAL_LABEL_DIR / label_path.name
            copied_val += 1
        else:
            target_image = TRAIN_IMAGE_DIR / image_path.name
            target_label = TRAIN_LABEL_DIR / label_path.name
            copied_train += 1

        shutil.copy2(image_path, target_image)
        shutil.copy2(label_path, target_label)

    print(f"Integrated train={copied_train} val={copied_val} from {FIELD_DIR}")
    print(f"Skipped missing json={len(missing_json)}")
    if missing_json:
        for name in missing_json:
            print(name)


if __name__ == "__main__":
    main()
