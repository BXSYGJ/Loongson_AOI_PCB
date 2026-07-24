import asyncio
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient

import camera_trigger
import main


class PhoneBatchStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="aoi-phone-batch-"))
        self.original_upload_dir = camera_trigger.PHONE_UPLOAD_DIR
        camera_trigger.PHONE_UPLOAD_DIR = self.temp_dir / "mobile_uploads"
        ok, encoded = cv2.imencode(".jpg", np.full((32, 48, 3), 127, dtype=np.uint8))
        self.assertTrue(ok)
        self.image_bytes = encoded.tobytes()

    def tearDown(self) -> None:
        main.phone_batch_tasks.clear()
        camera_trigger.PHONE_UPLOAD_DIR = self.original_upload_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_batch_accepts_nine_uploads_and_rejects_tenth(self) -> None:
        batch = camera_trigger.create_phone_batch("phone_page")
        batch_id = batch["batch_id"]

        for index in range(9):
            upload = camera_trigger.save_phone_upload(
                f"board-{index}.jpg",
                self.image_bytes,
                batch_id=batch_id,
                batch_index=index,
            )
            self.assertEqual(upload["batch_id"], batch_id)
            self.assertEqual(upload["batch_index"], index)

        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "maximum of 9"):
            camera_trigger.save_phone_upload(
                "board-9.jpg",
                self.image_bytes,
                batch_id=batch_id,
                batch_index=9,
            )

        stored = camera_trigger.get_phone_batch(batch_id)
        self.assertEqual(stored["status"], "ready")
        self.assertEqual(stored["upload_count"], 9)
        self.assertEqual([item["batch_index"] for item in stored["items"]], list(range(9)))

        legacy = camera_trigger.save_phone_upload("legacy.jpg", self.image_bytes)
        self.assertIsNone(legacy.get("batch_id"))
        self.assertEqual(len(camera_trigger.list_phone_uploads()), 10)

    def test_running_batch_blocks_delete_then_batch_delete_removes_all_items(self) -> None:
        batch_id = camera_trigger.create_phone_batch("web")["batch_id"]
        first = camera_trigger.save_phone_upload("first.jpg", self.image_bytes, batch_id=batch_id, batch_index=0)
        second = camera_trigger.save_phone_upload("second.jpg", self.image_bytes, batch_id=batch_id, batch_index=1)

        camera_trigger.update_phone_batch(batch_id, status="detecting")
        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "currently detecting"):
            camera_trigger.delete_phone_upload(first["upload_id"])
        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "currently detecting"):
            camera_trigger.delete_phone_batch(batch_id)

        camera_trigger.update_phone_batch(batch_id, status="ready")
        deleted = camera_trigger.delete_phone_upload(first["upload_id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(camera_trigger.get_phone_batch(batch_id)["upload_count"], 1)

        result = camera_trigger.delete_phone_batch(batch_id)
        self.assertEqual(result["deleted_upload_count"], 1)
        with self.assertRaises(camera_trigger.CameraTriggerError):
            camera_trigger.get_phone_upload(second["upload_id"])
        with self.assertRaises(camera_trigger.CameraTriggerError):
            camera_trigger.get_phone_batch(batch_id)

    def test_upload_failure_is_persisted_in_batch_progress(self) -> None:
        batch_id = camera_trigger.create_phone_batch("phone_page")["batch_id"]
        recorded = camera_trigger.record_phone_batch_upload_error(
            batch_id,
            batch_index=2,
            filename="broken.jpg",
            error="network interrupted",
        )
        self.assertEqual(recorded["status"], "partial_failed")
        self.assertEqual(recorded["upload_count"], 1)
        self.assertEqual(recorded["failed_count"], 1)
        self.assertEqual(recorded["items"][0]["status"], "upload_error")
        self.assertEqual(recorded["items"][0]["name"], "broken.jpg")
        camera_trigger.save_phone_upload("valid.jpg", self.image_bytes, batch_id=batch_id, batch_index=3)
        self.assertEqual(camera_trigger.get_phone_batch(batch_id)["status"], "partial_failed")

    def test_batch_rest_api_creates_uploads_lists_and_deletes_batch(self) -> None:
        client = TestClient(main.app)
        created_response = client.post("/api/phone/batches", json={"source": "test_client"})
        self.assertEqual(created_response.status_code, 200)
        batch_id = created_response.json()["batch_id"]

        upload_response = client.post(
            "/api/phone/upload",
            params={"filename": "api.jpg", "batch_id": batch_id, "batch_index": 0},
            content=self.image_bytes,
            headers={"Content-Type": "image/jpeg"},
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertEqual(upload_response.json()["batch_id"], batch_id)

        listed_response = client.get("/api/phone/batches")
        self.assertEqual(listed_response.status_code, 200)
        self.assertEqual(listed_response.json()["batches"][0]["batch_id"], batch_id)
        detail_response = client.get(f"/api/phone/batches/{batch_id}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["upload_count"], 1)

        deleted_response = client.delete(f"/api/phone/batches/{batch_id}")
        self.assertEqual(deleted_response.status_code, 200)
        self.assertEqual(deleted_response.json()["deleted_upload_count"], 1)

    def test_selection_batch_references_existing_uploads_without_taking_ownership(self) -> None:
        owner_batch_id = camera_trigger.create_phone_batch("phone_page")["batch_id"]
        uploads = [
            camera_trigger.save_phone_upload(
                f"existing-{index}.jpg",
                self.image_bytes,
                batch_id=owner_batch_id,
                batch_index=index,
            )
            for index in range(2)
        ]
        for upload in uploads:
            camera_trigger.update_phone_upload_status(upload["upload_id"], "detected")

        selection = camera_trigger.create_phone_batch(
            "desktop_selection",
            upload_ids=[upload["upload_id"] for upload in uploads],
        )

        self.assertEqual(selection["batch_kind"], "selection")
        self.assertFalse(selection["owns_uploads"])
        self.assertEqual([item["status"] for item in selection["items"]], ["uploaded", "uploaded"])
        for upload in uploads:
            stored = camera_trigger.get_phone_upload(upload["upload_id"])
            self.assertEqual(stored["batch_id"], owner_batch_id)

        deleted = camera_trigger.delete_phone_batch(selection["batch_id"])
        self.assertEqual(deleted["deleted_upload_count"], 0)
        for upload in uploads:
            self.assertEqual(camera_trigger.get_phone_upload(upload["upload_id"])["batch_id"], owner_batch_id)

    def test_selection_batch_validates_limit_duplicates_and_missing_uploads(self) -> None:
        uploads = [camera_trigger.save_phone_upload(f"item-{index}.jpg", self.image_bytes) for index in range(9)]
        upload_ids = [upload["upload_id"] for upload in uploads]

        batch = camera_trigger.create_phone_batch("desktop_selection", upload_ids=upload_ids)
        self.assertEqual(batch["upload_count"], 9)
        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "maximum of 9"):
            camera_trigger.create_phone_batch("desktop_selection", upload_ids=upload_ids + ["missing"])
        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "duplicate"):
            camera_trigger.create_phone_batch("desktop_selection", upload_ids=[upload_ids[0], upload_ids[0]])
        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "does not exist"):
            camera_trigger.create_phone_batch("desktop_selection", upload_ids=["missing-upload"])

    def test_delete_phone_uploads_preflights_all_items_and_removes_batch_references(self) -> None:
        owner_batch_id = camera_trigger.create_phone_batch("phone_page")["batch_id"]
        first = camera_trigger.save_phone_upload("first.jpg", self.image_bytes, batch_id=owner_batch_id, batch_index=0)
        second = camera_trigger.save_phone_upload("second.jpg", self.image_bytes, batch_id=owner_batch_id, batch_index=1)
        third = camera_trigger.save_phone_upload("third.jpg", self.image_bytes)
        selection = camera_trigger.create_phone_batch(
            "desktop_selection",
            upload_ids=[first["upload_id"], third["upload_id"]],
        )
        camera_trigger.update_phone_batch(selection["batch_id"], status="detecting")

        with self.assertRaisesRegex(camera_trigger.CameraTriggerError, "currently detecting"):
            camera_trigger.delete_phone_uploads([first["upload_id"], second["upload_id"]])
        self.assertTrue(camera_trigger.get_phone_upload(first["upload_id"])["ok"])
        self.assertTrue(camera_trigger.get_phone_upload(second["upload_id"])["ok"])

        camera_trigger.update_phone_batch(selection["batch_id"], status="partial_failed")
        deleted = camera_trigger.delete_phone_uploads([first["upload_id"], second["upload_id"]])
        self.assertEqual(deleted["deleted_count"], 2)
        self.assertEqual(set(deleted["deleted_upload_ids"]), {first["upload_id"], second["upload_id"]})
        self.assertEqual(camera_trigger.get_phone_batch(owner_batch_id)["upload_count"], 0)
        self.assertEqual(camera_trigger.get_phone_batch(selection["batch_id"])["upload_count"], 1)
        self.assertEqual(camera_trigger.get_phone_batch(selection["batch_id"])["items"][0]["upload_id"], third["upload_id"])

    def test_batch_annotated_archive_contains_successes_and_failure_manifest(self) -> None:
        uploads = [camera_trigger.save_phone_upload(f"board-{index}.jpg", self.image_bytes) for index in range(2)]
        batch = camera_trigger.create_phone_batch(
            "desktop_selection",
            upload_ids=[upload["upload_id"] for upload in uploads],
        )
        annotated_path = camera_trigger._phone_upload_path(uploads[0]["upload_id"]) / "annotated.jpg"
        annotated_path.write_bytes(self.image_bytes)
        metadata = camera_trigger._read_phone_metadata(uploads[0]["upload_id"])
        metadata.update(
            {
                "status": "detected",
                "annotated_image": str(annotated_path),
                "defects": [{"type": "short_circuit"}],
                "detection_profile": "fast",
            }
        )
        camera_trigger._write_phone_metadata(metadata)
        camera_trigger.update_phone_batch_item(batch["batch_id"], uploads[0]["upload_id"], status="detected")
        camera_trigger.update_phone_batch_item(
            batch["batch_id"],
            uploads[1]["upload_id"],
            status="detection_error",
            error="synthetic failure",
        )
        camera_trigger.update_phone_batch(batch["batch_id"], status="partial_failed")

        archive = camera_trigger.build_phone_batch_annotated_archive(batch["batch_id"])
        with zipfile.ZipFile(archive, "r") as zip_file:
            names = zip_file.namelist()
            self.assertIn("manifest.json", names)
            self.assertEqual(len([name for name in names if name.endswith(".jpg")]), 1)
            manifest = json.loads(zip_file.read("manifest.json").decode("utf-8"))
        self.assertEqual(manifest["batch_id"], batch["batch_id"])
        self.assertEqual(manifest["successful_count"], 1)
        self.assertEqual(manifest["failed_count"], 1)
        self.assertEqual(manifest["items"][1]["error"], "synthetic failure")

    def test_selection_batch_rest_api_and_atomic_delete_endpoint(self) -> None:
        uploads = [camera_trigger.save_phone_upload(f"api-{index}.jpg", self.image_bytes) for index in range(2)]
        upload_ids = [upload["upload_id"] for upload in uploads]
        client = TestClient(main.app)

        created = client.post(
            "/api/phone/batches",
            json={"source": "desktop_selection", "upload_ids": upload_ids},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["batch_kind"], "selection")

        deleted = client.post("/api/phone/uploads/delete-selection", json={"upload_ids": upload_ids})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted_count"], 2)


class PhoneBatchDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="aoi-phone-batch-detect-"))
        self.original_upload_dir = camera_trigger.PHONE_UPLOAD_DIR
        camera_trigger.PHONE_UPLOAD_DIR = self.temp_dir / "mobile_uploads"
        ok, encoded = cv2.imencode(".jpg", np.full((24, 36, 3), 160, dtype=np.uint8))
        self.assertTrue(ok)
        self.image_bytes = encoded.tobytes()

    async def asyncTearDown(self) -> None:
        camera_trigger.PHONE_UPLOAD_DIR = self.original_upload_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_sequential_detection_continues_after_failure_and_retries_only_failed(self) -> None:
        batch_id = camera_trigger.create_phone_batch("web")["batch_id"]
        uploads = [
            camera_trigger.save_phone_upload(
                f"item-{index}.jpg",
                self.image_bytes,
                batch_id=batch_id,
                batch_index=index,
            )
            for index in range(3)
        ]
        calls: list[str] = []

        async def first_detector(upload_id: str, *_args) -> dict:
            calls.append(upload_id)
            if upload_id == uploads[1]["upload_id"]:
                raise camera_trigger.CameraTriggerError("synthetic failure")
            return camera_trigger.store_phone_detection_result(
                upload_id,
                {"ok": True, "defects": [], "annotated_image": "mock.jpg", "result_json": "mock.json"},
                "fast",
                "快速模式",
            )

        result = await main.run_phone_batch_detection(
            batch_id,
            "fast",
            "cloud_baseline",
            detector=first_detector,
        )
        self.assertEqual(calls, [item["upload_id"] for item in uploads])
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["completed_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("synthetic failure", result["items"][1]["error"])

        retry_calls: list[str] = []

        async def retry_detector(upload_id: str, *_args) -> dict:
            retry_calls.append(upload_id)
            return camera_trigger.store_phone_detection_result(
                upload_id,
                {"ok": True, "defects": [], "annotated_image": "retry.jpg", "result_json": "retry.json"},
                "fast",
                "快速模式",
            )

        retried = await main.run_phone_batch_detection(
            batch_id,
            "fast",
            "cloud_baseline",
            detector=retry_detector,
        )
        self.assertEqual(retry_calls, [uploads[1]["upload_id"]])
        self.assertEqual(retried["status"], "done")
        self.assertEqual(retried["completed_count"], 3)
        self.assertEqual(retried["failed_count"], 0)

    async def test_selection_batch_redetects_items_that_already_have_results(self) -> None:
        uploads = [camera_trigger.save_phone_upload(f"detected-{index}.jpg", self.image_bytes) for index in range(2)]
        for upload in uploads:
            camera_trigger.update_phone_upload_status(upload["upload_id"], "detected")
        batch = camera_trigger.create_phone_batch(
            "desktop_selection",
            upload_ids=[upload["upload_id"] for upload in uploads],
        )
        calls: list[str] = []

        async def detector(upload_id: str, *_args) -> dict:
            calls.append(upload_id)
            return camera_trigger.store_phone_detection_result(
                upload_id,
                {"ok": True, "defects": [], "annotated_image": "rerun.jpg", "result_json": "rerun.json"},
                "fast",
                "快速模式",
            )

        result = await main.run_phone_batch_detection(
            batch["batch_id"],
            "fast",
            "cloud_baseline",
            detector=detector,
        )
        self.assertEqual(calls, [upload["upload_id"] for upload in uploads])
        self.assertEqual(result["status"], "done")

    async def test_selection_batch_records_detection_failure_on_the_reference_item(self) -> None:
        upload = camera_trigger.save_phone_upload("failure.jpg", self.image_bytes)
        batch = camera_trigger.create_phone_batch("desktop_selection", upload_ids=[upload["upload_id"]])

        async def detector(*_args) -> dict:
            raise camera_trigger.CameraTriggerError("selection failure")

        result = await main.run_phone_batch_detection(
            batch["batch_id"],
            "fast",
            "cloud_baseline",
            detector=detector,
        )

        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["items"][0]["status"], "detection_error")
        self.assertIn("selection failure", result["items"][0]["error"])


class PhoneBatchFrontendSourceTests(unittest.TestCase):
    def test_all_frontends_expose_nine_image_batch_controls_and_results(self) -> None:
        root = Path(__file__).resolve().parent
        index_html = (root / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        phone_html = (root / "static" / "phone.html").read_text(encoding="utf-8")
        mini_js = (root / "miniprogram" / "pages" / "index" / "index.js").read_text(encoding="utf-8")
        mini_wxml = (root / "miniprogram" / "pages" / "index" / "index.wxml").read_text(encoding="utf-8")

        for marker in ("phoneCurrentBatch", "detectPhoneBatchBtn", "deletePhoneBatchBtn"):
            self.assertIn(f'id="{marker}"', index_html)
            self.assertIn(marker, app_js)
        self.assertIn("/api/phone/batches", app_js)
        self.assertIn("phoneBatchSelectionError", app_js)
        for marker in (
            "phoneUploadHistoryList",
            "phoneHistorySelectionCount",
            "selectAllPhoneHistoryBtn",
            "clearPhoneHistorySelectionBtn",
            "detectSelectedPhoneUploadsBtn",
            "deleteSelectedPhoneUploadsBtn",
        ):
            self.assertIn(f'id="{marker}"', index_html)
            self.assertIn(marker, app_js)
        self.assertIn("/api/phone/uploads/delete-selection", app_js)
        self.assertIn('source: "desktop_selection"', app_js)
        self.assertIn("latestPhoneUploadId", app_js)
        self.assertIn("selectLatest: true", app_js)
        self.assertIn("/annotated.zip", app_js)

        self.assertRegex(phone_html, r'id="galleryInput"[^>]*multiple')
        for marker in ("batchResults", "detectBatchBtn", "deleteBatchBtn"):
            self.assertIn(f'id="{marker}"', phone_html)
        self.assertIn("selectedFiles", phone_html)
        self.assertIn("/api/phone/batches", phone_html)

        self.assertRegex(mini_js, r"count:\s*9")
        self.assertIn("detectCurrentBatch", mini_js)
        self.assertIn("deleteCurrentBatch", mini_js)
        self.assertIn("batchResults", mini_wxml)


if __name__ == "__main__":
    unittest.main()
