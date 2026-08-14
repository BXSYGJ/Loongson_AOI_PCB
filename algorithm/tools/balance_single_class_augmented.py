import argparse
from pathlib import Path


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove augmented single-class training samples for selected YOLO class IDs."
    )
    parser.add_argument("--image-dir", default="datasets/pcb_defects/images/train", help="Training image directory.")
    parser.add_argument("--label-dir", default="datasets/pcb_defects/labels/train", help="Training label directory.")
    parser.add_argument(
        "--class-ids",
        nargs="+",
        type=int,
        required=True,
        help="Class IDs to prune, for example: --class-ids 1 5",
    )
    parser.add_argument(
        "--prefix",
        default="aug_1_",
        help="Only files whose stem starts with this prefix will be removed.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files without deleting them.")
    return parser.parse_args()


def find_image(image_dir: Path, stem: str) -> Path | None:
    for ext in IMAGE_EXTS:
        path = image_dir / f"{stem}{ext}"
        if path.exists():
            return path
    return None


def single_class_id(label_path: Path) -> int | None:
    lines = [line.strip() for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    class_ids = {int(line.split()[0]) for line in lines if len(line.split()) >= 5}
    if len(class_ids) != 1:
        return None
    return next(iter(class_ids))


def main() -> None:
    args = parse_args()
    image_dir = Path(args.image_dir).resolve()
    label_dir = Path(args.label_dir).resolve()
    selected = set(args.class_ids)

    removed = 0
    for label_path in sorted(label_dir.glob("*.txt")):
        stem = label_path.stem
        if not stem.startswith(args.prefix):
            continue

        class_id = single_class_id(label_path)
        if class_id is None or class_id not in selected:
            continue

        image_path = find_image(image_dir, stem)
        if image_path is None:
            continue

        if args.dry_run:
            print(f"would remove class {class_id}: {image_path.name} / {label_path.name}")
        else:
            image_path.unlink()
            label_path.unlink()
            print(f"removed class {class_id}: {image_path.name} / {label_path.name}")
        removed += 1

    print(f"Matched {removed} augmented single-class samples.")


if __name__ == "__main__":
    main()
