from __future__ import annotations

import mimetypes
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CloudSettings


class StorageError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadTarget:
    object_key: str
    upload_url: str
    upload_method: str
    expires_at: str
    headers: dict[str, str]
    form_data: dict[str, str]


def expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class BaseStorage:
    def create_upload_target(self, job_id: str, filename: str) -> UploadTarget:
        raise NotImplementedError

    def put_bytes(self, object_key: str, content: bytes) -> None:
        raise NotImplementedError

    def put_file(self, object_key: str, source: Path) -> None:
        raise NotImplementedError

    def download_to_path(self, object_key: str, target: Path) -> None:
        raise NotImplementedError

    def public_url(self, object_key: str) -> str:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError


class LocalStorage(BaseStorage):
    def __init__(self, settings: CloudSettings) -> None:
        self.settings = settings
        self.root = settings.storage_root / "objects"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        if self.root.resolve() not in candidate.parents and candidate != self.root.resolve():
            raise StorageError("Object key escapes local storage root.")
        return candidate

    def create_upload_target(self, job_id: str, filename: str) -> UploadTarget:
        object_key = f"cloud/jobs/{job_id}/raw.jpg"
        return UploadTarget(
            object_key=object_key,
            upload_url=f"{self.settings.public_api_base}/api/cloud/local-upload/{job_id}?object_key={object_key}",
            upload_method="PUT",
            expires_at=expires_at(self.settings.upload_expires_seconds),
            headers={"Content-Type": content_type(Path(filename))},
            form_data={},
        )

    def put_bytes(self, object_key: str, content: bytes) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def put_file(self, object_key: str, source: Path) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)

    def download_to_path(self, object_key: str, target: Path) -> None:
        source = self._path(object_key)
        if not source.exists():
            raise StorageError(f"Object does not exist: {object_key}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def public_url(self, object_key: str) -> str:
        return f"{self.settings.public_api_base}/api/cloud/files/{object_key}"

    def status(self) -> dict[str, Any]:
        return {"backend": "local", "root": str(self.root)}


class ObsStorage(BaseStorage):
    def __init__(self, settings: CloudSettings) -> None:
        self.settings = settings
        self._client: Any | None = None

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not all([self.settings.obs_endpoint, self.settings.obs_bucket, self.settings.obs_ak, self.settings.obs_sk]):
            raise StorageError("OBS backend requires HUAWEI_OBS_ENDPOINT, HUAWEI_OBS_BUCKET, HUAWEI_OBS_AK and HUAWEI_OBS_SK.")
        try:
            from obs import ObsClient  # type: ignore[import-not-found]
        except Exception as exc:
            raise StorageError("OBS backend requires Huawei OBS Python SDK package esdk-obs-python.") from exc
        self._client = ObsClient(access_key_id=self.settings.obs_ak, secret_access_key=self.settings.obs_sk, server=self.settings.obs_endpoint)
        return self._client

    def create_upload_target(self, job_id: str, filename: str) -> UploadTarget:
        object_key = f"cloud/jobs/{job_id}/raw.jpg"
        result = self.client().createSignedUrl(
            method="PUT",
            bucketName=self.settings.obs_bucket,
            objectKey=object_key,
            expires=self.settings.upload_expires_seconds,
            headers={"Content-Type": content_type(Path(filename))},
        )
        upload_url = getattr(result, "signedUrl", None) or getattr(result, "signed_url", None)
        if not upload_url and isinstance(result, dict):
            upload_url = result.get("signedUrl") or result.get("signed_url")
        if not upload_url:
            raise StorageError("OBS SDK did not return a signed upload URL.")
        return UploadTarget(
            object_key=object_key,
            upload_url=str(upload_url),
            upload_method="PUT",
            expires_at=expires_at(self.settings.upload_expires_seconds),
            headers={"Content-Type": content_type(Path(filename))},
            form_data={},
        )

    def put_bytes(self, object_key: str, content: bytes) -> None:
        response = self.client().putContent(self.settings.obs_bucket, object_key, content)
        if getattr(response, "status", 500) >= 300:
            raise StorageError(f"OBS putContent failed: {getattr(response, 'errorMessage', response)}")

    def put_file(self, object_key: str, source: Path) -> None:
        response = self.client().putFile(self.settings.obs_bucket, object_key, str(source))
        if getattr(response, "status", 500) >= 300:
            raise StorageError(f"OBS putFile failed: {getattr(response, 'errorMessage', response)}")

    def download_to_path(self, object_key: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        response = self.client().getObject(self.settings.obs_bucket, object_key, downloadPath=str(target))
        if getattr(response, "status", 500) >= 300:
            raise StorageError(f"OBS getObject failed: {getattr(response, 'errorMessage', response)}")

    def public_url(self, object_key: str) -> str:
        result = self.client().createSignedUrl(method="GET", bucketName=self.settings.obs_bucket, objectKey=object_key, expires=3600)
        signed_url = getattr(result, "signedUrl", None) or getattr(result, "signed_url", None)
        if not signed_url and isinstance(result, dict):
            signed_url = result.get("signedUrl") or result.get("signed_url")
        return str(signed_url or "")

    def status(self) -> dict[str, Any]:
        configured = all([self.settings.obs_endpoint, self.settings.obs_bucket, self.settings.obs_ak, self.settings.obs_sk])
        return {"backend": "obs", "bucket": self.settings.obs_bucket, "endpoint": self.settings.obs_endpoint, "configured": configured}


def create_storage(settings: CloudSettings) -> BaseStorage:
    if settings.storage_backend == "obs":
        return ObsStorage(settings)
    return LocalStorage(settings)
