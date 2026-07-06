from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class CloudDetectionUnavailable(RuntimeError):
    pass


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _default_cloud_api_base() -> str:
    value = os.getenv("LOONGSON_AOI_CLOUD_API_BASE", "").strip()
    if value:
        return value.rstrip("/")
    config_path = Path(os.getenv("LOONGSON_AOI_CLOUD_API_BASE_FILE", Path(__file__).with_name("cloud_api_base.txt"))).expanduser()
    try:
        for line in config_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.rstrip("/")
    except OSError:
        pass
    return ""


class CloudDetectionClient:
    def __init__(self) -> None:
        self.enabled = _env_bool("LOONGSON_AOI_CLOUD_DETECTION_ENABLED", True)
        self.api_base = _default_cloud_api_base()
        self.timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_TIMEOUT_SECONDS", 240.0)
        self.request_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_REQUEST_TIMEOUT_SECONDS", 20.0)
        self.upload_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_UPLOAD_TIMEOUT_SECONDS", 120.0)
        self.poll_request_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_POLL_REQUEST_TIMEOUT_SECONDS", 20.0)
        self.poll_interval_seconds = _env_float("LOONGSON_AOI_CLOUD_POLL_SECONDS", 0.35)
        self.retry_count = max(1, int(_env_float("LOONGSON_AOI_CLOUD_RETRIES", 3)))
        self.retry_delay_seconds = _env_float("LOONGSON_AOI_CLOUD_RETRY_DELAY_SECONDS", 0.8)
        self._last_error: str | None = None

    def refresh_from_env(self) -> None:
        self.enabled = _env_bool("LOONGSON_AOI_CLOUD_DETECTION_ENABLED", True)
        self.api_base = _default_cloud_api_base() or self.api_base
        self.timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_TIMEOUT_SECONDS", self.timeout_seconds)
        self.request_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_REQUEST_TIMEOUT_SECONDS", self.request_timeout_seconds)
        self.upload_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_UPLOAD_TIMEOUT_SECONDS", self.upload_timeout_seconds)
        self.poll_request_timeout_seconds = _env_float("LOONGSON_AOI_CLOUD_POLL_REQUEST_TIMEOUT_SECONDS", self.poll_request_timeout_seconds)
        self.poll_interval_seconds = _env_float("LOONGSON_AOI_CLOUD_POLL_SECONDS", self.poll_interval_seconds)
        self.retry_count = max(1, int(_env_float("LOONGSON_AOI_CLOUD_RETRIES", self.retry_count)))
        self.retry_delay_seconds = _env_float("LOONGSON_AOI_CLOUD_RETRY_DELAY_SECONDS", self.retry_delay_seconds)

    def status(self) -> dict[str, Any]:
        self.refresh_from_env()
        return {
            "enabled": self.enabled,
            "configured": bool(self.api_base),
            "api_base": self.api_base,
            "timeout_seconds": self.timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "upload_timeout_seconds": self.upload_timeout_seconds,
            "poll_request_timeout_seconds": self.poll_request_timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "retry_count": self.retry_count,
            "retry_delay_seconds": self.retry_delay_seconds,
            "last_error": self._last_error,
            "priority": 1,
            "message": "Huawei Cloud detection is configured." if self.enabled and self.api_base else "Huawei Cloud detection is not configured.",
        }

    def detect_file(
        self,
        raw_path: Path,
        *,
        filename: str | None = None,
        profile: str = "fast",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.refresh_from_env()
        if not self.enabled:
            raise CloudDetectionUnavailable("cloud_detection_disabled")
        if not self.api_base:
            raise CloudDetectionUnavailable("cloud_api_base_not_configured")
        if not raw_path.exists():
            raise CloudDetectionUnavailable(f"raw_image_not_found: {raw_path}")

        started = time.perf_counter()
        try:
            job = self._create_job(filename or raw_path.name, profile, metadata or {})
            self._upload(job, raw_path)
            self._mark_uploaded(job, raw_path.stat().st_size)
            final = self._poll_job(str(job["job_id"]))
            if final.get("status") != "done":
                raise CloudDetectionUnavailable(final.get("error") or final.get("message") or f"cloud_job_{final.get('status')}")
            final.setdefault("inference_ms", round((time.perf_counter() - started) * 1000.0, 2))
            final["cloud_job_id"] = job.get("job_id")
            final["cloud_object_key"] = job.get("object_key")
            final["cloud_api_base"] = self.api_base
            final["inference_location"] = "huawei_cloud"
            self._last_error = None
            return final
        except CloudDetectionUnavailable as exc:
            self._last_error = str(exc)
            raise
        except Exception as exc:
            self._last_error = str(exc)
            raise CloudDetectionUnavailable(str(exc)) from exc

    def _json_request(self, method: str, url: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if payload is not None else {},
        )
        request_timeout = timeout or self.request_timeout_seconds
        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                with urllib.request.urlopen(request, timeout=request_timeout) as response:
                    body = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {500, 502, 503, 504} or attempt >= self.retry_count:
                    raise CloudDetectionUnavailable(f"cloud_http_{exc.code}: {detail}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self.retry_count:
                    raise CloudDetectionUnavailable(f"cloud_request_failed_after_{attempt}_tries: {exc}") from exc
                last_error = exc
            time.sleep(self.retry_delay_seconds * attempt)
        else:
            raise CloudDetectionUnavailable(f"cloud_request_failed: {last_error}")
        if not body:
            return {}
        return json.loads(body)

    def _create_job(self, filename: str, profile: str, metadata: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "filename": filename,
            "profile": profile,
            "client_type": "loongson_aoi_console",
            "metadata": metadata,
        }
        job = self._json_request("POST", f"{self.api_base}/api/cloud/jobs", payload, timeout=self.request_timeout_seconds)
        if not job.get("job_id") or not job.get("upload_url"):
            raise CloudDetectionUnavailable("cloud_job_create_missing_fields")
        return job

    def _upload(self, job: dict[str, Any], raw_path: Path) -> None:
        upload_url = str(job.get("upload_url") or "")
        method = str(job.get("upload_method") or "PUT").upper()
        if method != "PUT":
            raise CloudDetectionUnavailable(f"unsupported_cloud_upload_method: {method}")
        headers = dict(job.get("upload_headers") or {})
        headers.setdefault("Content-Type", mimetypes.guess_type(raw_path.name)[0] or "image/jpeg")
        data = raw_path.read_bytes()
        request = urllib.request.Request(upload_url, data=data, method="PUT", headers=headers)
        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.upload_timeout_seconds) as response:
                    if response.status < 200 or response.status >= 300:
                        raise CloudDetectionUnavailable(f"cloud_upload_http_{response.status}")
                return
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code not in {500, 502, 503, 504} or attempt >= self.retry_count:
                    raise CloudDetectionUnavailable(f"cloud_upload_http_{exc.code}: {detail}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt >= self.retry_count:
                    raise CloudDetectionUnavailable(f"cloud_upload_failed_after_{attempt}_tries: {exc}") from exc
                last_error = exc
            time.sleep(self.retry_delay_seconds * attempt)
        raise CloudDetectionUnavailable(f"cloud_upload_failed: {last_error}")

    def _mark_uploaded(self, job: dict[str, Any], size_bytes: int) -> None:
        job_id = urllib.parse.quote(str(job["job_id"]), safe="")
        self._json_request(
            "POST",
            f"{self.api_base}/api/cloud/jobs/{job_id}/uploaded",
            {"object_key": job.get("object_key"), "size_bytes": size_bytes},
            timeout=self.request_timeout_seconds,
        )

    def _poll_job(self, job_id: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(job_id, safe="")
        deadline = time.perf_counter() + self.timeout_seconds
        last: dict[str, Any] = {}
        last_error = ""
        while time.perf_counter() < deadline:
            try:
                last = self._json_request(
                    "GET",
                    f"{self.api_base}/api/cloud/jobs/{encoded}",
                    timeout=min(self.poll_request_timeout_seconds, self.timeout_seconds),
                )
            except CloudDetectionUnavailable as exc:
                last_error = str(exc)
                time.sleep(max(self.poll_interval_seconds, 1.0))
                continue
            if last.get("status") in {"done", "error"}:
                return last
            time.sleep(self.poll_interval_seconds)
        detail = last.get("status") or last_error or "unknown"
        raise CloudDetectionUnavailable(f"cloud_job_timeout_after_{self.timeout_seconds:g}s: {detail}")


cloud_detection_client = CloudDetectionClient()
