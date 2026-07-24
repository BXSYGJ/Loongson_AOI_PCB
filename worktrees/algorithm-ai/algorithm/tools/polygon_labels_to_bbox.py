import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABEL_DIR = ROOT / "datasets" / "pcb_defects" / "labels"
DEFAULT_BACKUP_DIR = ROOT / "datasets" / "pcb_defects" / "labels_polygon_backup"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert YOLO polygon labels to YOLO bbox labels.")
    parser.add_argument("--label-dir", default=str(DEFAULT_LABEL_DIR), help="Dataset labels directory.")
    parser.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR), help="Backup directory for original labels.")
    return parser.parse_args()


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def convert_line(line: str, label_path: Path) -> tuple[str, bool]:
    parts = line.split()
    if not parts:
        return "", False

    if len(parts) == 5:
        class_id, x_center, y_center, width, height = parts
        values = [clamp01(float(value)) for value in (x_center, y_center, width, height)]
        return f"{class_id} {values[0]:.6f} {values[1]:.6f} {values[2]:.6f} {values[3]:.6f}", False

    if len(parts) < 7 or (len(parts) - 1) % 2:
        raise ValueError(f"{label_path}: invalid YOLO polygon line with {len(parts)} values: {line}")

    class_id = parts[0]
    coords = [float(value) for value in parts[1:]]
    xs = [clamp01(value) for value in coords[0::2]]
    ys = [clamp01(value) for value in coords[1::2]]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_center = (x_min + x_max) / 2
    y_center = (y_min + y_max) / 2
    width = x_max - x_min
    height = y_max - y_min
    return f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}", True


def convert_file(label_path: Path) -> tuple[int, int]:
    lines = label_path.read_text(encoding="utf-8").splitlines()
    converted_lines = []
    converted_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        converted_line, was_polygon = convert_line(stripped, label_path)
        converted_lines.append(converted_line)
        converted_count += int(was_polygon)

    label_path.write_text("\n".join(converted_lines) + ("\n" if converted_lines else ""), encoding="utf-8")
    return len(converted_lines), converted_count


def main() -> None:
    args = parse_args()
    label_dir = Path(args.label_dir).resolve()
    backup_dir = Path(args.backup_dir).resolve()

    if not label_dir.exists():
        raise FileNotFoundError(f"Label directory not found: {label_dir}")
    if backup_dir.exists():
        raise FileExistsError(f"Backup directory already exists: {backup_dir}")

    shutil.copytree(label_dir, backup_dir)

    total_files = 0
    total_lines = 0
    total_converted = 0
    for label_path in sorted(label_dir.rglob("*.txt")):
        line_count, converted_count = convert_file(label_path)
        total_files += 1
        total_lines += line_count
        total_converted += converted_count

    for cache_path in label_dir.glob("*.cache"):
        cache_path.unlink()

    print(f"Backed up original labels to: {backup_dir}")
    print(f"Processed files: {total_files}")
    print(f"Processed labels: {total_lines}")
    print(f"Converted polygon labels: {total_converted}")


if __name__ == "__main__":
    main()
