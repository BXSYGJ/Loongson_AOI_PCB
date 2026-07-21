from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .config import CloudSettings
from .detector import BaseDetector, DetectorError
from .storage import BaseStorage


class JobError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(filename: str) -> str:
    name = Path(filename or "pcb.jpg").name
    cleaned = re.sub(r"[^0-9A-Za-z_.\-\u4e00-\u9fff]+", "_", name).strip("._")
    return cleaned or "pcb.jpg"


@dataclass
class Job:
    job_id: str
    filename: str
    profile: str
    client_type: str
    status: str
    object_key: str
    upload_url: str
    upload_method: str
    ws_url: str
    created_at: str
    updated_at: str
    expires_at: str
    upload_headers: dict[str, str] = field(default_factory=dict)
    upload_form_data: dict[str, str] = field(default_factory=dict)
    raw_image_url: str | None = None
    annotated_image_url: str | None = None
    result_json_url: str | None = None
    defects: list[dict[str, Any]] = field(default_factory=list)
    defect_count: int = 0
    inference_ms: float | None = None
    detection_details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class JobRepository:
    def __init__(self, settings: CloudSettings) -> None:
        self.root = settings.storage_root / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", job_id):
            raise JobError("Invalid job id.")
        return self.root / f"{job_id}.json"

    def save(self, job: Job) -> None:
        path = self._path(job.job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(job), ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)

    def get(self, job_id: str) -> Job:
        path = self._path(job_id)
        if not path.exists():
            raise JobError(f"Job does not exist: {job_id}")
        last_error: Exception | None = None
        for _ in range(10):
            try:
                content = path.read_text(encoding="utf-8")
                if not content.strip():
                    raise json.JSONDecodeError("empty job file", content, 0)
                return Job(**json.loads(content))
            except (OSError, json.JSONDecodeError) as exc:
                last_error = exc
                time.sleep(0.05)
        raise JobError(f"Job file is not readable yet: {job_id}: {last_error}")


class JobManager:
    def __init__(self, settings: CloudSettings, storage: BaseStorage, detector: BaseDetector) -> None:
        self.settings = settings
        self.storage = storage
        self.detector = detector
        self.repo = JobRepository(settings)
        self._connections: dict[str, list[WebSocket]] = {}

    def create_job(self, filename: str, profile: str = "fast", client_type: str = "miniprogram") -> Job:
        normalized_profile = "precision" if profile == "precision" else "fast"
        job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + os.urandom(4).hex()
        target = self.storage.create_upload_target(job_id, safe_filename(filename))
        job = Job(
            job_id=job_id,
            filename=safe_filename(filename),
            profile=normalized_profile,
            client_type=client_type or "miniprogram",
            status="created",
            object_key=target.object_key,
            upload_url=target.upload_url,
            upload_method=target.upload_method,
            ws_url=f"{self.settings.public_ws_base}/ws/cloud/jobs/{job_id}",
            created_at=now_iso(),
            updated_at=now_iso(),
            expires_at=target.expires_at,
            upload_headers=target.headers,
            upload_form_data=target.form_data,
        )
        self.repo.save(job)
        return job

    def get_job(self, job_id: str) -> Job:
        return self.repo.get(job_id)

    def mark_uploaded(self, job_id: str, object_key: str, size_bytes: int | None = None) -> Job:
        job = self.repo.get(job_id)
        if object_key != job.object_key:
            raise JobError("Uploaded object key does not match job object key.")
        job.status = "queued"
        job.updated_at = now_iso()
        job.detection_details["uploaded_size_bytes"] = int(size_bytes or 0)
        self.repo.save(job)
        return job

    async def broadcast(self, job_id: str, message: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for websocket in list(self._connections.get(job_id, [])):
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self._connections.get(job_id, []).remove(websocket)

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(job_id, []).append(websocket)
        job = self.repo.get(job_id)
        await websocket.send_json(job_event(job))

    def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        if job_id in self._connections and websocket in self._connections[job_id]:
            self._connections[job_id].remove(websocket)

    def process_job(self, job_id: str) -> Job:
        job = self.repo.get(job_id)
        try:
            job.status = "detecting"
            job.updated_at = now_iso()
            self.repo.save(job)
            with tempfile.TemporaryDirectory() as tmp:
                workdir = Path(tmp)
                uploaded_path = workdir / "uploaded"
                raw_path = workdir / "raw.jpg"
                annotated_path = workdir / "annotated.jpg"
                result_json_path = workdir / "result.json"
                self.storage.download_to_path(job.object_key, uploaded_path)
                self._write_grayscale_jpeg(uploaded_path, raw_path)
                self.storage.put_file(job.object_key, raw_path)
                defects, inference_ms, details = self.detector.detect(raw_path, annotated_path, result_json_path, job.profile)
                annotated_key = f"cloud/jobs/{job.job_id}/annotated.jpg"
                result_key = f"cloud/jobs/{job.job_id}/result.json"
                self.storage.put_file(annotated_key, annotated_path)
                self.storage.put_file(result_key, result_json_path)
                job.status = "done"
                job.raw_image_url = self.storage.public_url(job.object_key)
                job.annotated_image_url = self.storage.public_url(annotated_key)
                job.result_json_url = self.storage.public_url(result_key)
                job.defects = defects
                job.defect_count = len(defects)
                job.inference_ms = inference_ms
                job.detection_details.update(details)
                job.updated_at = now_iso()
                self.repo.save(job)
                return job
        except Exception as exc:
            job.status = "error"
            job.error = str(exc)
            job.updated_at = now_iso()
            self.repo.save(job)
            raise

    def _write_grayscale_jpeg(self, source: Path, target: Path) -> None:
        import cv2

        image = cv2.imread(str(source))
        if image is None:
            raise DetectorError(f"Uploaded image could not be decoded: {source}")
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        normalized = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), normalized):
            raise DetectorError(f"Could not write grayscale image: {target}")


def job_payload(job: Job) -> dict[str, Any]:
    return asdict(job)


def job_event(job: Job) -> dict[str, Any]:
    payload = job_payload(job)
    event_type = {
        "created": "job_created",
        "queued": "uploaded",
        "detecting": "detecting",
        "done": "result",
        "error": "error",
    }.get(job.status, job.status)
    payload["type"] = event_type
    return payload
