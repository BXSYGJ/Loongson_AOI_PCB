import json
import shutil
import time
import unittest
import uuid
import asyncio
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
    def test_onnx_detector_maps_letterboxed_boxes_to_original_image(self):
        class FakeOnnxNet:
            def __init__(self, output):
                self.output = output

            def setInput(self, blob):
                self.blob_shape = blob.shape

            def forward(self):
                return self.output

        detector = camera_trigger.YoloDetector()
        detector.weights = Path("unit-test.onnx")
        detector._model = FakeOnnxNet(np.zeros((1, 11, 8400), dtype=np.float32))
        detector._names = {
            0: "dry_solder_joint",
            1: "improper_installation",
            2: "pcb_damage",
            3: "short_circuit",
            4: "insufficient_solder",
            5: "reverse_component",
            6: "excess_solder",
        }
        detector._cn_names = {}
        detector._backend = "onnx_opencv"
        detector.conf = 0.5
        detector.imgsz = 640

        output = detector._model.output
        output[0, 0, 0] = 120.0
        output[0, 1, 0] = 230.0
        output[0, 2, 0] = 40.0
        output[0, 3, 0] = 40.0
        output[0, 7, 0] = 0.86

        image = np.zeros((320, 640, 3), dtype=np.uint8)
        detections, annotated, _ = detector.detect_frame(image, imgsz=640)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["type"], "short_circuit")
        self.assertEqual(detections[0]["bbox"], [100, 50, 140, 90])
        self.assertAlmostEqual(detections[0]["confidence"], 0.86, places=2)
        self.assertEqual(annotated.shape, image.shape)

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

    def test_simulation_upload_resizes_when_safe_limit_enabled(self):
        original_limit = camera_trigger.SIMULATION_MAX_DIMENSION
        original_quality = camera_trigger.SIMULATION_JPEG_QUALITY
        original_board_safe = camera_trigger.BOARD_SAFE_MODE
        image = np.zeros((60, 120, 3), dtype=np.uint8)
        image[:, :, 0] = 40
        image[:, :, 1] = 120
        image[:, :, 2] = 220
        ok, encoded = cv2.imencode(".png", image)
        self.assertTrue(ok)

        saved_path = None
        try:
            camera_trigger.SIMULATION_MAX_DIMENSION = 64
            camera_trigger.SIMULATION_JPEG_QUALITY = 80
            camera_trigger.BOARD_SAFE_MODE = True

            result = camera_trigger.save_simulation_upload("large-board.png", encoded.tobytes())
            saved_path = camera_trigger.resolve_artifact(result["path"])
            saved = cv2.imread(str(saved_path))

            self.assertTrue(result["ok"])
            self.assertEqual(result["name"], "large-board.jpg")
            self.assertEqual(result["original_width"], 120)
            self.assertEqual(result["original_height"], 60)
            self.assertEqual(result["width"], 64)
            self.assertEqual(result["height"], 32)
            self.assertTrue(result["resized"])
            self.assertTrue(result["board_safe_mode"])
            self.assertEqual(result["max_dimension"], 64)
            self.assertEqual(result["preprocess"], ["resize_max_64", "jpeg_reencode"])
            self.assertTrue(saved_path.exists())
            self.assertEqual(saved.shape[:2], (32, 64))
        finally:
            camera_trigger.SIMULATION_MAX_DIMENSION = original_limit
            camera_trigger.SIMULATION_JPEG_QUALITY = original_quality
            camera_trigger.BOARD_SAFE_MODE = original_board_safe
            if saved_path is not None and saved_path.exists():
                saved_path.unlink()

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

    def test_tiled_phone_detection_skips_imgsz_incompatible_with_fixed_onnx(self):
        upload_id = f"unit-phone-onnx-imgsz-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        if upload_dir.exists():
            remove_tree_retry(upload_dir)

        class FakeOnnxDetector:
            imgsz = 640

            def __init__(self):
                self.calls = []

            def compatible_imgszs(self, requested_imgszs):
                return [640], [960], 640

            def detect_frame(self, frame, annotated_path=None, imgsz=None):
                self.calls.append(imgsz)
                if imgsz != 640:
                    raise camera_trigger.CameraTriggerError("fixed ONNX input")
                return [], frame.copy(), 1.0

        original_detector = camera_trigger.yolo_detector
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            image = np.full((80, 80, 3), 80, dtype=np.uint8)
            cv2.imwrite(str(raw_path), image)

            detector = FakeOnnxDetector()
            camera_trigger.yolo_detector = detector
            detections, _, details = camera_trigger.detect_tiled_image(
                raw_path,
                upload_dir / "annotated.jpg",
                upload_dir / "result.json",
                tile_size=640,
                imgsz=[640, 960],
                overlap=0.5,
            )

            self.assertEqual(detections, [])
            self.assertEqual(details["requested_imgszs"], [640, 960])
            self.assertEqual(details["imgszs"], [640])
            self.assertEqual(details["skipped_imgszs"], [960])
            self.assertTrue(details["imgsz_adjusted_for_backend"])
            self.assertEqual(details["onnx_input_imgsz"], 640)
            self.assertEqual(detector.calls, [640])
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

    def test_run_phone_detection_falls_back_to_local_when_no_phone_worker_is_online(self):
        class FakeWebSocket:
            def __init__(self):
                self.packets = []

            async def send_json(self, packet):
                self.packets.append(packet)

        original_get_upload = camera_trigger.get_phone_upload
        original_profile_config = camera_trigger.phone_detection_profile_config
        original_detect = camera_trigger.detect_phone_upload
        try:
            camera_trigger.get_phone_upload = lambda upload_id: {
                "ok": True,
                "upload_id": upload_id,
                "name": "fallback.jpg",
                "raw_image": "algorithm/outputs/mobile_uploads/fallback/raw.jpg",
            }
            camera_trigger.phone_detection_profile_config = lambda profile=None: {
                "profile": "fast",
                "label": "快速模式",
                "tile_sizes": (640,),
                "imgszs": (640,),
                "overlap": 0.0,
            }
            camera_trigger.detect_phone_upload = lambda upload_id, profile=None: {
                "ok": True,
                "upload_id": upload_id,
                "raw_image": "algorithm/outputs/mobile_uploads/fallback/raw.jpg",
                "annotated_image": "algorithm/outputs/mobile_uploads/fallback/annotated.jpg",
                "result_json": "algorithm/outputs/mobile_uploads/fallback/result.json",
                "defects": [],
                "inference_ms": 12.3,
                "detection_profile": "fast",
                "detection_profile_label": "快速模式",
            }

            websocket = FakeWebSocket()
            asyncio.run(main.run_phone_detection(websocket, "scan-local-fallback", "fallback-upload", asyncio.Event()))

            result_packets = [packet for packet in websocket.packets if packet["type"] == "phone_result"]
            self.assertEqual(len(result_packets), 1)
            payload = result_packets[0]["payload"]
            self.assertEqual(payload["operation_mode"], "phone")
            self.assertEqual(payload["inference_location"], "local_fallback")
            self.assertEqual(payload["fallback_reason"], "no_phone_worker_online")
            self.assertTrue(payload["local_fallback"])
        finally:
            camera_trigger.get_phone_upload = original_get_upload
            camera_trigger.phone_detection_profile_config = original_profile_config
            camera_trigger.detect_phone_upload = original_detect

    def test_phone_upload_local_fallback_marks_detection_location(self):
        original_detect = camera_trigger.detect_phone_upload
        try:
            camera_trigger.detect_phone_upload = lambda upload_id, profile=None: {
                "ok": True,
                "upload_id": upload_id,
                "defects": [],
                "detection_profile": profile or "fast",
            }

            result = camera_trigger.detect_phone_upload_local_fallback(
                "fallback-upload",
                detection_profile="precision",
                fallback_reason="phone_worker_timeout",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["upload_id"], "fallback-upload")
            self.assertEqual(result["detection_profile"], "precision")
            self.assertEqual(result["inference_location"], "local_fallback")
            self.assertEqual(result["fallback_reason"], "phone_worker_timeout")
            self.assertTrue(result["local_fallback"])
        finally:
            camera_trigger.detect_phone_upload = original_detect

    def test_detect_captured_frame_marks_local_fallback_for_grid_modes(self):
        scan_id = f"unit-grid-local-fallback-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        class FakeDetector:
            def detect_image(self, image_path, annotated_path, json_path):
                annotated_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image_path, annotated_path)
                json_path.write_text("[]", encoding="utf-8")
                return [], 3.2

        original_detector = camera_trigger.yolo_detector
        try:
            raw_path = scan_dir / "r000_c000" / "raw.jpg"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(raw_path), np.full((16, 16, 3), 80, dtype=np.uint8))

            camera_trigger.yolo_detector = FakeDetector()
            result = asyncio.run(
                camera_trigger.detect_captured_frame(
                    0,
                    0,
                    0.0,
                    0.0,
                    scan_id,
                    camera_trigger._rel(raw_path),
                    asyncio.Event(),
                )
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["inference_location"], "local_fallback")
            self.assertEqual(result["fallback_reason"], "no_phone_worker_online")
            self.assertTrue(result["local_fallback"])
        finally:
            camera_trigger.yolo_detector = original_detector
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_frontend_mentions_local_fallback_for_phone_results(self):
        script = (camera_trigger.ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("本机兜底", script)
        self.assertIn("local_fallback", script)

    def test_phone_page_contains_browser_inference_worker(self):
        html = (camera_trigger.ROOT / "static" / "phone.html").read_text(encoding="utf-8")
        self.assertIn("onnxruntime-web", html)
        self.assertIn("/ws/phone-inference", html)
        self.assertIn("inference_request", html)
        self.assertIn("inference_result", html)

    def test_desktop_status_shows_phone_inference_worker_state(self):
        html = (camera_trigger.ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (camera_trigger.ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("phoneInferenceStatus", html)
        self.assertIn("phone_inference", script)
        self.assertIn("phoneInferenceStatus", script)

    def test_phone_first_artifact_detection_uses_phone_worker_result(self):
        scan_id = f"unit-phone-worker-artifact-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        class FakeHub:
            def status(self):
                return {"ready": True, "worker_count": 1}

            async def detect_artifact(self, raw_image, metadata=None):
                return {
                    "ok": True,
                    "job_id": "job-1",
                    "inference_location": "phone_browser",
                    "inference_ms": 7.5,
                    "defects": [
                        {
                            "type": "short_circuit",
                            "label": "短路",
                            "bbox": [1, 2, 8, 9],
                            "confidence": 0.88,
                        }
                    ],
                }

        original_hub = main.phone_inference_hub
        try:
            raw_path = scan_dir / "r000_c000" / "raw.jpg"
            annotated_path = scan_dir / "r000_c000" / "annotated.jpg"
            result_json_path = scan_dir / "r000_c000" / "result.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(raw_path), np.full((20, 20, 3), 70, dtype=np.uint8))

            main.phone_inference_hub = FakeHub()
            result = asyncio.run(
                main.detect_artifact_phone_first(
                    camera_trigger._rel(raw_path),
                    annotated_path,
                    result_json_path,
                    {"operation_mode": "real", "row": 0, "col": 0},
                )
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["inference_location"], "phone_browser")
            self.assertFalse(result["local_fallback"])
            self.assertEqual(result["defects"][0]["type"], "short_circuit")
            self.assertTrue(annotated_path.exists())
            self.assertTrue(result_json_path.exists())
            saved = json.loads(result_json_path.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["label"], "短路")
        finally:
            main.phone_inference_hub = original_hub
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_phone_first_artifact_detection_falls_back_when_no_worker(self):
        scan_id = f"unit-phone-worker-fallback-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        class FakeHub:
            def status(self):
                return {"ready": False, "worker_count": 0}

            async def detect_artifact(self, raw_image, metadata=None):
                raise main.PhoneInferenceUnavailable("no worker")

        class FakeDetector:
            def detect_image(self, image_path, annotated_path, json_path):
                annotated_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image_path, annotated_path)
                json_path.write_text("[]", encoding="utf-8")
                return [], 4.1

        original_hub = main.phone_inference_hub
        original_detector = camera_trigger.yolo_detector
        try:
            raw_path = scan_dir / "r000_c000" / "raw.jpg"
            annotated_path = scan_dir / "r000_c000" / "annotated.jpg"
            result_json_path = scan_dir / "r000_c000" / "result.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(raw_path), np.full((20, 20, 3), 70, dtype=np.uint8))

            main.phone_inference_hub = FakeHub()
            camera_trigger.yolo_detector = FakeDetector()
            result = asyncio.run(
                main.detect_artifact_phone_first(
                    camera_trigger._rel(raw_path),
                    annotated_path,
                    result_json_path,
                    {"operation_mode": "simulation", "row": 0, "col": 0},
                )
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["inference_location"], "local_fallback")
            self.assertEqual(result["fallback_reason"], "no_phone_worker_online")
            self.assertTrue(result["local_fallback"])
            self.assertEqual(result["defects"], [])
        finally:
            main.phone_inference_hub = original_hub
            camera_trigger.yolo_detector = original_detector
            if scan_dir.exists():
                remove_tree_retry(scan_dir)


if __name__ == "__main__":
    unittest.main()
