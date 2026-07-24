import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


CORE_DIR = Path(__file__).resolve().parents[1] / "core"
sys.path.insert(0, str(CORE_DIR))

import train


class ValidateTrainingInputsTests(unittest.TestCase):
    def test_returns_train_and_val_image_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "yolov8n.pt"
            model_path.write_bytes(b"weights")
            (root / "images" / "train").mkdir(parents=True)
            (root / "images" / "val").mkdir(parents=True)
            (root / "images" / "train" / "train.jpg").write_bytes(b"image")
            (root / "images" / "val" / "val.png").write_bytes(b"image")
            config_path = root / "dataset.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "path": str(root),
                        "train": "images/train",
                        "val": "images/val",
                    }
                ),
                encoding="utf-8",
            )

            counts = train.validate_training_inputs(model_path, config_path)

            self.assertEqual(counts, {"train_images": 1, "val_images": 1})

    def test_rejects_missing_local_pretrained_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "dataset.yaml"
            config_path.write_text("path: .\ntrain: images/train\nval: images/val\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "Local pretrained model not found"):
                train.validate_training_inputs(root / "missing.pt", config_path)

    def test_rejects_empty_validation_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_path = root / "yolov8n.pt"
            model_path.write_bytes(b"weights")
            (root / "images" / "train").mkdir(parents=True)
            (root / "images" / "train" / "train.jpg").write_bytes(b"image")
            config_path = root / "dataset.yaml"
            config_path.write_text(
                "path: .\ntrain: images/train\nval: images/val\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(FileNotFoundError, "No validation images found"):
                train.validate_training_inputs(model_path, config_path)


class TrainingConfigurationTests(unittest.TestCase):
    def test_enables_ultralytics_offline_mode(self) -> None:
        self.assertEqual(os.environ.get("YOLO_OFFLINE"), "true")

    @patch.object(train, "validate_training_inputs", return_value={"train_images": 1, "val_images": 1})
    @patch.object(train, "build_runtime_data_config", return_value=Path("dataset.yaml"))
    @patch.object(train, "YOLO")
    def test_uses_gpu_auto_batch_without_network_amp_check(
        self,
        yolo_mock,
        _runtime_config_mock,
        _validate_mock,
    ) -> None:
        train.main()

        train_kwargs = yolo_mock.return_value.train.call_args.kwargs
        self.assertEqual(train_kwargs["imgsz"], 960)
        self.assertEqual(train_kwargs["device"], 0)
        self.assertEqual(train_kwargs["batch"], -1)
        self.assertEqual(train_kwargs["workers"], 0)
        self.assertIs(train_kwargs["cache"], False)
        self.assertEqual(train_kwargs["mosaic"], 1.0)
        self.assertIs(train_kwargs["amp"], False)


if __name__ == "__main__":
    unittest.main()
