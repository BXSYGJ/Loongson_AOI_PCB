import os
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))


class CloudBackendFlowTest(unittest.TestCase):
    def make_jpeg(self) -> bytes:
        image = np.zeros((96, 128, 3), dtype=np.uint8)
        image[24:72, 42:86] = (210, 210, 210)
        ok, encoded = cv2.imencode(".jpg", image)
        self.assertTrue(ok)
        return encoded.tobytes()

    def make_client(self, root: Path) -> TestClient:
        from app.config import CloudSettings
        from app.main import create_app

        settings = CloudSettings(
            storage_backend="local",
            storage_root=root,
            detector_backend="fake",
            public_api_base="http://testserver",
            public_ws_base="ws://testserver",
        )
        return TestClient(create_app(settings))

    def test_local_storage_job_flow_reaches_done_and_exposes_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = self.make_client(Path(tmp))

            create_response = client.post(
                "/api/cloud/jobs",
                json={"filename": "board.jpg", "profile": "fast", "client_type": "miniprogram"},
            )
            self.assertEqual(create_response.status_code, 200)
            created = create_response.json()
            self.assertEqual(created["status"], "created")
            self.assertEqual(created["profile"], "fast")
            self.assertIn("/api/cloud/local-upload/", created["upload_url"])
            self.assertIn("/ws/cloud/jobs/", created["ws_url"])

            upload_response = client.put(created["upload_url"], content=self.make_jpeg())
            self.assertEqual(upload_response.status_code, 200)
            self.assertEqual(upload_response.json()["status"], "uploaded")

            queued_response = client.post(
                f"/api/cloud/jobs/{created['job_id']}/uploaded",
                json={"object_key": created["object_key"], "size_bytes": len(self.make_jpeg())},
            )
            self.assertEqual(queued_response.status_code, 200)
            self.assertEqual(queued_response.json()["status"], "queued")

            status_response = client.get(f"/api/cloud/jobs/{created['job_id']}")
            self.assertEqual(status_response.status_code, 200)
            payload = status_response.json()
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["job_id"], created["job_id"])
            self.assertEqual(payload["profile"], "fast")
            self.assertEqual(payload["defect_count"], 1)
            self.assertTrue(payload["raw_image_url"].endswith("/raw.jpg"))
            self.assertTrue(payload["annotated_image_url"].endswith("/annotated.jpg"))
            self.assertTrue(payload["result_json_url"].endswith("/result.json"))
            self.assertGreaterEqual(payload["inference_ms"], 0)
            self.assertEqual(payload["defects"][0]["type"], "mock_defect")

            with client.websocket_connect(f"/ws/cloud/jobs/{created['job_id']}") as websocket:
                message = websocket.receive_json()
            self.assertEqual(message["type"], "result")
            self.assertEqual(message["job_id"], created["job_id"])
            self.assertEqual(message["defects"][0]["label"], "mock_defect")


class CloudBackendHealthTest(unittest.TestCase):
    def test_health_reports_local_storage_and_detector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = CloudBackendFlowTest().make_client(Path(tmp))
            response = client.get("/health")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["storage"]["backend"], "local")
            self.assertEqual(payload["detector"]["backend"], "fake")
            self.assertIn("model_exists", payload["detector"])


if __name__ == "__main__":
    unittest.main()
