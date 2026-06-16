import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
RESULT_JSON = ROOT / "algorithm" / "outputs" / "detections" / "result.json"
DEFAULT_WEIGHTS = ROOT / "runs" / "detect" / "pcb_defects" / "weights" / "best.pt"
DEFAULT_IMAGE = ROOT / "algorithm" / "outputs" / "camera" / "latest.jpg"


class CameraTriggerError(RuntimeError):
    pass


def resolve_detect_script() -> Path:
    preferred = ROOT / "algorithm" / "scripts" / "detect.py"
    fallback = ROOT / "algorithm" / "core" / "detect.py"
    if preferred.exists():
        return preferred
    if fallback.exists():
        return fallback
    raise CameraTriggerError("No YOLO detect script found in algorithm/scripts or algorithm/core.")


async def capture_detect_and_read(
    row: int,
    col: int,
    x: float,
    y: float,
    image_path: str | Path | None,
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    image = Path(image_path).resolve() if image_path else DEFAULT_IMAGE
    weights = DEFAULT_WEIGHTS
    detect_script = resolve_detect_script()

    if RESULT_JSON.exists():
        RESULT_JSON.unlink()

    if not image.exists():
        return {
            "ok": False,
            "error": "capture_image_missing",
            "message": f"Capture image does not exist: {image}",
            "row": row,
            "col": col,
            "x_mm": x,
            "y_mm": y,
        }

    command = [
        sys.executable,
        str(detect_script),
        "--weights",
        str(weights),
        "--image",
        str(image),
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(ROOT),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    communicate_task = asyncio.create_task(process.communicate())
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            {communicate_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if stop_task in done and stop_event.is_set():
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            communicate_task.cancel()
            return {
                "ok": False,
                "error": "scan_stopped",
                "message": "YOLO subprocess terminated by stop_scan.",
                "row": row,
                "col": col,
                "x_mm": x,
                "y_mm": y,
            }

        stdout, stderr = await communicate_task
    finally:
        stop_task.cancel()

    if process.returncode != 0:
        return {
            "ok": False,
            "error": "detect_process_failed",
            "message": stderr.decode("utf-8", errors="replace")[-2000:],
            "stdout": stdout.decode("utf-8", errors="replace")[-1000:],
            "returncode": process.returncode,
            "row": row,
            "col": col,
            "x_mm": x,
            "y_mm": y,
        }

    if not RESULT_JSON.exists():
        return {
            "ok": False,
            "error": "result_json_missing",
            "message": "YOLO process exited successfully but result.json was not regenerated.",
            "row": row,
            "col": col,
            "x_mm": x,
            "y_mm": y,
        }

    with RESULT_JSON.open("r", encoding="utf-8") as file:
        defects = json.load(file)

    return {
        "ok": True,
        "defects": defects,
        "result_json": str(RESULT_JSON.relative_to(ROOT)).replace("\\", "/"),
        "row": row,
        "col": col,
        "x_mm": x,
        "y_mm": y,
    }
