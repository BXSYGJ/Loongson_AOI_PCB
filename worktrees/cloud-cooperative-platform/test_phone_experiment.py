import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

import camera_trigger
import main


class PhoneExperimentTests(unittest.TestCase):
    def test_board_parameters_only_show_in_simulation_and_frontends_default_precision(self):
        html = (main.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        script = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
        mini_script = (main.STATIC_DIR.parent / "miniprogram" / "pages" / "index" / "index.js").read_text(encoding="utf-8")

        self.assertIn('id="boardParametersPanel"', html)
        self.assertIn('data-visible-mode="simulation"', html)
        self.assertRegex(html, r'name="phoneDetectionProfile" value="precision" checked')
        self.assertNotRegex(html, r'name="phoneDetectionProfile" value="fast" checked')
        self.assertIn('boardParametersPanel.classList.toggle("is-hidden", state.mode !== "simulation")', script)
        self.assertIn('phoneDetectionProfile: "precision"', script)
        self.assertIn('state.phoneDetectionProfile || "precision"', script)
        self.assertIn('profile: "precision"', mini_script)
        self.assertIn('profileText: "高精模式"', mini_script)

    def test_missing_phone_profile_defaults_precision_but_explicit_fast_remains(self):
        self.assertEqual(camera_trigger.normalize_phone_detection_profile(None), "precision")
        self.assertEqual(camera_trigger.phone_detection_profile_config(None)["profile"], "precision")
        self.assertEqual(camera_trigger.phone_detection_profile_config("fast")["profile"], "fast")
        self.assertEqual(main._platform_profile({"operation_mode": "phone"}), "precision")
        self.assertEqual(main._platform_profile({"operation_mode": "simulation"}), "fast")
        self.assertEqual(main._platform_profile({"operation_mode": "real"}), "fast")

    def test_phone_http_and_batch_endpoints_default_precision(self):
        class FakeRequest:
            def __init__(self, payload):
                self.payload = payload

            async def json(self):
                return self.payload

        with patch.object(main, "start_phone_batch_detection", return_value={"status": "queued"}) as start_batch:
            result = asyncio.run(main.api_phone_batch_detect("batch-1", FakeRequest({})))
        self.assertEqual(result["status"], "queued")
        start_batch.assert_called_once_with("batch-1", "precision", "cloud_baseline")

        async def fake_detect(_upload_id, detection_profile, _platform, _experiment):
            return {"detection_profile": detection_profile}

        with patch.object(main, "detect_phone_upload_phone_first", side_effect=fake_detect):
            result = asyncio.run(main.api_phone_detect(FakeRequest({"upload_id": "upload-1"})))
        self.assertEqual(result["detection_profile"], "precision")

    def test_phone_frontend_exposes_experiment_export_and_viewport_controls(self):
        html = (main.STATIC_DIR / "index.html").read_text(encoding="utf-8")
        script = (main.STATIC_DIR / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="phoneCanvasTools"', html)
        self.assertIn('id="phoneZoomInBtn"', html)
        self.assertIn('id="phoneZoomOutBtn"', html)
        self.assertIn('id="phoneZoomResetBtn"', html)
        self.assertIn('id="phoneElapsedValue"', html)
        self.assertIn("phone_experiment_profile: currentPhoneExperimentProfile()", script)
        self.assertIn("/annotated`;", script)
        self.assertIn('addEventListener("wheel", handlePhoneCanvasWheel', script)
        self.assertIn('addEventListener("pointermove", handlePhoneCanvasPointerMove', script)

    def test_normalize_phone_experiment_profile_supports_new_and_legacy_values(self):
        self.assertEqual(main.normalize_phone_experiment_profile("cloud_baseline"), "cloud_baseline")
        self.assertEqual(main.normalize_phone_experiment_profile("phone_link_sim"), "phone_link_sim")
        self.assertEqual(main.normalize_phone_experiment_profile("local_link_sim"), "local_link_sim")
        self.assertEqual(main.normalize_phone_experiment_profile(None, "huawei_cloud"), "cloud_baseline")
        self.assertEqual(main.normalize_phone_experiment_profile(None, "phone_browser"), "phone_link_sim")
        self.assertEqual(main.normalize_phone_experiment_profile(None, "local_dnn"), "local_link_sim")
        with self.assertRaises(ValueError):
            main.normalize_phone_experiment_profile("unknown_profile")

    def test_phone_experiment_delay_ranges_and_fast_profile_halving(self):
        midpoint = lambda lower, upper: (lower + upper) / 2.0
        self.assertEqual(main.phone_experiment_delay_seconds("cloud_baseline", "precision", midpoint), 0.0)
        self.assertEqual(main.phone_experiment_delay_seconds("phone_link_sim", "precision", midpoint), 2.75)
        self.assertEqual(main.phone_experiment_delay_seconds("local_link_sim", "precision", midpoint), 5.0)
        self.assertEqual(main.phone_experiment_delay_seconds("phone_link_sim", "fast", midpoint), 1.375)
        self.assertEqual(main.phone_experiment_delay_seconds("local_link_sim", "fast", midpoint), 2.5)

    def test_phone_experiment_always_routes_detection_to_huawei_cloud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "raw.jpg"
            raw_path.write_bytes(b"raw")
            seen_metadata = {}

            async def fake_detect(_raw_image, _annotated_path, _result_path, metadata):
                seen_metadata.update(metadata)
                return {
                    "ok": True,
                    "defects": [],
                    "raw_image": "raw.jpg",
                    "annotated_image": "annotated.jpg",
                    "result_json": "result.json",
                    "inference_ms": 321.0,
                    "detection_platform": "huawei_cloud",
                    "inference_location": "huawei_cloud",
                }

            with (
                patch.object(camera_trigger, "phone_detection_profile_config", return_value={"profile": "precision", "label": "高精模式"}),
                patch.object(camera_trigger, "get_phone_upload", return_value={"upload_id": "upload-1", "raw_image": "raw.jpg"}),
                patch.object(camera_trigger, "resolve_artifact", return_value=raw_path),
                patch.object(camera_trigger, "store_phone_detection_result", side_effect=lambda _upload_id, result, _profile, _label: result),
                patch.object(main, "detect_artifact_platform_first", side_effect=fake_detect),
                patch.object(main.random, "uniform", return_value=2.75),
                patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
            ):
                result = asyncio.run(
                    main.detect_phone_upload_phone_first(
                        "upload-1",
                        "precision",
                        "phone_browser",
                        "phone_link_sim",
                    )
                )

        self.assertEqual(seen_metadata["detection_platform"], "huawei_cloud")
        self.assertEqual(seen_metadata["phone_experiment_profile"], "phone_link_sim")
        self.assertEqual(result["experiment_profile"], "phone_link_sim")
        self.assertEqual(result["executed_platform"], "huawei_cloud")
        self.assertEqual(result["detection_platform"], "huawei_cloud")
        self.assertEqual(result["cloud_inference_ms"], 321.0)
        self.assertEqual(result["simulated_delay_ms"], 2750.0)
        self.assertGreaterEqual(result["total_elapsed_ms"], 2750.0)
        sleep_mock.assert_awaited_once_with(2.75)

    def test_non_phone_upload_detection_preserves_selected_platform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            raw_path = Path(temp_dir) / "raw.jpg"
            raw_path.write_bytes(b"raw")
            seen_metadata = {}

            async def fake_detect(_raw_image, _annotated_path, _result_path, metadata):
                seen_metadata.update(metadata)
                return {
                    "ok": True,
                    "defects": [],
                    "raw_image": "raw.jpg",
                    "annotated_image": "annotated.jpg",
                    "result_json": "result.json",
                    "inference_ms": 10.0,
                    "detection_platform": metadata["detection_platform"],
                }

            with (
                patch.object(camera_trigger, "phone_detection_profile_config", return_value={"profile": "fast", "label": "快速模式"}),
                patch.object(camera_trigger, "get_phone_upload", return_value={"upload_id": "upload-1", "raw_image": "raw.jpg"}),
                patch.object(camera_trigger, "resolve_artifact", return_value=raw_path),
                patch.object(camera_trigger, "store_phone_detection_result", side_effect=lambda _upload_id, result, _profile, _label: result),
                patch.object(main, "detect_artifact_platform_first", side_effect=fake_detect),
            ):
                result = asyncio.run(
                    main.detect_phone_upload_selected_platform(
                        "upload-1",
                        "fast",
                        "local_dnn",
                        "real_browser_camera",
                    )
                )

        self.assertEqual(seen_metadata["operation_mode"], "real_browser_camera")
        self.assertEqual(seen_metadata["detection_platform"], "local_dnn")
        self.assertEqual(result["detection_platform"], "local_dnn")

    def test_phone_annotated_download_returns_attachment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            annotated_path = Path(temp_dir) / "annotated.jpg"
            annotated_path.write_bytes(b"annotated")
            with (
                patch.object(camera_trigger, "get_phone_upload", return_value={"upload_id": "upload-1", "annotated_image": "annotated.jpg"}),
                patch.object(camera_trigger, "resolve_artifact", return_value=annotated_path),
            ):
                response = asyncio.run(main.api_phone_annotated("upload-1"))

        self.assertIsInstance(response, FileResponse)
        self.assertIn("upload-1_annotated.jpg", response.headers.get("content-disposition", ""))

    def test_phone_annotated_download_rejects_missing_result(self):
        with patch.object(camera_trigger, "get_phone_upload", return_value={"upload_id": "upload-1"}):
            with self.assertRaises(HTTPException) as context:
                asyncio.run(main.api_phone_annotated("upload-1"))
        self.assertEqual(context.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
