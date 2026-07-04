import json
import shutil
import time
import unittest
import uuid
from pathlib import Path

import cv2
import numpy as np

import camera_trigger
import main


def remove_tree_retry(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


class ScanMosaicTests(unittest.TestCase):
    def test_grid_plan_uses_default_twenty_percent_overlap(self):
        plan = main.build_grid_plan(60, 60)

        self.assertEqual(plan["rows"], 3)
        self.assertEqual(plan["cols"], 3)
        self.assertEqual(plan["step_mm"], {"x": 24.0, "y": 24.0})
        self.assertEqual(plan["total_cells"], 9)

        cells = main.iter_snake_cells(plan)
        self.assertEqual((cells[0]["row"], cells[0]["col"], cells[0]["x_mm"], cells[0]["y_mm"]), (0, 0, 0.0, 0.0))
        self.assertEqual((cells[1]["row"], cells[1]["col"], cells[1]["x_mm"], cells[1]["y_mm"]), (0, 1, 24.0, 0.0))
        self.assertEqual((cells[3]["row"], cells[3]["col"], cells[3]["x_mm"], cells[3]["y_mm"]), (1, 2, 48.0, 24.0))

    def test_build_scan_mosaic_blends_overlapping_cells(self):
        scan_id = f"unit-mosaic-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            cell0 = scan_dir / "r000_c000" / "raw.jpg"
            cell1 = scan_dir / "r000_c001" / "raw.jpg"
            cell0.parent.mkdir(parents=True, exist_ok=True)
            cell1.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cell0), np.full((10, 10, 3), 100, dtype=np.uint8))
            cv2.imwrite(str(cell1), np.full((10, 10, 3), 200, dtype=np.uint8))

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "rows": 1,
                    "cols": 2,
                    "fov_mm": {"width": 10.0, "height": 10.0},
                    "step_mm": {"x": 5.0, "y": 10.0},
                    "pcb_mm": {"length": 15.0, "width": 10.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                },
                "cells": [
                    {"row": 0, "col": 0, "x_mm": 0.0, "y_mm": 0.0, "raw_image": camera_trigger._rel(cell0)},
                    {"row": 0, "col": 1, "x_mm": 5.0, "y_mm": 0.0, "raw_image": camera_trigger._rel(cell1)},
                ],
            }
            manifest_path = scan_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=False)

            self.assertTrue(result["ok"])
            mosaic = cv2.imread(str(camera_trigger.resolve_artifact(result["mosaic_raw"])))
            self.assertEqual(mosaic.shape[:2], (10, 15))
            self.assertTrue(95 <= int(mosaic[0, 2, 0]) <= 105)
            self.assertTrue(195 <= int(mosaic[0, 12, 0]) <= 205)
            self.assertTrue(145 <= int(mosaic[0, 7, 0]) <= 155)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_simulation_mosaic_restores_source_pixels(self):
        scan_id = f"unit-sim-pixel-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            source = scan_dir / "source.png"
            source.parent.mkdir(parents=True, exist_ok=True)
            image = np.zeros((37, 53, 3), dtype=np.uint8)
            yy, xx = np.indices(image.shape[:2])
            image[:, :, 0] = (xx * 3 + yy * 5) % 256
            image[:, :, 1] = (xx * 7 + 11) % 256
            image[:, :, 2] = (yy * 13 + 17) % 256
            cv2.imwrite(str(source), image)

            plan = {
                "rows": 2,
                "cols": 3,
                "fov_mm": {"width": 30.0, "height": 30.0},
                "step_mm": {"x": 24.0, "y": 24.0},
                "covered_mm": {"length": 78.0, "width": 54.0},
                "origin_mm": {"x": 0.0, "y": 0.0},
            }

            cells = []
            for row in range(plan["rows"]):
                for col in range(plan["cols"]):
                    raw_path, _, crop_meta = camera_trigger._write_simulation_crop(source, row, col, scan_id, plan)
                    cells.append(
                        {
                            "row": row,
                            "col": col,
                            "x_mm": col * plan["step_mm"]["x"],
                            "y_mm": row * plan["step_mm"]["y"],
                            "raw_image": camera_trigger._rel(raw_path),
                            "simulation_crop": crop_meta,
                        }
                    )

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "simulation",
                "plan": plan,
                "cells": cells,
            }
            manifest_path = scan_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["geometry"]["mode"], "pixel_restore")
            mosaic = cv2.imread(str(camera_trigger.resolve_artifact(result["mosaic_raw"])))
            self.assertEqual(mosaic.shape, image.shape)
            self.assertTrue(np.array_equal(mosaic, image))
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_tiled_phone_detection_maps_tile_boxes_to_full_image(self):
        upload_id = f"unit-phone-tile-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeDetector:
            def __init__(self):
                self.calls = 0

            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                self.calls += 1
                if self.calls != 2:
                    return [], frame.copy(), 1.0
                return [
                    {
                        "type": "improper_installation",
                        "label": "improper_installation",
                        "bbox": [1, 2, 4, 5],
                        "confidence": 0.91,
                    }
                ], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((40, 40, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)

            camera_trigger.yolo_detector = FakeDetector()
            detections, _, details = camera_trigger.detect_tiled_image(
                raw_path,
                upload_dir / "annotated.jpg",
                upload_dir / "result.json",
                tile_size=32,
                imgsz=960,
                overlap=0.0,
            )

            self.assertEqual(details["strategy"], "tiled")
            self.assertEqual(details["tile_count"], 4)
            self.assertEqual(len(detections), 1)
            self.assertEqual(detections[0]["bbox"], [9, 2, 12, 5])
            self.assertEqual(detections[0]["source_tile"]["x"], 8)
            self.assertTrue((upload_dir / "annotated.jpg").exists())
            self.assertTrue((upload_dir / "result.json").exists())
        finally:
            camera_trigger.yolo_detector = original_detector
            if upload_dir.exists():
                remove_tree_retry(upload_dir)

    def test_tiled_phone_detection_supports_multiple_tile_sizes(self):
        upload_id = f"unit-phone-multiscale-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeDetector:
            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                if frame.shape[0] != 40 or frame.shape[1] != 40:
                    return [], frame.copy(), 1.0
                return [
                    {
                        "type": "short_circuit",
                        "label": "short_circuit",
                        "bbox": [10, 11, 20, 22],
                        "confidence": 0.88,
                    }
                ], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((40, 40, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)

            camera_trigger.yolo_detector = FakeDetector()
            detections, _, details = camera_trigger.detect_tiled_image(
                raw_path,
                upload_dir / "annotated.jpg",
                upload_dir / "result.json",
                tile_size=[32, 40],
                imgsz=960,
                overlap=0.0,
            )

            self.assertEqual(details["strategy"], "profile_ensemble")
            self.assertEqual(details["tile_sizes"], [32, 40])
            self.assertEqual(details["selected_profile"]["tile_size"], 40)
            self.assertEqual(details["tile_count"], 5)
            self.assertEqual(len(detections), 1)
            self.assertEqual(detections[0]["bbox"], [10, 11, 20, 22])
            self.assertEqual(detections[0]["source_tile"]["tile_size"], 40)
        finally:
            camera_trigger.yolo_detector = original_detector
            if upload_dir.exists():
                remove_tree_retry(upload_dir)

    def test_tiled_phone_detection_fuses_repeated_profile_evidence(self):
        upload_id = f"unit-phone-profile-fusion-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeDetector:
            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                if imgsz == 640:
                    return [
                        {
                            "type": "short_circuit",
                            "label": "short_circuit",
                            "bbox": [10, 10, 22, 22],
                            "confidence": 0.34,
                        },
                        {
                            "type": "improper_installation",
                            "label": "improper_installation",
                            "bbox": [25, 25, 35, 35],
                            "confidence": 0.24,
                        },
                    ], frame.copy(), 1.0
                if imgsz == 960:
                    return [
                        {
                            "type": "short_circuit",
                            "label": "short_circuit",
                            "bbox": [11, 11, 23, 23],
                            "confidence": 0.36,
                        }
                    ], frame.copy(), 1.0
                return [], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((40, 40, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)

            camera_trigger.yolo_detector = FakeDetector()
            detections, _, details = camera_trigger.detect_tiled_image(
                raw_path,
                upload_dir / "annotated.jpg",
                upload_dir / "result.json",
                tile_size=40,
                imgsz=[640, 960],
                overlap=0.0,
            )

            self.assertEqual(details["strategy"], "profile_ensemble")
            self.assertEqual(details["confirmed_count"], 1)
            self.assertEqual(details["suspected_count"], 1)
            self.assertEqual(len(detections), 2)

            confirmed = details["confirmed_defects"][0]
            self.assertEqual(confirmed["type"], "short_circuit")
            self.assertEqual(confirmed["decision"], "confirmed")
            self.assertEqual(confirmed["support_count"], 2)
            self.assertGreaterEqual(confirmed["confidence"], 0.4)
            self.assertEqual({profile["imgsz"] for profile in confirmed["source_profiles"]}, {640, 960})

            suspected = details["suspected_defects"][0]
            self.assertEqual(suspected["type"], "improper_installation")
            self.assertEqual(suspected["decision"], "suspected")
            self.assertEqual(suspected["support_count"], 1)
        finally:
            camera_trigger.yolo_detector = original_detector
            if upload_dir.exists():
                remove_tree_retry(upload_dir)

    def test_phone_upload_detection_uses_fast_profile_by_default(self):
        upload_id = f"unit-phone-fast-profile-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeDetector:
            def __init__(self):
                self.calls = []

            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                self.calls.append((frame.shape[1], frame.shape[0], imgsz))
                return [], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((80, 80, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)
            metadata = {
                "upload_id": upload_id,
                "name": "fast.jpg",
                "raw_image": camera_trigger._rel(raw_path),
                "status": "uploaded",
            }
            camera_trigger._write_phone_metadata(metadata)

            detector = FakeDetector()
            camera_trigger.yolo_detector = detector
            result = camera_trigger.detect_phone_upload(upload_id)

            self.assertEqual(result["detection_profile"], "fast")
            self.assertEqual(result["detection_profile_label"], "快速模式")
            self.assertEqual(result["detection_details"]["tile_sizes"], [640])
            self.assertEqual(result["detection_details"]["imgszs"], [640])
            self.assertEqual(result["detection_details"]["tile_overlap"], 0.5)
            self.assertEqual(len(detector.calls), 1)
            self.assertEqual(detector.calls[0][2], 640)
        finally:
            camera_trigger.yolo_detector = original_detector
            if upload_dir.exists():
                remove_tree_retry(upload_dir)

    def test_phone_upload_detection_uses_precision_profile_when_requested(self):
        upload_id = f"unit-phone-precision-profile-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeDetector:
            def __init__(self):
                self.calls = []

            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                self.calls.append((frame.shape[1], frame.shape[0], imgsz))
                return [], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((80, 80, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)
            metadata = {
                "upload_id": upload_id,
                "name": "precision.jpg",
                "raw_image": camera_trigger._rel(raw_path),
                "status": "uploaded",
            }
            camera_trigger._write_phone_metadata(metadata)

            detector = FakeDetector()
            camera_trigger.yolo_detector = detector
            result = camera_trigger.detect_phone_upload(upload_id, detection_profile="precision")

            self.assertEqual(result["detection_profile"], "precision")
            self.assertEqual(result["detection_profile_label"], "高精模式")
            self.assertEqual(result["detection_details"]["tile_sizes"], [512, 640, 800])
            self.assertEqual(result["detection_details"]["imgszs"], [640, 960])
            self.assertEqual(result["detection_details"]["tile_overlap"], 0.5)
            self.assertEqual(len(detector.calls), 6)
        finally:
            camera_trigger.yolo_detector = original_detector
            if upload_dir.exists():
                remove_tree_retry(upload_dir)


if __name__ == "__main__":
    unittest.main()
