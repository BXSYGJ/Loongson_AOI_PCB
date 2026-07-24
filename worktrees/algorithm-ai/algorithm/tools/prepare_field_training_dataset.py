import argparse
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import yaml

from labelme_to_yolo import convert_one, load_class_ids


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
REVERSE_CLASS_ID = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a new PCB defect dataset by replacing existing field samples, "
            "rebuilding their labels from LabelMe JSON, and reducing duplicated "
            "reverse_component source groups."
        )
    )
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--max-reverse-per-group", type=int, default=2)
    return parser.parse_args()


def image_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def require_layout(root: Path) -> None:
    required = [
        root / "images" / "field",
        root / "images" / "train",
        root / "images" / "val",
        root / "labels" / "train",
        root / "labels" / "val",
        root / "classes.txt",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing dataset paths: " + ", ".join(missing))


def copy_pair(image_path: Path, label_path: Path, image_dir: Path, label_dir: Path) -> None:
    if not label_path.exists():
        raise FileNotFoundError(f"Missing label for {image_path.name}: {label_path}")
    shutil.copy2(image_path, image_dir / image_path.name)
    shutil.copy2(label_path, label_dir / label_path.name)


def label_rows(label_path: Path) -> list[list[str]]:
    rows = []
    for raw_line in label_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line:
            rows.append(line.split())
    return rows


def label_counts(label_path: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    for row in label_rows(label_path):
        counts[int(row[0])] += 1
    return counts


def has_class(label_path: Path, class_id: int) -> bool:
    return any(int(row[0]) == class_id for row in label_rows(label_path))


def source_group(stem: str) -> str:
    return stem.split(".rf.", 1)[0]


def decode_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not decode image: {path}")
    return image


def image_quality(path: Path) -> dict[str, float]:
    image = decode_image(path)
    gray = cv2.cvtColor(cv2.resize(image, (160, 160)), cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    return {
        "sharpness": sharpness,
        "brightness": brightness,
        "brightness_distance": abs(brightness - 127.5),
    }


def quality_sort_key(item: tuple[Path, dict[str, float]]) -> tuple:
    image_path, quality = item
    augmented = 1.0 if ".rf." in image_path.stem else 0.0
    return (
        augmented,
        -quality["sharpness"],
        quality["brightness_distance"],
        image_path.name,
    )


def copy_non_field_split(source_root: Path, build_root: Path, split: str) -> int:
    source_images = source_root / "images" / split
    source_labels = source_root / "labels" / split
    output_images = build_root / "images" / split
    output_labels = build_root / "labels" / split
    copied = 0
    for image_path in image_files(source_images):
        if image_path.stem.startswith("field_"):
            continue
        copy_pair(
            image_path,
            source_labels / f"{image_path.stem}.txt",
            output_images,
            output_labels,
        )
        copied += 1
    return copied


def locate_recovery_image(source_root: Path, stem: str) -> Path:
    matches = []
    for split in ("train", "val"):
        matches.extend(
            path
            for path in image_files(source_root / "images" / split)
            if path.stem == stem
        )
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected exactly one recovery image for {stem}, found {len(matches)}"
        )
    return matches[0]


def rebuild_field_samples(
    source_root: Path,
    build_root: Path,
    class_ids: dict[str, int],
) -> dict:
    source_field = source_root / "images" / "field"
    output_field_images = build_root / "images" / "field"
    output_field_labels = build_root / "labels" / "field"
    output_train_images = build_root / "images" / "train"
    output_train_labels = build_root / "labels" / "train"

    field_images = {path.stem: path for path in image_files(source_field)}
    field_jsons = {path.stem: path for path in sorted(source_field.glob("*.json"))}
    recovered_images: dict[str, Path] = {}
    for stem in sorted(set(field_jsons) - set(field_images)):
        recovered_images[stem] = locate_recovery_image(source_root, stem)

    all_images = dict(field_images)
    all_images.update(recovered_images)
    negative_stems = []
    empty_json_stems = []

    for file_path in sorted(source_field.iterdir()):
        if file_path.is_file():
            shutil.copy2(file_path, output_field_images / file_path.name)
    for stem, image_path in recovered_images.items():
        shutil.copy2(image_path, output_field_images / image_path.name)

    for stem, image_path in sorted(all_images.items()):
        json_path = field_jsons.get(stem)
        label_path = output_field_labels / f"{stem}.txt"
        if json_path is None:
            label_path.write_text("", encoding="utf-8")
            negative_stems.append(stem)
        else:
            convert_one(json_path, output_field_labels, class_ids)
            if not label_rows(label_path):
                empty_json_stems.append(stem)

        shutil.copy2(image_path, output_train_images / image_path.name)
        shutil.copy2(label_path, output_train_labels / label_path.name)

    return {
        "source_field_images": len(field_images),
        "source_field_jsons": len(field_jsons),
        "training_field_samples": len(all_images),
        "recovered_images": {
            stem: str(path.relative_to(source_root)).replace("\\", "/")
            for stem, path in sorted(recovered_images.items())
        },
        "negative_without_json": negative_stems,
        "negative_empty_json": empty_json_stems,
    }


def reduce_reverse_component(build_root: Path, max_per_group: int) -> dict:
    train_images = build_root / "images" / "train"
    train_labels = build_root / "labels" / "train"
    val_labels = build_root / "labels" / "val"

    val_reverse_groups = {
        source_group(path.stem)
        for path in sorted(val_labels.glob("*.txt"))
        if has_class(path, REVERSE_CLASS_ID)
    }
    grouped_images: dict[str, list[Path]] = defaultdict(list)
    for image_path in image_files(train_images):
        label_path = train_labels / f"{image_path.stem}.txt"
        if has_class(label_path, REVERSE_CLASS_ID):
            grouped_images[source_group(image_path.stem)].append(image_path)

    removed = []
    kept = []
    removed_counts: Counter[int] = Counter()
    kept_reverse_instances = 0

    for group, paths in sorted(grouped_images.items()):
        scored = [(path, image_quality(path)) for path in paths]
        ranked = sorted(scored, key=quality_sort_key)
        if group in val_reverse_groups:
            selected = []
            rejected = ranked
            reason = "train_val_source_overlap"
        else:
            selected = ranked[:max_per_group]
            rejected = ranked[max_per_group:]
            reason = "source_group_limit"

        for image_path, quality in selected:
            counts = label_counts(train_labels / f"{image_path.stem}.txt")
            kept_reverse_instances += counts[REVERSE_CLASS_ID]
            kept.append(
                {
                    "file": image_path.name,
                    "group": group,
                    "reverse_instances": counts[REVERSE_CLASS_ID],
                    **quality,
                }
            )
        for image_path, quality in rejected:
            label_path = train_labels / f"{image_path.stem}.txt"
            counts = label_counts(label_path)
            removed_counts.update(counts)
            removed.append(
                {
                    "file": image_path.name,
                    "group": group,
                    "reason": reason,
                    "class_instances": dict(sorted(counts.items())),
                    **quality,
                }
            )
            image_path.unlink()
            label_path.unlink()

    return {
        "max_per_non_overlap_group": max_per_group,
        "validation_overlap_groups": sorted(val_reverse_groups & set(grouped_images)),
        "source_groups_before": len(grouped_images),
        "images_before": len(kept) + len(removed),
        "images_kept": len(kept),
        "images_removed": len(removed),
        "reverse_instances_kept": kept_reverse_instances,
        "removed_class_instances": dict(sorted(removed_counts.items())),
        "kept": kept,
        "removed": removed,
    }


def validate_split(root: Path, split: str, class_count: int) -> dict:
    image_dir = root / "images" / split
    label_dir = root / "labels" / split
    images = {path.stem: path for path in image_files(image_dir)}
    labels = {path.stem: path for path in sorted(label_dir.glob("*.txt"))}
    if set(images) != set(labels):
        missing_labels = sorted(set(images) - set(labels))
        missing_images = sorted(set(labels) - set(images))
        raise ValueError(
            f"{split} pairing mismatch: missing labels={missing_labels}, "
            f"missing images={missing_images}"
        )

    class_instances: Counter[int] = Counter()
    class_images: Counter[int] = Counter()
    empty_labels = 0
    dimensions: Counter[str] = Counter()
    non_gray_images = []

    for stem, image_path in sorted(images.items()):
        rows = label_rows(labels[stem])
        if not rows:
            empty_labels += 1
        file_classes = set()
        for row in rows:
            if len(row) != 5:
                raise ValueError(f"Expected YOLO bbox row in {labels[stem]}: {row}")
            class_id = int(row[0])
            coordinates = [float(value) for value in row[1:]]
            if not 0 <= class_id < class_count:
                raise ValueError(f"Invalid class id {class_id} in {labels[stem]}")
            if any(value < 0.0 or value > 1.0 for value in coordinates):
                raise ValueError(f"Out-of-range coordinates in {labels[stem]}: {row}")
            class_instances[class_id] += 1
            file_classes.add(class_id)
        for class_id in file_classes:
            class_images[class_id] += 1

        image = decode_image(image_path)
        height, width = image.shape[:2]
        dimensions[f"{width}x{height}"] += 1
        if not (
            np.array_equal(image[:, :, 0], image[:, :, 1])
            and np.array_equal(image[:, :, 1], image[:, :, 2])
        ):
            non_gray_images.append(image_path.name)

    if non_gray_images:
        raise ValueError(
            f"{split} contains non-grayscale image content: {non_gray_images[:10]}"
        )

    return {
        "images": len(images),
        "labels": len(labels),
        "empty_labels": empty_labels,
        "dimensions": dict(sorted(dimensions.items())),
        "class_images": dict(sorted(class_images.items())),
        "class_instances": dict(sorted(class_instances.items())),
        "all_images_are_three_channel_grayscale_jpg": all(
            path.suffix.lower() == ".jpg" for path in images.values()
        ),
    }


def write_dataset_yaml(
    write_root: Path,
    configured_root: Path,
    class_names: list[str],
) -> None:
    config = {
        "path": configured_root.resolve().as_posix(),
        "train": "images/train",
        "val": "images/val",
        "test": "images/val",
        "names": {index: name for index, name in enumerate(class_names)},
    }
    (write_root / "dataset.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    source_root = args.source_root.resolve()
    output_root = args.output_root.resolve()
    build_root = output_root.with_name(output_root.name + ".building")

    if args.max_reverse_per_group < 1:
        raise ValueError("--max-reverse-per-group must be at least 1")
    require_layout(source_root)
    if output_root.exists():
        raise FileExistsError(f"Output already exists: {output_root}")
    if build_root.exists():
        raise FileExistsError(f"Temporary output already exists: {build_root}")

    class_names = [
        line.strip()
        for line in (source_root / "classes.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    class_ids = load_class_ids()
    expected_class_ids = {name: index for index, name in enumerate(class_names)}
    if class_ids != expected_class_ids:
        raise ValueError(
            f"Class mapping mismatch: config={class_ids}, classes.txt={expected_class_ids}"
        )

    try:
        for path in (
            build_root / "images" / "train",
            build_root / "images" / "val",
            build_root / "images" / "field",
            build_root / "labels" / "train",
            build_root / "labels" / "val",
            build_root / "labels" / "field",
        ):
            path.mkdir(parents=True, exist_ok=True)

        shutil.copy2(source_root / "classes.txt", build_root / "classes.txt")
        backup_dir = source_root / "labels_polygon_backup"
        if backup_dir.exists():
            shutil.copytree(backup_dir, build_root / "labels_polygon_backup")

        copied_train_base = copy_non_field_split(source_root, build_root, "train")
        copied_val_base = copy_non_field_split(source_root, build_root, "val")
        field_report = rebuild_field_samples(source_root, build_root, class_ids)
        reverse_report = reduce_reverse_component(
            build_root, args.max_reverse_per_group
        )
        train_report = validate_split(build_root, "train", len(class_names))
        val_report = validate_split(build_root, "val", len(class_names))

        write_dataset_yaml(build_root, output_root, class_names)
        report = {
            "source_root": source_root.as_posix(),
            "output_root": output_root.as_posix(),
            "policy": {
                "field_mode": "replace_existing_and_move_all_to_train",
                "field_without_annotation": "negative_sample_with_empty_label",
                "reverse_sampling": "drop_val_overlap_then_keep_max_per_source_group",
                "max_reverse_per_non_overlap_group": args.max_reverse_per_group,
                "image_format": "preserve_original_jpg_grayscale_content_and_dimensions",
            },
            "base_samples_copied": {
                "train_non_field": copied_train_base,
                "val_non_field": copied_val_base,
            },
            "field": field_report,
            "reverse_component": reverse_report,
            "final": {"train": train_report, "val": val_report},
        }
        (build_root / "dataset_preparation_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        removed_lines = [item["file"] for item in reverse_report["removed"]]
        (build_root / "removed_reverse_samples.txt").write_text(
            "\n".join(removed_lines) + ("\n" if removed_lines else ""),
            encoding="utf-8",
        )

        build_root.rename(output_root)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception:
        if build_root.exists():
            shutil.rmtree(build_root)
        raise


if __name__ == "__main__":
    main()
