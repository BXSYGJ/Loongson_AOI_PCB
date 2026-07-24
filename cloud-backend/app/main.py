from __future__ import annotations

import asyncio
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import CloudSettings
from .detector import create_detector
from .jobs import JobError, JobManager, job_event, job_payload
from .storage import LocalStorage, StorageError, create_storage


def create_app(settings: CloudSettings | None = None) -> FastAPI:
    resolved_settings = settings or CloudSettings.from_env()
    storage = create_storage(resolved_settings)
    detector = create_detector(resolved_settings)
    manager = JobManager(resolved_settings, storage, detector)

    app = FastAPI(title="Loongson AOI Cloud Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = resolved_settings
    app.state.storage = storage
    app.state.detector = detector
    app.state.manager = manager

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "storage": storage.status(), "detector": detector.status()}

    @app.post("/api/cloud/jobs")
    async def create_job(request: Request) -> dict[str, Any]:
        payload = await request.json()
        job = manager.create_job(
            filename=str(payload.get("filename") or "pcb.jpg"),
            profile=str(payload.get("profile") or "precision"),
            client_type=str(payload.get("client_type") or "miniprogram"),
        )
        return job_payload(job)

    @app.put("/api/cloud/local-upload/{job_id}")
    async def local_upload(job_id: str, request: Request) -> dict[str, Any]:
        if not isinstance(storage, LocalStorage):
            raise HTTPException(status_code=404, detail="Local upload endpoint is only available for local storage.")
        object_key = request.query_params.get("object_key", "")
        try:
            job = manager.get_job(job_id)
            if object_key != job.object_key:
                raise JobError("Object key does not match job.")
            content = await request.body()
            if not content:
                raise JobError("Upload body is empty.")
            storage.put_bytes(object_key, content)
            return {"status": "uploaded", "job_id": job_id, "object_key": object_key, "size_bytes": len(content)}
        except (JobError, StorageError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/cloud/jobs/{job_id}/uploaded")
    async def uploaded(job_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
        payload = await request.json()
        try:
            job = manager.mark_uploaded(job_id, str(payload.get("object_key") or ""), int(payload.get("size_bytes") or 0))
        except JobError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        async def process_and_notify() -> None:
            try:
                await manager.broadcast(job_id, job_event(job))
                detecting = manager.get_job(job_id)
                detecting.status = "detecting"
                await manager.broadcast(job_id, job_event(detecting))
                done = await asyncio.to_thread(manager.process_job, job_id)
                await manager.broadcast(job_id, job_event(done))
            except Exception:
                try:
                    failed = manager.get_job(job_id)
                    await manager.broadcast(job_id, job_event(failed))
                except Exception:
                    pass

        background_tasks.add_task(process_and_notify)
        return {"status": "queued", "job_id": job_id}

    @app.get("/api/cloud/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            return job_payload(manager.get_job(job_id))
        except JobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/cloud/files/{object_key:path}")
    async def local_file(object_key: str) -> FileResponse:
        if not isinstance(storage, LocalStorage):
            raise HTTPException(status_code=404, detail="Local file endpoint is only available for local storage.")
        path = storage._path(object_key)
        if not path.exists():
            raise HTTPException(status_code=404, detail="File not found.")
        return FileResponse(path)

    @app.websocket("/ws/cloud/jobs/{job_id}")
    async def job_websocket(websocket: WebSocket, job_id: str) -> None:
        try:
            await manager.connect(job_id, websocket)
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            manager.disconnect(job_id, websocket)
        except JobError:
            await websocket.close(code=4404)

    return app


app = create_app()
