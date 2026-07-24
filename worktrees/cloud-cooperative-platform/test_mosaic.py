import json
import shutil
import time
import unittest
import uuid
import asyncio
import gc
import weakref
import zipfile
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
    def test_real_motion_ui_caps_speed_and_displays_motion_telemetry(self):
        root = Path(__file__).resolve().parent
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="realMotionSpeed" type="number" min="0.1" max="25"', html)
        self.assertIn("10mm/s 以上为软实时高速档", html)
        self.assertIn('case "motion_complete"', app_js)
        self.assertIn("achieved_dominant_speed_mm_s", app_js)

    def test_real_motion_speed_is_capped_at_twenty_five_mm_s(self):
        self.assertEqual(main.normalize_motion_speed(30.0), 25.0)

    def test_llm_report_ui_has_thinking_toggle_and_markdown_export(self):
        root = Path(__file__).resolve().parent
        html = (root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        mini_wxml = (root / "miniprogram" / "pages" / "index" / "index.wxml").read_text(encoding="utf-8")
        mini_js = (root / "miniprogram" / "pages" / "index" / "index.js").read_text(encoding="utf-8")

        self.assertIn('id="llmThinkingToggle"', html)
        self.assertIn('id="llmModelSelect"', html)
        self.assertIn('id="downloadReportBtn"', html)
        self.assertIn("enable_thinking", app_js)
        self.assertIn("qwen3.6-flash", app_js)
        self.assertIn("qwen3.7-max", app_js)
        self.assertIn("bindchange=\"onThinkingModeChange\"", mini_wxml)
        self.assertIn("bindchange=\"onLlmModelChange\"", mini_wxml)
        self.assertIn("qwen3.6-flash", mini_js)
        self.assertIn("qwen3.7-max", mini_js)
        self.assertIn("exportDiagnosisReport", mini_js)

    def test_config_cn_names_are_english_labels(self):
        config_path = camera_trigger.ALGORITHM_ROOT / "algorithm" / "configs" / "pcb_defects.yaml"
        text = config_path.read_text(encoding="utf-8")
        self.assertIn("short_circuit: short_circuit", text)
        self.assertIn("dry_solder_joint: dry_solder_joint", text)
        self.assertNotIn("鐭", text)
        self.assertNotIn("骞", text)

    def test_annotation_text_is_english_ascii_even_for_non_ascii_input(self):
        text = camera_trigger._format_annotation_text(
            "短路",
            0.714,
            ascii_label="short_circuit",
        )
        self.assertEqual(text, "short_circuit 0.71")
        self.assertNotIn("?", text)
        self.assertEqual(camera_trigger._normalize_label_text("短路", "short_circuit"), "short_circuit")

    def test_bottom_panel_order_prioritizes_llm_report(self):
        html_path = camera_trigger.ROOT / "static" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        report_index = html.index('class="report-panel"')
        defect_index = html.index('class="defect-panel"')
        event_index = html.index('class="event-panel"')
        self.assertLess(report_index, defect_index)
        self.assertLess(defect_index, event_index)

    def test_stepped_canvas_mirrors_only_cell_images_and_cell_overlays(self):
        app_path = camera_trigger.ROOT / "static" / "app.js"
        script = app_path.read_text(encoding="utf-8")

        self.assertIn("function drawCellImage(ctx, image, rect, mirrorX = false)", script)
        self.assertIn("drawCellImage(ctx, image, rect, true)", script)
        self.assertIn('drawDetectionOverlay(ctx, cellDefects, rect, image, "#c43c3c", true)', script)
        self.assertIn("ctx.drawImage(mosaicImage, ox, oy, boardW, boardH)", script)
        self.assertNotIn("drawCellImage(ctx, mosaicImage", script)

    def test_real_fixed16_scan_captures_origin_before_first_motion_and_uses_speed(self):
        scan_id = f"unit-real-first-capture-{uuid.uuid4().hex[:8]}"
        plan = main.build_fixed_16_scan_plan(origin_x_mm=0.0, origin_y_mm=0.0)
        plan["motion_speed_mm_s"] = 7.5
        plan["settle_seconds"] = 0.0
        events: list[str] = []

        class FakeWebSocket:
            def __init__(self):
                self.packets = []

            async def send_json(self, packet):
                self.packets.append(packet)

        async def fake_capture(row, col, x, y, scan_id_arg, stop_event):
            events.append(f"capture:{row}:{col}")
            return {
                "ok": True,
                "row": row,
                "col": col,
                "x_mm": x,
                "y_mm": y,
                "raw_image": f"algorithm/outputs/scans/{scan_id_arg}/r{row:03d}_c{col:03d}/raw.jpg",
                "latest_image": "algorithm/outputs/camera/latest.jpg",
                "capture_ms": 1.0,
            }

        async def fake_detect(raw_image, annotated_path, result_json_path, metadata):
            events.append(f"detect:{metadata['row']}:{metadata['col']}")
            return {
                "ok": True,
                "defects": [],
                "result_json": str(result_json_path),
                "raw_image": raw_image,
                "annotated_image": str(annotated_path),
                "inference_ms": 2.0,
                "inference_location": "local_dnn",
                "detection_platform": "local_dnn",
            }

        def fake_move_to_xy(x, y):
            events.append(f"legacy_move:{x}:{y}")
            return {"x_mm": x, "y_mm": y, "z_mm": 0.0}

        def fake_move_to_xy_stream(x, y, speed_mm_s=2.0, *args, **kwargs):
            if float(x) == 0.0 and float(y) == 0.0:
                events.append(f"return_origin:speed={speed_mm_s}")
            else:
                events.append(f"move:{x}:{y}:speed={speed_mm_s}")
            return {
                "x_mm": x,
                "y_mm": y,
                "z_mm": 0.0,
                "motion_telemetry": {
                    "requested_pulses": {"x": 750},
                    "emitted_pulses": {"x": 750},
                    "elapsed_s": 4.0,
                    "achieved_dominant_speed_mm_s": 7.5,
                    "max_scheduler_delay_ms": 1.0,
                },
            }

        original_capture = camera_trigger.capture_frame
        original_detect = main.detect_artifact_phone_first
        original_move = main.motor_controller.move_to_xy
        original_stream = main.motor_controller.move_to_xy_stream
        original_mosaic = camera_trigger.build_scan_mosaic
        try:
            camera_trigger.capture_frame = fake_capture
            main.detect_artifact_phone_first = fake_detect
            main.motor_controller.move_to_xy = fake_move_to_xy
            main.motor_controller.move_to_xy_stream = fake_move_to_xy_stream
            def fake_mosaic(scan_id_arg, run_detection=False):
                events.append("mosaic")
                return {
                    "ok": True,
                    "scan_id": scan_id_arg,
                    "cell_count": 16,
                    "mosaic_raw": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                    "mosaic_annotated": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                    "mosaic_result_json": "algorithm/outputs/scans/unit/mosaic/mosaic_result.json",
                    "defects": [],
                }

            camera_trigger.build_scan_mosaic = fake_mosaic

            websocket = FakeWebSocket()
            asyncio.run(
                main.run_scan(
                    websocket,
                    scan_id,
                    plan,
                    None,
                    "real",
                    "real",
                    "local_dnn",
                    asyncio.Event(),
                )
            )

            self.assertEqual(events[0], "capture:0:0")
            self.assertNotIn("legacy_move:0.0:0.0", events)
            self.assertEqual(sum(1 for event in events if event.startswith("capture:")), 16)
            self.assertIn("mosaic", events)
            self.assertGreater(events.index("mosaic"), max(index for index, event in enumerate(events) if event.startswith("capture:")))
            return_events = [index for index, event in enumerate(events) if event.startswith("return_origin:")]
            self.assertEqual(len(return_events), 1)
            self.assertGreater(return_events[0], max(index for index, event in enumerate(events) if event.startswith("capture:")))
            self.assertLess(return_events[0], events.index("mosaic"))
            move_events = [event for event in events if event.startswith("move:")]
            self.assertTrue(move_events)
            self.assertTrue(all(event.endswith("speed=7.5") for event in move_events))
            motion_packets = [packet for packet in websocket.packets if packet.get("type") == "motion_complete"]
            self.assertTrue(motion_packets)
            self.assertEqual(motion_packets[0]["payload"]["motion_telemetry"]["emitted_pulses"]["x"], 750)
        finally:
            camera_trigger.capture_frame = original_capture
            main.detect_artifact_phone_first = original_detect
            main.motor_controller.move_to_xy = original_move
            main.motor_controller.move_to_xy_stream = original_stream
            camera_trigger.build_scan_mosaic = original_mosaic

    def test_real_scan_speed_update_applies_to_next_motion_segment(self):
        scan_id = f"unit-real-live-speed-{uuid.uuid4().hex[:8]}"
        plan = main.build_fixed_16_scan_plan(origin_x_mm=0.0, origin_y_mm=0.0)
        plan["motion_speed_mm_s"] = 3.0
        plan["settle_seconds"] = 0.0
        move_speeds: list[float] = []

        class FakeWebSocket:
            async def send_json(self, packet):
                pass

        async def fake_capture(row, col, x, y, scan_id_arg, stop_event):
            return {
                "ok": True,
                "row": row,
                "col": col,
                "x_mm": x,
                "y_mm": y,
                "raw_image": f"algorithm/outputs/scans/{scan_id_arg}/r{row:03d}_c{col:03d}/raw.jpg",
                "latest_image": "algorithm/outputs/camera/latest.jpg",
                "capture_ms": 1.0,
            }

        async def fake_detect(raw_image, annotated_path, result_json_path, metadata):
            return {
                "ok": True,
                "defects": [],
                "result_json": str(result_json_path),
                "raw_image": raw_image,
                "annotated_image": str(annotated_path),
                "inference_ms": 1.0,
                "inference_location": "local_dnn",
                "detection_platform": "local_dnn",
            }

        def fake_move_to_xy_stream(x, y, speed_mm_s=2.0, *args, **kwargs):
            if float(x) != 0.0 or float(y) != 0.0:
                move_speeds.append(float(speed_mm_s))
                if len(move_speeds) == 1:
                    self.assertIsNotNone(main.active_real_scan_control)
                    main.active_real_scan_control.update_speed(8.0)
            return {"x_mm": x, "y_mm": y, "z_mm": 0.0}

        original_capture = camera_trigger.capture_frame
        original_detect = main.detect_artifact_phone_first
        original_stream = main.motor_controller.move_to_xy_stream
        original_mosaic = camera_trigger.build_scan_mosaic
        try:
            camera_trigger.capture_frame = fake_capture
            main.detect_artifact_phone_first = fake_detect
            main.motor_controller.move_to_xy_stream = fake_move_to_xy_stream
            camera_trigger.build_scan_mosaic = lambda scan_id_arg, run_detection=False: {
                "ok": True,
                "scan_id": scan_id_arg,
                "cell_count": 16,
                "mosaic_raw": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                "mosaic_annotated": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                "mosaic_result_json": "algorithm/outputs/scans/unit/mosaic/mosaic_result.json",
                "defects": [],
            }

            asyncio.run(
                main.run_scan(
                    FakeWebSocket(),
                    scan_id,
                    plan,
                    None,
                    "real",
                    "real",
                    "local_dnn",
                    asyncio.Event(),
                )
            )

            self.assertGreaterEqual(len(move_speeds), 2)
            self.assertEqual(move_speeds[0], 3.0)
            self.assertTrue(all(speed == 8.0 for speed in move_speeds[1:]))
        finally:
            camera_trigger.capture_frame = original_capture
            main.detect_artifact_phone_first = original_detect
            main.motor_controller.move_to_xy_stream = original_stream
            camera_trigger.build_scan_mosaic = original_mosaic
            main.active_real_scan_control = None

    def test_real_scan_moves_while_previous_detection_is_pending(self):
        scan_id = f"unit-real-detect-queue-{uuid.uuid4().hex[:8]}"
        plan = main.build_fixed_16_scan_plan(origin_x_mm=0.0, origin_y_mm=0.0)
        plan["motion_speed_mm_s"] = 5.0
        plan["settle_seconds"] = 0.0
        events: list[str] = []

        class FakeWebSocket:
            async def send_json(self, packet):
                pass

        async def fake_capture(row, col, x, y, scan_id_arg, stop_event):
            events.append(f"capture:{row}:{col}")
            return {
                "ok": True,
                "row": row,
                "col": col,
                "x_mm": x,
                "y_mm": y,
                "raw_image": f"algorithm/outputs/scans/{scan_id_arg}/r{row:03d}_c{col:03d}/raw.jpg",
                "latest_image": "algorithm/outputs/camera/latest.jpg",
                "capture_ms": 1.0,
            }

        async def fake_detect(raw_image, annotated_path, result_json_path, metadata):
            events.append(f"detect_start:{metadata['row']}:{metadata['col']}")
            await asyncio.sleep(0.05)
            events.append(f"detect_end:{metadata['row']}:{metadata['col']}")
            return {
                "ok": True,
                "defects": [],
                "result_json": str(result_json_path),
                "raw_image": raw_image,
                "annotated_image": str(annotated_path),
                "inference_ms": 50.0,
                "inference_location": "local_dnn",
                "detection_platform": "local_dnn",
            }

        def fake_move_to_xy_stream(x, y, speed_mm_s=2.0, *args, **kwargs):
            events.append(f"move:{x}:{y}:speed={speed_mm_s}")
            return {"x_mm": x, "y_mm": y, "z_mm": 0.0}

        def fake_legacy_move_to_xy(x, y):
            events.append(f"legacy_move:{x}:{y}")
            return {"x_mm": x, "y_mm": y, "z_mm": 0.0}

        original_capture = camera_trigger.capture_frame
        original_detect = main.detect_artifact_phone_first
        original_move = main.motor_controller.move_to_xy
        original_stream = main.motor_controller.move_to_xy_stream
        original_mosaic = camera_trigger.build_scan_mosaic
        try:
            camera_trigger.capture_frame = fake_capture
            main.detect_artifact_phone_first = fake_detect
            main.motor_controller.move_to_xy = fake_legacy_move_to_xy
            main.motor_controller.move_to_xy_stream = fake_move_to_xy_stream
            camera_trigger.build_scan_mosaic = lambda scan_id_arg, run_detection=False: {
                "ok": True,
                "scan_id": scan_id_arg,
                "cell_count": 16,
                "mosaic_raw": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                "mosaic_annotated": "algorithm/outputs/scans/unit/mosaic/mosaic_raw.jpg",
                "mosaic_result_json": "algorithm/outputs/scans/unit/mosaic/mosaic_result.json",
                "defects": [],
            }

            asyncio.run(
                main.run_scan(
                    FakeWebSocket(),
                    scan_id,
                    plan,
                    None,
                    "real",
                    "real",
                    "local_dnn",
                    asyncio.Event(),
                )
            )

            first_detect_end = events.index("detect_end:0:0")
            first_motion = next(index for index, event in enumerate(events) if event.startswith("move:"))
            self.assertLess(first_motion, first_detect_end)
            self.assertEqual(sum(1 for event in events if event.startswith("detect_end:")), 16)
        finally:
            camera_trigger.capture_frame = original_capture
            main.detect_artifact_phone_first = original_detect
            main.motor_controller.move_to_xy = original_move
            main.motor_controller.move_to_xy_stream = original_stream
            camera_trigger.build_scan_mosaic = original_mosaic

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

    def test_fixed_real_scan_plan_uses_teammate_16_points(self):
        plan = main.build_fixed_16_scan_plan(origin_x_mm=0.0, origin_y_mm=0.0)

        self.assertEqual(plan["scan_type"], "fixed_points_16")
        self.assertFalse(plan["pcb_size_known"])
        self.assertEqual(plan["rows"], 4)
        self.assertEqual(plan["cols"], 4)
        self.assertEqual(plan["total_cells"], 16)
        self.assertEqual(plan["image_px"], {"width": 1280, "height": 800})
        self.assertEqual(plan["step_mm"], {"x": 30.0, "y": 18.0})
        self.assertEqual(plan["mosaic_mode"], "physical_fov_crop_stitch")
        self.assertEqual(plan["axis_mapping"], "slide_x_image_x_slide_y_image_y")
        self.assertEqual(plan["fov_mm"]["long"], 39.0)
        self.assertEqual(plan["fov_mm"]["short"], 24.375)
        self.assertEqual(plan["fov_mm"]["width"], 39.0)
        self.assertEqual(plan["fov_mm"]["height"], 24.375)

        cells = main.iter_snake_cells(plan)
        self.assertEqual(len(cells), 16)
        self.assertEqual((cells[0]["row"], cells[0]["col"], cells[0]["x_mm"], cells[0]["y_mm"]), (0, 0, 0.0, 0.0))
        self.assertEqual((cells[1]["row"], cells[1]["col"], cells[1]["x_mm"], cells[1]["y_mm"]), (0, 1, 30.0, 0.0))
        self.assertEqual((cells[3]["row"], cells[3]["col"], cells[3]["x_mm"], cells[3]["y_mm"]), (0, 3, 90.0, 0.0))
        self.assertEqual((cells[4]["row"], cells[4]["col"], cells[4]["x_mm"], cells[4]["y_mm"]), (1, 3, 90.0, 18.0))
        self.assertEqual((cells[7]["row"], cells[7]["col"], cells[7]["x_mm"], cells[7]["y_mm"]), (1, 0, 0.0, 18.0))
        self.assertEqual((cells[-1]["row"], cells[-1]["col"], cells[-1]["x_mm"], cells[-1]["y_mm"]), (3, 0, 0.0, 54.0))
        self.assertEqual(plan["point_order"], "y_major_horizontal_snake_fixed_points")

    def test_fixed_points_direct_crop_reconstructs_exact_four_by_four_source(self):
        scan_id = f"unit-direct-crop-exact-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        old_x = camera_trigger.FIXED_STEP_X_PX
        old_y = camera_trigger.FIXED_STEP_Y_PX
        old_mirror = camera_trigger.STITCH_MIRROR_X

        try:
            camera_trigger.FIXED_STEP_X_PX = 8
            camera_trigger.FIXED_STEP_Y_PX = 6
            camera_trigger.STITCH_MIRROR_X = True
            tile_width = 12
            tile_height = 10
            canvas_width = tile_width + 3 * camera_trigger.FIXED_STEP_X_PX
            canvas_height = tile_height + 3 * camera_trigger.FIXED_STEP_Y_PX
            yy, xx = np.indices((canvas_height, canvas_width))
            source = np.zeros((canvas_height, canvas_width, 3), dtype=np.uint8)
            source[:, :, 0] = (xx * 7 + yy * 3) % 251
            source[:, :, 1] = (xx * 11 + 17) % 251
            source[:, :, 2] = (yy * 13 + 29) % 251

            cells = []
            for row in range(4):
                for col in range(4):
                    raw_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    x = col * camera_trigger.FIXED_STEP_X_PX
                    y = row * camera_trigger.FIXED_STEP_Y_PX
                    tile = source[y : y + tile_height, x : x + tile_width]
                    self.assertTrue(cv2.imwrite(str(raw_path), cv2.flip(tile, 1)))
                    cells.append(
                        {
                            "row": row,
                            "col": col,
                            "x_mm": float(col * 30),
                            "y_mm": float(row * 18),
                            "raw_image": camera_trigger._rel(raw_path),
                        }
                    )

            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 4,
                    "cols": 4,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }

            mosaic, geometry, placements = camera_trigger._build_physical_fov_crop_mosaic_image(manifest)

            self.assertTrue(np.array_equal(mosaic, source))
            self.assertEqual(geometry["mode"], "physical_fov_direct_crop_stitch")
            self.assertEqual(geometry["crop_strategy"], "midpoint_hard_crop")
            self.assertEqual(len(placements), 16)
            self.assertEqual(sum(item["width_px"] * item["height_px"] for item in placements), source.shape[0] * source.shape[1])
            for placement in placements:
                self.assertEqual(placement["mirror_x"], True)
                self.assertIn("source_crop_px", placement)
                self.assertIn("full_origin_px", placement)
                self.assertEqual(placement["full_tile_px"], {"width": tile_width, "height": tile_height})
        finally:
            camera_trigger.FIXED_STEP_X_PX = old_x
            camera_trigger.FIXED_STEP_Y_PX = old_y
            camera_trigger.STITCH_MIRROR_X = old_mirror
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_direct_crop_has_exact_calibrated_canvas_dimensions(self):
        scan_id = f"unit-direct-crop-size-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        old_x = camera_trigger.FIXED_STEP_X_PX
        old_y = camera_trigger.FIXED_STEP_Y_PX

        try:
            camera_trigger.FIXED_STEP_X_PX = 985
            camera_trigger.FIXED_STEP_Y_PX = 591
            cells = []
            for row in range(4):
                for col in range(4):
                    raw_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    image = np.full((800, 1280, 3), row * 16 + col, dtype=np.uint8)
                    self.assertTrue(cv2.imwrite(str(raw_path), image))
                    cells.append(
                        {
                            "row": row,
                            "col": col,
                            "x_mm": float(col * 30),
                            "y_mm": float(row * 18),
                            "raw_image": camera_trigger._rel(raw_path),
                        }
                    )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 4,
                    "cols": 4,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }

            mosaic, geometry, placements = camera_trigger._build_physical_fov_crop_mosaic_image(manifest)

            self.assertEqual(mosaic.shape, (2573, 4235, 3))
            self.assertEqual(geometry["canvas_px"], {"width": 4235, "height": 2573})
            self.assertEqual(geometry["stride_px"], {"slide_x": 985, "slide_y": 591})
            self.assertEqual(geometry["mode"], "physical_fov_direct_crop_stitch")
            self.assertEqual(geometry["crop_strategy"], "midpoint_hard_crop")
            self.assertEqual(len(placements), 16)
            self.assertLess(placements[0]["width_px"], 1280)
        finally:
            camera_trigger.FIXED_STEP_X_PX = old_x
            camera_trigger.FIXED_STEP_Y_PX = old_y
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_missing_expected_cell_raises_without_contact_sheet_fallback(self):
        scan_id = f"unit-direct-crop-missing-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        original_fallback = camera_trigger._build_mosaic_image

        try:
            cells = []
            for row, col in [(0, 0), (0, 1), (1, 0)]:
                raw_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(cv2.imwrite(str(raw_path), np.zeros((20, 30, 3), dtype=np.uint8)))
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "x_mm": float(col * 30),
                        "y_mm": float(row * 18),
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 2,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": cells,
            }
            scan_dir.mkdir(parents=True, exist_ok=True)
            (scan_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            def forbidden_fallback(*args, **kwargs):
                raise AssertionError("fixed_points_16 must not use contact-sheet fallback")

            camera_trigger._build_mosaic_image = forbidden_fallback
            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "missing expected cell"):
                camera_trigger.build_scan_mosaic(scan_id, run_detection=False)
        finally:
            camera_trigger._build_mosaic_image = original_fallback
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_out_of_range_extra_cell_raises(self):
        scan_id = f"unit-direct-crop-extra-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id

        try:
            cells = []
            for row, col in [(0, 0), (1, 0)]:
                raw_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(cv2.imwrite(str(raw_path), np.zeros((20, 30, 3), dtype=np.uint8)))
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "x_mm": 0.0,
                        "y_mm": float(row * 18),
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 1,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": cells,
            }

            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "out-of-range"):
                camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_unreadable_cell_raises(self):
        scan_id = f"unit-direct-crop-unreadable-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id

        try:
            valid_path = scan_dir / "r000_c000" / "raw.png"
            invalid_path = scan_dir / "r000_c001" / "raw.png"
            valid_path.parent.mkdir(parents=True, exist_ok=True)
            invalid_path.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(cv2.imwrite(str(valid_path), np.zeros((20, 30, 3), dtype=np.uint8)))
            invalid_path.write_bytes(b"not an image")
            manifest = {
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(valid_path),
                    },
                    {
                        "row": 0,
                        "col": 1,
                        "x_mm": 30.0,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(invalid_path),
                    },
                ],
            }

            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "Could not read raw image"):
                camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_mismatched_cell_dimensions_raise_instead_of_resizing(self):
        scan_id = f"unit-direct-crop-mismatch-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id

        try:
            cells = []
            for col, width in [(0, 30), (1, 31)]:
                raw_path = scan_dir / f"r000_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(cv2.imwrite(str(raw_path), np.zeros((20, width, 3), dtype=np.uint8)))
                cells.append(
                    {
                        "row": 0,
                        "col": col,
                        "x_mm": float(col * 30),
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": cells,
            }

            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "dimensions do not match"):
                camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_rejects_invalid_numeric_inputs_before_decoding_images(self):
        scan_id = f"unit-direct-crop-invalid-numeric-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        original_import_cv2 = camera_trigger._import_cv2

        class DecodeMustNotRun:
            @staticmethod
            def imread(path):
                raise AssertionError(f"image decoding must not run for invalid numeric input: {path}")

        try:
            raw_path = scan_dir / "r000_c000" / "raw.png"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(b"placeholder")
            base_manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 1,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                ],
            }
            invalid_cases = [
                ("nan_step", lambda item: item["plan"]["step_mm"].update({"x": float("nan")})),
                ("zero_step", lambda item: item["plan"]["step_mm"].update({"x": 0.0})),
                ("negative_step", lambda item: item["plan"]["step_mm"].update({"y": -1.0})),
                ("huge_step", lambda item: item["plan"]["step_mm"].update({"x": 1.0e20})),
                ("infinite_coordinate", lambda item: item["cells"][0].update({"x_mm": float("inf")})),
                ("nan_origin", lambda item: item["plan"]["origin_mm"].update({"y": float("nan")})),
            ]
            camera_trigger._import_cv2 = lambda: DecodeMustNotRun()

            for label, mutate in invalid_cases:
                with self.subTest(label=label):
                    manifest = json.loads(json.dumps(base_manifest))
                    mutate(manifest)
                    try:
                        camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
                    except camera_trigger.CameraTriggerError:
                        continue
                    except Exception as exc:
                        self.fail(f"invalid numeric input leaked {type(exc).__name__}: {exc}")
                    self.fail("invalid numeric input did not raise CameraTriggerError")
        finally:
            camera_trigger._import_cv2 = original_import_cv2
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_rejects_inconsistent_grid_coordinates_before_decoding_images(self):
        scan_id = f"unit-direct-crop-grid-coordinate-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        original_import_cv2 = camera_trigger._import_cv2

        class DecodeMustNotRun:
            @staticmethod
            def imread(path):
                raise AssertionError(f"image decoding must not run for inconsistent coordinates: {path}")

        try:
            cells = []
            for col, x_mm in [(0, 0.0), (1, 30.25)]:
                raw_path = scan_dir / f"r000_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(b"placeholder")
                cells.append(
                    {
                        "row": 0,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": cells,
            }
            camera_trigger._import_cv2 = lambda: DecodeMustNotRun()

            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "does not match fixed grid"):
                camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
        finally:
            camera_trigger._import_cv2 = original_import_cv2
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_rejects_stride_without_positive_overlap(self):
        scan_id = f"unit-direct-crop-no-overlap-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        old_x = camera_trigger.FIXED_STEP_X_PX
        old_y = camera_trigger.FIXED_STEP_Y_PX

        try:
            camera_trigger.FIXED_STEP_X_PX = 30
            camera_trigger.FIXED_STEP_Y_PX = 10
            cells = []
            for col in range(2):
                raw_path = scan_dir / f"r000_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                self.assertTrue(cv2.imwrite(str(raw_path), np.zeros((20, 30, 3), dtype=np.uint8)))
                cells.append(
                    {
                        "row": 0,
                        "col": col,
                        "x_mm": float(col * 30),
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }

            with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "positive overlap"):
                camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
        finally:
            camera_trigger.FIXED_STEP_X_PX = old_x
            camera_trigger.FIXED_STEP_Y_PX = old_y
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_decodes_each_source_in_two_streaming_passes(self):
        scan_id = f"unit-direct-crop-streaming-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        original_import_cv2 = camera_trigger._import_cv2
        live_images = []
        read_count = 0
        max_live_before_read = 0

        class TrackingCv2:
            @staticmethod
            def imread(path):
                nonlocal read_count, max_live_before_read
                gc.collect()
                max_live_before_read = max(max_live_before_read, sum(ref() is not None for ref in live_images))
                image = np.zeros((20, 30, 3), dtype=np.uint8)
                live_images.append(weakref.ref(image))
                read_count += 1
                return image

            @staticmethod
            def flip(image, axis):
                return np.flip(image, axis=1).copy()

        try:
            cells = []
            for row in range(2):
                for col in range(2):
                    raw_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_bytes(b"placeholder")
                    cells.append(
                        {
                            "row": row,
                            "col": col,
                            "x_mm": float(col * 30),
                            "y_mm": float(row * 18),
                            "raw_image": camera_trigger._rel(raw_path),
                        }
                    )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 2,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }
            camera_trigger._import_cv2 = lambda: TrackingCv2()

            camera_trigger._build_physical_fov_crop_mosaic_image(manifest)

            self.assertEqual(read_count, 8)
            self.assertLessEqual(max_live_before_read, 1)
        finally:
            camera_trigger._import_cv2 = original_import_cv2
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_build_skips_board_crop_and_final_mosaic_inference(self):
        scan_id = f"unit-direct-crop-no-final-detect-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        original_board_crop = camera_trigger._detect_board_crop
        original_tile_detection = camera_trigger._detect_mosaic_tiles
        original_frame_detection = camera_trigger.yolo_detector.detect_frame

        try:
            raw_path = scan_dir / "r000_c000" / "raw.png"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(cv2.imwrite(str(raw_path), np.full((20, 30, 3), 90, dtype=np.uint8)))
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 1,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                        "defects": [
                            {
                                "type": "short_circuit",
                                "label": "short_circuit",
                                "bbox": [5, 4, 15, 14],
                                "confidence": 0.9,
                            }
                        ],
                    }
                ],
            }
            (scan_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            def forbidden(*args, **kwargs):
                raise AssertionError("fixed_points_16 must not crop or infer on the final mosaic")

            camera_trigger._detect_board_crop = forbidden
            camera_trigger._detect_mosaic_tiles = forbidden
            camera_trigger.yolo_detector.detect_frame = forbidden
            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=True)

            self.assertEqual(Path(result["mosaic_raw"]).name, "mosaic_raw.jpg")
            self.assertEqual(Path(result["mosaic_annotated"]).name, "mosaic_annotated.jpg")
            self.assertEqual(Path(result["mosaic_result_json"]).name, "mosaic_result.json")
            self.assertEqual(Path(result["mosaic_manifest"]).name, "mosaic_manifest.json")
            self.assertEqual(result["board_crop_status"], "not_used")
            self.assertIsNone(result["board_crop_raw"])
            self.assertIsNone(result["board_crop_annotated"])
            self.assertIsNone(result["board_crop_metadata"])
            self.assertEqual(len(result["defects"]), 1)
            mosaic_dir = scan_dir / "mosaic"
            self.assertFalse((mosaic_dir / "board_crop_raw.jpg").exists())
            self.assertFalse((mosaic_dir / "board_crop_annotated.jpg").exists())
            self.assertFalse((mosaic_dir / "board_crop_metadata.json").exists())
            output_manifest = json.loads((mosaic_dir / "mosaic_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(output_manifest["geometry"]["mode"], "physical_fov_weighted_blend_stitch")
            self.assertEqual(output_manifest["geometry"]["crop_strategy"], "feather_weighted_overlap")
            self.assertEqual(output_manifest["board_crop"], {"status": "not_used"})
        finally:
            camera_trigger._detect_board_crop = original_board_crop
            camera_trigger._detect_mosaic_tiles = original_tile_detection
            camera_trigger.yolo_detector.detect_frame = original_frame_detection
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_fixed_points_mirror_bbox_mapping_merges_duplicate_across_hard_seam(self):
        scan_id = f"unit-direct-crop-seam-defect-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        old_x = camera_trigger.FIXED_STEP_X_PX
        old_y = camera_trigger.FIXED_STEP_Y_PX
        old_mirror = camera_trigger.STITCH_MIRROR_X

        try:
            camera_trigger.FIXED_STEP_X_PX = 60
            camera_trigger.FIXED_STEP_Y_PX = 10
            camera_trigger.STITCH_MIRROR_X = True
            base = np.zeros((20, 160, 3), dtype=np.uint8)
            cells = []
            source_boxes = ([15, 2, 25, 12], [75, 2, 85, 12])
            for col in range(2):
                raw_path = scan_dir / f"r000_c{col:03d}" / "raw.png"
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                tile = base[:, col * 60 : col * 60 + 100]
                self.assertTrue(cv2.imwrite(str(raw_path), cv2.flip(tile, 1)))
                cells.append(
                    {
                        "row": 0,
                        "col": col,
                        "x_mm": float(col * 30),
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                        "defects": [
                            {
                                "type": "short_circuit",
                                "label": "short_circuit",
                                "bbox": list(source_boxes[col]),
                                "confidence": 0.9 - col * 0.01,
                            }
                        ],
                    }
                )
            manifest = {
                "scan_id": scan_id,
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }

            _, _, placements = camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
            self.assertEqual(placements[0]["source_crop_px"], [0, 0, 80, 20])
            self.assertEqual(placements[0]["x_px"], 0)
            self.assertEqual(placements[1]["source_crop_px"], [20, 0, 80, 20])
            self.assertEqual(placements[1]["x_px"], 80)

            fusion = camera_trigger._fuse_cell_detections_for_mosaic(
                manifest,
                placements,
                {"status": "not_used"},
            )

            self.assertEqual(len(fusion["detections"]), 1)
            detection = fusion["detections"][0]
            self.assertEqual(detection["bbox"], [75, 2, 85, 12])
            self.assertEqual(detection["mosaic_bbox"], [75, 2, 85, 12])
            self.assertEqual(detection["support_count"], 2)
            self.assertEqual(detection["source_mirror_x"], True)
        finally:
            camera_trigger.FIXED_STEP_X_PX = old_x
            camera_trigger.FIXED_STEP_Y_PX = old_y
            camera_trigger.STITCH_MIRROR_X = old_mirror
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_physical_fov_mosaic_uses_manual_pixel_stride_overrides(self):
        scan_id = f"unit-physical-manual-step-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)
        old_x = camera_trigger.FIXED_STEP_X_PX
        old_y = camera_trigger.FIXED_STEP_Y_PX

        try:
            camera_trigger.FIXED_STEP_X_PX = 52
            camera_trigger.FIXED_STEP_Y_PX = 59
            cells = []
            for row, col, x_mm, y_mm in [
                (0, 0, 0.0, 0.0),
                (0, 1, 30.0, 0.0),
                (1, 0, 0.0, 18.0),
                (1, 1, 30.0, 18.0),
            ]:
                cell_dir = scan_dir / f"r{row:03d}_c{col:03d}"
                cell_dir.mkdir(parents=True, exist_ok=True)
                raw_path = cell_dir / "raw.png"
                image = np.full((80, 128, 3), 40 + row * 60 + col * 30, dtype=np.uint8)
                cv2.imwrite(str(raw_path), image)
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "raw_image": camera_trigger._rel(raw_path),
                    }
                )

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 2,
                    "cols": 2,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "image_px": {"width": 128, "height": 80},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }

            mosaic, geometry, placed = camera_trigger._build_physical_fov_crop_mosaic_image(manifest)

            self.assertEqual(geometry["estimated_step_px"], {"x": 52, "y": 59})
            self.assertEqual(geometry["estimated_step_vectors_px"]["x"], {"dx": 52, "dy": 0})
            self.assertEqual(geometry["estimated_step_vectors_px"]["y"], {"dx": 0, "dy": 59})
            self.assertEqual(geometry["step_px_source"], "manual_env")
            self.assertEqual(len(placed), 4)
            self.assertEqual(mosaic.shape[:2], (139, 180))
        finally:
            camera_trigger.FIXED_STEP_X_PX = old_x
            camera_trigger.FIXED_STEP_Y_PX = old_y
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_physical_fov_mosaic_mirrors_cell_images_and_mapped_boxes(self):
        scan_id = f"unit-physical-mirror-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            cell_dir = scan_dir / "r000_c000"
            cell_dir.mkdir(parents=True, exist_ok=True)
            raw_path = cell_dir / "raw.png"
            image = np.zeros((20, 100, 3), dtype=np.uint8)
            image[:, :50] = (20, 40, 200)
            image[:, 50:] = (220, 60, 20)
            cv2.imwrite(str(raw_path), image)
            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "rows": 1,
                    "cols": 1,
                    "step_mm": {"x": 30.0, "y": 18.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "image_px": {"width": 100, "height": 20},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": [
                    {
                        "row": 0,
                        "col": 0,
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "raw_image": camera_trigger._rel(raw_path),
                        "defects": [
                            {
                                "type": "short_circuit",
                                "label": "short_circuit",
                                "bbox": [10, 2, 30, 12],
                                "confidence": 0.81,
                            }
                        ],
                    }
                ],
            }

            mosaic, geometry, placed = camera_trigger._build_physical_fov_crop_mosaic_image(manifest)
            self.assertEqual(geometry["cell_image_transform"], {"mirror_x": True})
            expected_mosaic = cv2.flip(image, 1)
            self.assertLessEqual(int(cv2.absdiff(mosaic, expected_mosaic).max()), 1)
            self.assertEqual(mosaic[0, 0].tolist(), image[0, -1].tolist())
            self.assertEqual(placed[0]["mirror_x"], True)

            fusion = camera_trigger._fuse_cell_detections_for_mosaic(
                manifest,
                placed,
                {"status": "not_found"},
            )

            self.assertEqual(len(fusion["detections"]), 1)
            detection = fusion["detections"][0]
            self.assertEqual(detection["bbox"], [70, 2, 90, 12])
            self.assertEqual(detection["mosaic_bbox"], [70, 2, 90, 12])
            self.assertEqual(detection["original_bbox"], [10, 2, 30, 12])
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_real_scan_settle_seconds_can_be_overridden_from_payload(self):
        plan = main.build_fixed_16_scan_plan(origin_x_mm=0.0, origin_y_mm=0.0)
        plan["settle_seconds"] = main.normalize_settle_seconds(0.12)

        self.assertEqual(plan["settle_seconds"], 0.12)
        self.assertEqual(main.normalize_settle_seconds("0.03"), 0.03)
        with self.assertRaises(ValueError):
            main.normalize_settle_seconds("-0.1")

    def test_physical_fov_mosaic_uses_calibrated_slide_steps(self):
        scan_id = f"unit-fixed-physical-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            tile_width = 390
            tile_height = 244
            step_x_px = 160
            step_y_px = 240
            canvas_width = tile_width + step_x_px
            canvas_height = tile_height + step_y_px
            yy, xx = np.indices((canvas_height, canvas_width))
            base = np.zeros((canvas_height, canvas_width, 3), dtype=np.uint8)
            base[:, :, 0] = (xx * 7 + yy * 3) % 251
            base[:, :, 1] = (xx * 5 + 40) % 251
            base[:, :, 2] = (yy * 11 + 80) % 251

            cells = []
            for row, col, x_mm, y_mm in [
                (0, 0, 0.0, 0.0),
                (1, 0, 0.0, 24.0),
                (0, 1, 16.0, 0.0),
                (1, 1, 16.0, 24.0),
            ]:
                cell_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                cell_path.parent.mkdir(parents=True, exist_ok=True)
                x_px = col * step_x_px
                y_px = row * step_y_px
                tile = base[y_px : y_px + tile_height, x_px : x_px + tile_width]
                cv2.imwrite(str(cell_path), cv2.flip(tile, 1))
                cells.append(
                    {
                        "row": row,
                        "col": col,
                        "x_mm": x_mm,
                        "y_mm": y_mm,
                        "raw_image": camera_trigger._rel(cell_path),
                    }
                )

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "pcb_size_known": False,
                    "rows": 2,
                    "cols": 2,
                    "step_mm": {"x": 16.0, "y": 24.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "image_px": {"width": tile_width, "height": tile_height},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "axis_mapping": "slide_x_image_x_slide_y_image_y",
                },
                "cells": cells,
            }
            manifest_path = scan_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["geometry"]["mode"], "physical_fov_weighted_blend_stitch")
            self.assertEqual(result["geometry"]["crop_strategy"], "feather_weighted_overlap")
            self.assertEqual(result["geometry"]["estimated_step_px"], {"x": step_x_px, "y": step_y_px})
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["x"], {"dx": step_x_px, "dy": 0})
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["y"], {"dx": 0, "dy": step_y_px})
            self.assertEqual(result["geometry"]["axis_mapping"], "slide_x_image_x_slide_y_image_y")
            self.assertEqual(result["geometry"]["overlap_px"], {"image_x": tile_width - step_x_px, "image_y": tile_height - step_y_px})
            self.assertAlmostEqual(result["geometry"]["px_per_mm"], 10.0, places=4)
            self.assertEqual(result["geometry"]["fov_mm"]["long"], 39.0)
            self.assertAlmostEqual(result["geometry"]["fov_mm"]["short"], 24.4, places=4)
            mosaic = cv2.imread(str(camera_trigger.resolve_artifact(result["mosaic_raw"])))
            self.assertEqual(mosaic.shape[:2], (canvas_height, canvas_width))
            self.assertLess(float(np.mean(cv2.absdiff(mosaic, base))), 10.0)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_auto_crop_mosaic_restores_real_val_image_from_sixteen_overlapping_tiles(self):
        val_image = (
            camera_trigger.ROOT.parent.parent
            / "pcb_defects_training_package_20260704_035505"
            / "datasets"
            / "pcb_defects"
            / "images"
            / "val"
            / "136_png_jpg.rf.8b766410155d072086a106d12f7096b6.jpg"
        )
        if not val_image.exists():
            self.skipTest(f"val image not available: {val_image}")

        scan_id = f"unit-fixed-val16-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            source = cv2.imread(str(val_image))
            self.assertIsNotNone(source)
            tile_width = 390
            tile_height = 244
            step_x_px = 160
            step_y_px = 240
            canvas_width = tile_width + 3 * step_x_px
            canvas_height = tile_height + 3 * step_y_px
            base = cv2.resize(source, (canvas_width, canvas_height), interpolation=cv2.INTER_AREA)

            cells = []
            for row in range(4):
                for col in range(4):
                    cell_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                    cell_path.parent.mkdir(parents=True, exist_ok=True)
                    x_px = col * step_x_px
                    y_px = row * step_y_px
                    tile = base[y_px : y_px + tile_height, x_px : x_px + tile_width]
                    cv2.imwrite(str(cell_path), cv2.flip(tile, 1))
                    cells.append(
                        {
                            "row": row,
                            "col": col,
                            "x_mm": float(col * 16.0),
                            "y_mm": float(row * 24.0),
                            "raw_image": camera_trigger._rel(cell_path),
                        }
                    )

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "pcb_size_known": False,
                    "rows": 4,
                    "cols": 4,
                    "step_mm": {"x": 16.0, "y": 24.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "image_px": {"width": tile_width, "height": tile_height},
                    "mosaic_mode": "physical_fov_crop_stitch",
                },
                "cells": cells,
            }
            manifest_path = scan_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["geometry"]["mode"], "physical_fov_weighted_blend_stitch")
            self.assertEqual(result["geometry"]["estimated_step_px"], {"x": step_x_px, "y": step_y_px})
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["x"], {"dx": step_x_px, "dy": 0})
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["y"], {"dx": 0, "dy": step_y_px})
            self.assertEqual(result["annotated_frame_count"], 16)
            archive_path = camera_trigger.resolve_artifact(result["annotated_frames_zip"])
            self.assertTrue(archive_path.exists())
            with zipfile.ZipFile(archive_path) as archive:
                names = archive.namelist()
            frame_names = [name for name in names if name.startswith("frame_")]
            self.assertEqual(len(frame_names), 16)
            self.assertIn("frames_manifest.json", names)
            self.assertTrue(frame_names[0].startswith("frame_001_r000_c000_annotated"))
            self.assertTrue(frame_names[4].startswith("frame_005_r001_c000_annotated"))
            mosaic = cv2.imread(str(camera_trigger.resolve_artifact(result["mosaic_raw"])))
            self.assertEqual(mosaic.shape, base.shape)
            self.assertLess(float(np.mean(cv2.absdiff(mosaic, base))), 14.0)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_auto_crop_mosaic_handles_camera_axes_swapped_against_slide_axes(self):
        scan_id = f"unit-fixed-axis-swap-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        try:
            tile_width = 390
            tile_height = 390
            x_axis_vector = (0, 160)
            y_axis_vector = (240, 0)
            canvas_width = tile_width + 3 * y_axis_vector[0] + 3 * x_axis_vector[0]
            canvas_height = tile_height + 3 * y_axis_vector[1] + 3 * x_axis_vector[1]
            yy, xx = np.indices((canvas_height, canvas_width))
            base = np.zeros((canvas_height, canvas_width, 3), dtype=np.uint8)
            base[:, :, 0] = (xx * 9 + yy * 2) % 251
            base[:, :, 1] = (xx * 4 + yy * 7 + 31) % 251
            base[:, :, 2] = (xx * 5 + yy * 11 + 79) % 251
            cv2.circle(base, (230, 180), 37, (240, 45, 160), -1)
            cv2.rectangle(base, (420, 390), (500, 470), (30, 220, 210), -1)

            cells = []
            order = 0
            for col in range(4):
                rows = range(4) if col % 2 == 0 else range(3, -1, -1)
                for row in rows:
                    order += 1
                    x_px = row * y_axis_vector[0] + col * x_axis_vector[0]
                    y_px = row * y_axis_vector[1] + col * x_axis_vector[1]
                    cell_path = scan_dir / f"r{row:03d}_c{col:03d}" / "raw.png"
                    annotated_path = scan_dir / f"r{row:03d}_c{col:03d}" / "annotated.png"
                    cell_path.parent.mkdir(parents=True, exist_ok=True)
                    tile = base[y_px : y_px + tile_height, x_px : x_px + tile_width]
                    cv2.imwrite(str(cell_path), cv2.flip(tile, 1))
                    cv2.imwrite(str(annotated_path), tile)
                    cells.append(
                        {
                            "order": order,
                            "row": row,
                            "col": col,
                            "x_mm": float(col * 16.0),
                            "y_mm": float(row * 24.0),
                            "raw_image": camera_trigger._rel(cell_path),
                            "annotated_image": camera_trigger._rel(annotated_path),
                            "defects": [],
                        }
                    )

            manifest = {
                "scan_id": scan_id,
                "status": "complete",
                "operation_mode": "real",
                "plan": {
                    "scan_type": "fixed_points_16",
                    "pcb_size_known": False,
                    "rows": 4,
                    "cols": 4,
                    "step_mm": {"x": 16.0, "y": 24.0},
                    "origin_mm": {"x": 0.0, "y": 0.0},
                    "image_px": {"width": tile_width, "height": tile_height},
                    "mosaic_mode": "physical_fov_crop_stitch",
                    "point_order": "x_major_vertical_snake_fixed_points",
                    "axis_mapping": "slide_y_image_x_slide_x_image_y",
                },
                "cells": cells,
            }
            manifest_path = scan_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = camera_trigger.build_scan_mosaic(scan_id, run_detection=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["geometry"]["mode"], "physical_fov_weighted_blend_stitch")
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["x"], {"dx": 0, "dy": 160})
            self.assertEqual(result["geometry"]["estimated_step_vectors_px"]["y"], {"dx": 240, "dy": 0})
            mosaic = cv2.imread(str(camera_trigger.resolve_artifact(result["mosaic_raw"])))
            self.assertEqual(mosaic.shape, base.shape)
            self.assertLess(float(np.mean(cv2.absdiff(mosaic, base))), 12.0)
        finally:
            if scan_dir.exists():
                remove_tree_retry(scan_dir)

    def test_detect_board_crop_returns_clear_rectangular_board_region(self):
        image = np.full((90, 130, 3), 24, dtype=np.uint8)
        image[22:72, 31:104] = 155

        crop = camera_trigger._detect_board_crop(image)

        self.assertEqual(crop["status"], "ok")
        x, y, width, height = crop["bbox_px"]
        self.assertLessEqual(abs(x - 31), 4)
        self.assertLessEqual(abs(y - 22), 4)
        self.assertLessEqual(abs(width - 73), 8)
        self.assertLessEqual(abs(height - 50), 8)

    def test_cell_mosaic_fusion_maps_crop_coordinates_and_marks_conflicts(self):
        manifest = {
            "cells": [
                {
                    "row": 0,
                    "col": 0,
                    "x_mm": 0.0,
                    "y_mm": 0.0,
                    "raw_image": "cell0/raw.jpg",
                    "defects": [
                        {"type": "short_circuit", "label": "短路", "bbox": [5, 5, 15, 15], "confidence": 0.62},
                    ],
                },
                {
                    "row": 0,
                    "col": 1,
                    "x_mm": 16.0,
                    "y_mm": 0.0,
                    "raw_image": "cell1/raw.jpg",
                    "defects": [
                        {"type": "short_circuit", "label": "短路", "bbox": [2, 5, 12, 15], "confidence": 0.58},
                        {"type": "excess_solder", "label": "锡多", "bbox": [2, 5, 12, 15], "confidence": 0.55},
                    ],
                },
                {
                    "row": 0,
                    "col": 2,
                    "x_mm": 32.0,
                    "y_mm": 0.0,
                    "raw_image": "cell2/raw.jpg",
                    "defects": [
                        {"type": "pcb_damage", "label": "板损", "bbox": [0, 0, 6, 6], "confidence": 0.9},
                    ],
                },
            ]
        }
        placed_cells = [
            {"row": 0, "col": 0, "x_px": 0, "y_px": 0, "width_px": 20, "height_px": 20, "source_crop_px": [0, 0, 20, 20]},
            {"row": 0, "col": 1, "x_px": 3, "y_px": 0, "width_px": 20, "height_px": 20, "source_crop_px": [0, 0, 20, 20]},
            {"row": 0, "col": 2, "x_px": 50, "y_px": 0, "width_px": 10, "height_px": 20, "source_crop_px": [10, 0, 10, 20]},
        ]
        board_crop = {"status": "ok", "bbox_px": [2, 2, 18, 18]}

        fusion = camera_trigger._fuse_cell_detections_for_mosaic(manifest, placed_cells, board_crop)

        self.assertEqual(fusion["audit"]["coordinate_space"], "board_crop")
        self.assertEqual(len(fusion["detections"]), 1)
        detection = fusion["detections"][0]
        self.assertEqual(detection["type"], "short_circuit")
        self.assertEqual(detection["bbox"], [3, 3, 13, 13])
        self.assertEqual(detection["mosaic_bbox"], [5, 5, 15, 15])
        self.assertEqual(detection["support_count"], 2)
        self.assertTrue(detection["has_conflict"])
        self.assertEqual(detection["conflict_alternatives"][0]["type"], "excess_solder")
        discarded_reasons = {item.get("reason") for item in fusion["audit"]["discarded_defects"]}
        self.assertIn("outside_retained_source_crop", discarded_reasons)

    def test_non_direct_fusion_with_full_origins_preserves_best_detection_bbox(self):
        manifest = {
            "cells": [
                {
                    "row": 0,
                    "col": 0,
                    "raw_image": "cell0/raw.jpg",
                    "defects": [
                        {"type": "short_circuit", "bbox": [5, 5, 15, 15], "confidence": 0.9},
                    ],
                },
                {
                    "row": 0,
                    "col": 1,
                    "raw_image": "cell1/raw.jpg",
                    "defects": [
                        {"type": "short_circuit", "bbox": [3, 5, 13, 15], "confidence": 0.8},
                    ],
                },
            ]
        }
        placed_cells = [
            {
                "row": 0,
                "col": 0,
                "x_px": 0,
                "y_px": 0,
                "width_px": 20,
                "height_px": 20,
                "source_crop_px": [0, 0, 20, 20],
                "full_origin_px": {"x": 0, "y": 0},
                "full_tile_px": {"width": 20, "height": 20},
            },
            {
                "row": 0,
                "col": 1,
                "x_px": 3,
                "y_px": 0,
                "width_px": 20,
                "height_px": 20,
                "source_crop_px": [0, 0, 20, 20],
                "full_origin_px": {"x": 3, "y": 0},
                "full_tile_px": {"width": 20, "height": 20},
            },
        ]

        fusion = camera_trigger._fuse_cell_detections_for_mosaic(
            manifest,
            placed_cells,
            {"status": "not_found"},
        )

        self.assertEqual(len(fusion["detections"]), 1)
        self.assertEqual(fusion["detections"][0]["bbox"], [5, 5, 15, 15])
        self.assertEqual(fusion["detections"][0]["mosaic_bbox"], [5, 5, 15, 15])

    def test_direct_crop_fusion_does_not_single_link_three_box_chain(self):
        manifest = {
            "cells": [
                {
                    "row": 0,
                    "col": index,
                    "raw_image": f"cell{index}/raw.jpg",
                    "defects": [
                        {
                            "type": "short_circuit",
                            "bbox": bbox,
                            "confidence": confidence,
                        }
                    ],
                }
                for index, (bbox, confidence) in enumerate(
                    [
                        ([0, 2, 10, 12], 0.9),
                        ([3, 2, 13, 12], 0.8),
                        ([6, 2, 16, 12], 0.7),
                    ]
                )
            ]
        }
        placed_cells = [
            {
                "row": 0,
                "col": col,
                "x_px": 0,
                "y_px": 0,
                "width_px": 20,
                "height_px": 20,
                "source_crop_px": [0, 0, 20, 20],
                "full_origin_px": {"x": 0, "y": 0},
                "full_tile_px": {"width": 20, "height": 20},
                "mirror_x": False,
                "direct_crop_stitch": True,
            }
            for col in range(3)
        ]

        fusion = camera_trigger._fuse_cell_detections_for_mosaic(
            manifest,
            placed_cells,
            {"status": "not_used"},
        )

        self.assertEqual(len(fusion["detections"]), 2)
        self.assertEqual([item["bbox"] for item in fusion["detections"]], [[0, 2, 10, 12], [6, 2, 16, 12]])
        self.assertEqual([item["mosaic_bbox"] for item in fusion["detections"]], [[0, 2, 10, 12], [6, 2, 16, 12]])
        self.assertEqual(fusion["detections"][0]["original_bbox"], [0, 2, 10, 12])

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

    def test_phone_upload_detection_uses_precision_profile_by_default(self):
        upload_id = f"unit-phone-default-precision-profile-{uuid.uuid4().hex[:8]}"
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
                "name": "default-precision.jpg",
                "raw_image": camera_trigger._rel(raw_path),
                "status": "uploaded",
            }
            camera_trigger._write_phone_metadata(metadata)

            detector = FakeDetector()
            camera_trigger.yolo_detector = detector
            result = camera_trigger.detect_phone_upload(upload_id)

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

    def test_run_phone_detection_does_not_require_phone_worker_for_link_simulation(self):
        upload_id = f"unit-phone-worker-unavailable-{uuid.uuid4().hex[:8]}"
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id

        class FakeWebSocket:
            def __init__(self):
                self.packets = []

            async def send_json(self, packet):
                self.packets.append(packet)

        class FakeHub:
            def status(self):
                return {"ready": False, "worker_count": 0}

        original_get_upload = camera_trigger.get_phone_upload
        original_profile_config = camera_trigger.phone_detection_profile_config
        original_hub = main.phone_inference_hub
        original_detect = main.detect_phone_upload_phone_first
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            raw_path = upload_dir / "raw.jpg"
            cv2.imwrite(str(raw_path), np.full((20, 20, 3), 70, dtype=np.uint8))
            camera_trigger.get_phone_upload = lambda upload_id: {
                "ok": True,
                "upload_id": upload_id,
                "name": "worker-unavailable.jpg",
                "raw_image": camera_trigger._rel(raw_path),
            }
            camera_trigger.phone_detection_profile_config = lambda profile=None: {
                "profile": "fast",
                "label": "快速模式",
                "tile_sizes": (640,),
                "imgszs": (640,),
                "overlap": 0.0,
            }
            main.phone_inference_hub = FakeHub()

            async def fake_detect(upload_id_arg, detection_profile, detection_platform, experiment_profile):
                return {
                    "ok": True,
                    "upload_id": upload_id_arg,
                    "operation_mode": "phone",
                    "detection_profile": detection_profile,
                    "detection_profile_label": "快速模式",
                    "detection_platform": "huawei_cloud",
                    "executed_platform": "huawei_cloud",
                    "experiment_profile": experiment_profile,
                    "defects": [],
                    "raw_image": camera_trigger._rel(raw_path),
                    "annotated_image": camera_trigger._rel(raw_path),
                    "result_json": camera_trigger._rel(upload_dir / "result.json"),
                    "inference_ms": 10.0,
                    "total_elapsed_ms": 1260.0,
                }

            main.detect_phone_upload_phone_first = fake_detect

            websocket = FakeWebSocket()
            asyncio.run(
                main.run_phone_detection(
                    websocket,
                    "scan-phone-error",
                    upload_id,
                    asyncio.Event(),
                    detection_platform="phone_browser",
                    phone_experiment_profile="phone_link_sim",
                )
            )

            error_packets = [packet for packet in websocket.packets if packet["type"] == "phone_error"]
            self.assertEqual(error_packets, [])
            result_packets = [packet for packet in websocket.packets if packet["type"] == "phone_result"]
            self.assertEqual(len(result_packets), 1)
            payload = result_packets[0]["payload"]
            self.assertEqual(payload["operation_mode"], "phone")
            self.assertEqual(payload["detection_platform"], "huawei_cloud")
            self.assertEqual(payload["experiment_profile"], "phone_link_sim")
        finally:
            camera_trigger.get_phone_upload = original_get_upload
            camera_trigger.phone_detection_profile_config = original_profile_config
            main.phone_inference_hub = original_hub
            main.detect_phone_upload_phone_first = original_detect
            if upload_dir.exists():
                remove_tree_retry(upload_dir)

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

    def test_user_selected_phone_browser_uses_phone_worker_result(self):
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
                    {
                        "operation_mode": "real",
                        "row": 0,
                        "col": 0,
                        "detection_platform": "phone_browser",
                    },
                )
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["inference_location"], "phone_browser")
            self.assertEqual(result["detection_platform"], "phone_browser")
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

    def test_user_selected_phone_browser_returns_error_when_no_worker(self):
        scan_id = f"unit-phone-worker-fallback-{uuid.uuid4().hex[:8]}"
        scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
        if scan_dir.exists():
            remove_tree_retry(scan_dir)

        class FakeHub:
            def status(self):
                return {"ready": False, "worker_count": 0}

            async def detect_artifact(self, raw_image, metadata=None):
                raise main.PhoneInferenceUnavailable("no worker")

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
                    {
                        "operation_mode": "simulation",
                        "row": 0,
                        "col": 0,
                        "detection_platform": "phone_browser",
                    },
                )
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "detection_platform_failed")
            self.assertEqual(result["detection_platform"], "phone_browser")
            self.assertEqual(result["inference_location"], "phone_browser")
            self.assertIn("no_phone_worker_online", result["message"])
            self.assertFalse(annotated_path.exists())
            self.assertFalse(result_json_path.exists())
        finally:
            main.phone_inference_hub = original_hub
            if scan_dir.exists():
                remove_tree_retry(scan_dir)


if __name__ == "__main__":
    unittest.main()
