from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import cv2

import camera_trigger
import llm_reporter


CHAT_OUTPUT_DIR = camera_trigger.ALGORITHM_ROOT / "algorithm" / "outputs" / "llm_chats"
MAX_PHONE_IMAGES = 9
MAX_CONTEXT_ROUNDS = 12
MAX_IMAGE_LONG_EDGE = 1600
_SESSION_LOCKS: dict[str, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


class LlmChatError(RuntimeError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z_-]{6,120}", text):
        raise LlmChatError(f"{field} 格式无效。")
    return text


def _session_lock(session_id: str) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _session_path(session_id: str) -> Path:
    safe_session_id = _safe_id(session_id, "session_id")
    path = (CHAT_OUTPUT_DIR / safe_session_id / "session.json").resolve()
    root = CHAT_OUTPUT_DIR.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise LlmChatError("会话路径越界。") from exc
    return path


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _scope_from_payload(payload: dict[str, Any], validate_source: bool) -> dict[str, str]:
    mode = str(payload.get("mode") or "").strip().lower()
    if mode in {"real", "simulation"}:
        scan_id = _safe_id(payload.get("scan_id"), "scan_id")
        return {"mode": mode, "scan_id": scan_id}
    if mode != "phone":
        raise LlmChatError("mode 必须是 real、simulation 或 phone。")

    batch_id = str(payload.get("batch_id") or "").strip()
    upload_id = str(payload.get("upload_id") or "").strip()
    if batch_id:
        batch_id = _safe_id(batch_id, "batch_id")
        if validate_source:
            try:
                camera_trigger.get_phone_batch(batch_id)
            except camera_trigger.CameraTriggerError as exc:
                raise LlmChatError(str(exc)) from exc
        return {"mode": "phone", "batch_id": batch_id}
    upload_id = _safe_id(upload_id, "upload_id")
    if validate_source:
        try:
            metadata = camera_trigger.get_phone_upload(upload_id)
        except camera_trigger.CameraTriggerError as exc:
            raise LlmChatError(str(exc)) from exc
        metadata_batch_id = str(metadata.get("batch_id") or "").strip()
        if metadata_batch_id:
            return {"mode": "phone", "batch_id": _safe_id(metadata_batch_id, "batch_id")}
    return {"mode": "phone", "upload_id": upload_id}


def _session_id_for_scope(scope: dict[str, str]) -> str:
    mode = scope["mode"]
    if mode in {"real", "simulation"}:
        return f"chat_{mode}_{scope['scan_id']}"
    if scope.get("batch_id"):
        return f"chat_phone_{scope['batch_id']}"
    return f"chat_phone_{scope['upload_id']}"


def ensure_session(payload: dict[str, Any], validate_source: bool = True) -> dict[str, Any]:
    scope = _scope_from_payload(payload, validate_source=validate_source)
    session_id = _session_id_for_scope(scope)
    path = _session_path(session_id)
    lock = _session_lock(session_id)
    with lock:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        session = {
            "session_id": session_id,
            "scope": scope,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "messages": [],
        }
        _atomic_write_json(path, session)
        return session


def load_session(session_id: str) -> dict[str, Any]:
    path = _session_path(session_id)
    if not path.is_file():
        raise LlmChatError("聊天会话不存在。")
    with _session_lock(session_id):
        return json.loads(path.read_text(encoding="utf-8"))


def append_message(session: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    session_id = _safe_id(session.get("session_id"), "session_id")
    path = _session_path(session_id)
    with _session_lock(session_id):
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else dict(session)
        item = dict(message)
        item.setdefault("created_at", _now_iso())
        current.setdefault("messages", []).append(item)
        current["updated_at"] = _now_iso()
        _atomic_write_json(path, current)
        return current


def recent_messages(messages: list[dict[str, Any]], rounds: int = MAX_CONTEXT_ROUNDS) -> list[dict[str, str]]:
    limit = max(1, int(rounds)) * 2
    usable = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")}
        for item in messages
        if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
    ]
    return usable[-limit:]


def parse_openai_sse(lines: Iterable[bytes | str]) -> Iterator[dict[str, Any]]:
    for raw_line in lines:
        line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        if data_text == "[DONE]":
            yield {"type": "done"}
            continue
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            continue
        usage = payload.get("usage")
        if usage:
            yield {"type": "usage", "usage": usage}
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            reasoning = delta.get("reasoning_content")
            content = delta.get("content")
            if reasoning:
                yield {"type": "reasoning", "content": str(reasoning)}
            if content:
                yield {"type": "content", "content": str(content)}


def _read_defects(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise LlmChatError(f"检测结果不存在: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LlmChatError(f"检测结果无法读取: {path.name}") from exc
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    for key in ("defects", "detections", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalized_bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise LlmChatError("缺陷 bbox 格式无效。")
    try:
        return [round(float(item), 2) for item in value]
    except (TypeError, ValueError) as exc:
        raise LlmChatError("缺陷 bbox 格式无效。") from exc


def _defect_matches(candidate: dict[str, Any], reference: dict[str, Any]) -> bool:
    candidate_type = str(candidate.get("type") or candidate.get("label") or candidate.get("name") or "")
    reference_type = str(reference.get("type") or reference.get("label") or reference.get("name") or "")
    if candidate_type != reference_type:
        return False
    try:
        candidate_bbox = _normalized_bbox(candidate.get("bbox") or candidate.get("xyxy"))
        reference_bbox = _normalized_bbox(reference.get("bbox") or reference.get("xyxy"))
    except LlmChatError:
        return False
    return all(abs(left - right) <= 2.0 for left, right in zip(candidate_bbox, reference_bbox))


def _first_image(*paths: Path) -> Path | None:
    return next((path for path in paths if path.is_file()), None)


def _resolve_scan_context(payload: dict[str, Any]) -> dict[str, Any]:
    scan_id = _safe_id(payload.get("scan_id"), "scan_id")
    scan_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id
    if not scan_dir.is_dir():
        raise LlmChatError("扫描结果不存在。")
    references = payload.get("selected_defects") or []
    if not isinstance(references, list):
        raise LlmChatError("selected_defects 必须是数组。")
    images: list[Path] = []
    defects: list[dict[str, Any]] = []
    seen_images: set[str] = set()
    for reference in references:
        if not isinstance(reference, dict):
            raise LlmChatError("缺陷引用格式无效。")
        row = int(reference.get("row"))
        col = int(reference.get("col"))
        if row < 0 or col < 0 or row > 999 or col > 999:
            raise LlmChatError("缺陷来源格坐标无效。")
        cell_dir = scan_dir / f"r{row:03d}_c{col:03d}"
        candidates = _read_defects(cell_dir / "result.json")
        match = next((item for item in candidates if _defect_matches(item, reference)), None)
        if match is None:
            raise LlmChatError(f"缺陷引用与区域 {row},{col} 的检测结果不匹配。")
        validated = dict(match)
        validated["source_cell"] = {"row": row, "col": col}
        defects.append(validated)
        image = _first_image(cell_dir / "annotated.jpg", cell_dir / "raw.jpg", cell_dir / "raw.png")
        if image and str(image.resolve()) not in seen_images:
            seen_images.add(str(image.resolve()))
            images.append(image)
    mosaic_dir = scan_dir / "mosaic"
    mosaic_image = _first_image(
        mosaic_dir / "mosaic_annotated.jpg",
        mosaic_dir / "mosaic_raw.jpg",
        mosaic_dir / "mosaic_raw.png",
    )
    if mosaic_image and str(mosaic_image.resolve()) not in seen_images:
        images.append(mosaic_image)
    if not references:
        target = payload.get("report_target") or {}
        target_type = str(target.get("target_type") or "mosaic")
        if target_type == "cell":
            row = int(target.get("row"))
            col = int(target.get("col"))
            cell_dir = scan_dir / f"r{row:03d}_c{col:03d}"
            defects = _read_defects(cell_dir / "result.json")
            cell_image = _first_image(cell_dir / "annotated.jpg", cell_dir / "raw.jpg", cell_dir / "raw.png")
            images = [item for item in (cell_image, mosaic_image) if item]
        else:
            defects = _read_defects(mosaic_dir / "mosaic_result.json")
            images = [mosaic_image] if mosaic_image else []
    if not images:
        raise LlmChatError("所选扫描上下文没有可用图片。")
    return {
        "summary": f"扫描 {scan_id}，已选择 {len(defects)} 个缺陷，附带 {len(images)} 张图。",
        "images": images,
        "defects": defects,
        "snapshot": {"scan_id": scan_id, "defects": defects},
    }


def _resolve_phone_context(payload: dict[str, Any]) -> dict[str, Any]:
    upload_ids = payload.get("selected_upload_ids") or []
    if not isinstance(upload_ids, list):
        raise LlmChatError("selected_upload_ids 必须是数组。")
    normalized = []
    for value in upload_ids:
        upload_id = _safe_id(value, "upload_id")
        if upload_id not in normalized:
            normalized.append(upload_id)
    if len(normalized) > MAX_PHONE_IMAGES:
        raise LlmChatError("聊天上下文最多选择 9 张手机照片。")
    if not normalized:
        fallback = payload.get("upload_id")
        if fallback:
            normalized = [_safe_id(fallback, "upload_id")]
    if not normalized:
        raise LlmChatError("请至少选择一张手机照片。")
    images: list[Path] = []
    snapshots: list[dict[str, Any]] = []
    all_defects: list[dict[str, Any]] = []
    for upload_id in normalized:
        try:
            metadata = camera_trigger.get_phone_upload(upload_id)
        except camera_trigger.CameraTriggerError as exc:
            raise LlmChatError(str(exc)) from exc
        image_relative = metadata.get("annotated_image") or metadata.get("raw_image")
        if not image_relative:
            raise LlmChatError(f"手机照片 {upload_id} 没有可用图像。")
        image_path = camera_trigger.resolve_artifact(str(image_relative))
        if not image_path.is_file():
            raise LlmChatError(f"手机照片 {upload_id} 的图像文件不存在。")
        defects = list(metadata.get("defects") or [])
        images.append(image_path)
        all_defects.extend(defects)
        snapshots.append(
            {
                "upload_id": upload_id,
                "name": metadata.get("name"),
                "status": metadata.get("status"),
                "defects": defects,
                "image_kind": "annotated" if metadata.get("annotated_image") else "raw",
            }
        )
    return {
        "summary": f"已选择 {len(images)} 张手机照片，共包含 {len(all_defects)} 个检测缺陷。",
        "images": images,
        "defects": all_defects,
        "snapshot": {"uploads": snapshots},
    }


def resolve_chat_context(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "").strip().lower()
    if mode in {"real", "simulation"}:
        return _resolve_scan_context(payload)
    if mode == "phone":
        return _resolve_phone_context(payload)
    raise LlmChatError("mode 必须是 real、simulation 或 phone。")


def _image_data_url(path: Path) -> str:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise LlmChatError(f"图片无法读取: {path.name}")
    height, width = image.shape[:2]
    long_edge = max(width, height)
    if long_edge > MAX_IMAGE_LONG_EDGE:
        scale = MAX_IMAGE_LONG_EDGE / float(long_edge)
        image = cv2.resize(
            image,
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise LlmChatError(f"图片压缩失败: {path.name}")
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def _context_prompt(message: str, context: dict[str, Any]) -> str:
    details = json.dumps(context.get("snapshot") or {}, ensure_ascii=False, indent=2)
    if len(details) > 16000:
        details = details[:16000] + "\n...（上下文已截断）"
    return (
        f"用户要求：{message.strip()}\n\n"
        f"AOI 上下文摘要：{context['summary']}\n"
        f"经服务端校验的检测上下文：\n```json\n{details}\n```\n"
        "请基于所附图片和检测信息谨慎回答；机器视觉结论不是最终人工判定，低置信度项目应建议复核。"
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_request(messages: list[dict[str, Any]], model: str, enable_thinking: bool):
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "enable_thinking": enable_thinking,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        llm_reporter._chat_url(),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {llm_reporter._api_key()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": os.getenv("LOONGSON_AOI_LLM_APP_URL", "http://localhost").strip() or "http://localhost",
            "X-Title": os.getenv("LOONGSON_AOI_LLM_APP_TITLE", "Loongson AOI PCB").strip() or "Loongson AOI PCB",
        },
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=llm_reporter._timeout_seconds())


def stream_chat(payload: dict[str, Any]) -> Iterator[str]:
    if not llm_reporter.status().get("configured"):
        yield _sse("error", {"message": "LLM 未配置。"})
        return
    message = str(payload.get("message") or "").strip()
    if not message and not payload.get("selected_defects") and not payload.get("selected_upload_ids"):
        yield _sse("error", {"message": "请输入问题或选择检测上下文。"})
        return
    started = time.perf_counter()
    try:
        session = ensure_session(payload)
        context = resolve_chat_context(payload)
        model_key, executed_model = llm_reporter.resolve_model_choice(payload.get("model"))
        enable_thinking = llm_reporter._bool_value(payload.get("enable_thinking"), default=False)
        yield _sse("session", {"session_id": session["session_id"], "scope": session["scope"]})
        yield _sse("phase", {"phase": "preparing", "message": context["summary"]})
        history = recent_messages(list(session.get("messages") or []))
        user_prompt = _context_prompt(message or "请分析所选检测上下文。", context)
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image_path in context["images"]:
            content.append({"type": "image_url", "image_url": {"url": _image_data_url(image_path)}})
        upstream_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "你是工业 AOI PCB 缺陷诊断助手。回答必须谨慎、可复核，并使用中文 Markdown。",
            },
            *history,
            {"role": "user", "content": content},
        ]
        append_message(
            session,
            {
                "role": "user",
                "content": message or "请分析所选检测上下文。",
                "context_summary": context["summary"],
                "context": context["snapshot"],
            },
        )
        assistant_parts: list[str] = []
        usage = None
        thinking_started = False
        with _chat_request(upstream_messages, executed_model, enable_thinking) as response:
            for event in parse_openai_sse(response):
                if event["type"] == "reasoning" and not thinking_started:
                    thinking_started = True
                    yield _sse("phase", {"phase": "thinking", "message": "深度思考中"})
                elif event["type"] == "content":
                    if not assistant_parts:
                        yield _sse("phase", {"phase": "answering", "message": "正在生成回答"})
                    assistant_parts.append(event["content"])
                    yield _sse("delta", {"content": event["content"]})
                elif event["type"] == "usage":
                    usage = event["usage"]
        answer = "".join(assistant_parts).strip()
        if not answer:
            raise LlmChatError("LLM 返回了空回答。")
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        append_message(
            session,
            {
                "role": "assistant",
                "content": answer,
                "kind": "chat",
                "model": model_key,
                "executed_model": executed_model,
                "thinking_enabled": enable_thinking,
                "elapsed_ms": elapsed_ms,
                "usage": usage,
            },
        )
        yield _sse(
            "done",
            {
                "session_id": session["session_id"],
                "content": answer,
                "model": model_key,
                "executed_model": executed_model,
                "thinking_enabled": enable_thinking,
                "elapsed_ms": elapsed_ms,
                "usage": usage,
            },
        )
    except GeneratorExit:
        return
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        yield _sse("error", {"message": f"LLM API 返回 HTTP {exc.code}: {detail[:500]}"})
    except urllib.error.URLError as exc:
        yield _sse("error", {"message": f"LLM API 连接失败: {exc.reason}"})
    except (LlmChatError, llm_reporter.LlmReportError, camera_trigger.CameraTriggerError, ValueError, TypeError) as exc:
        yield _sse("error", {"message": str(exc)})


def _scope_payload_from_report(payload: dict[str, Any]) -> dict[str, Any]:
    target_type = str(payload.get("target_type") or "").strip().lower()
    if target_type in {"cell", "mosaic"}:
        return {
            "mode": str(payload.get("mode") or "real").strip().lower(),
            "scan_id": payload.get("scan_id"),
        }
    if target_type == "phone":
        upload_id = _safe_id(payload.get("upload_id"), "upload_id")
        try:
            metadata = camera_trigger.get_phone_upload(upload_id)
        except camera_trigger.CameraTriggerError:
            metadata = {}
        return {
            "mode": "phone",
            "batch_id": metadata.get("batch_id"),
            "upload_id": upload_id,
        }
    raise LlmChatError("无法为该报告目标确定聊天会话。")


def append_report(payload: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    session = ensure_session(_scope_payload_from_report(payload), validate_source=False)
    return append_message(
        session,
        {
            "role": "assistant",
            "kind": "report",
            "content": str(report.get("report_markdown") or ""),
            "model": report.get("model"),
            "executed_model": report.get("executed_model"),
            "thinking_enabled": bool(report.get("thinking_enabled")),
            "elapsed_ms": report.get("elapsed_ms"),
            "usage": report.get("usage"),
            "report_target": {
                key: payload.get(key)
                for key in ("target_type", "scan_id", "row", "col", "upload_id")
                if payload.get(key) is not None
            },
        },
    )


def session_markdown(session: dict[str, Any]) -> str:
    scope = json.dumps(session.get("scope") or {}, ensure_ascii=False)
    lines = ["# AOI AI 诊断会话", "", f"- 会话：`{session.get('session_id', '')}`", f"- 检测对象：`{scope}`", ""]
    for item in session.get("messages") or []:
        role = "用户" if item.get("role") == "user" else "AI"
        kind = "（诊断报告）" if item.get("kind") == "report" else ""
        lines.extend([f"## {role}{kind}", ""])
        if item.get("context_summary"):
            lines.extend([f"> 上下文：{item['context_summary']}", ""])
        lines.extend([str(item.get("content") or ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_session_markdown(session: dict[str, Any]) -> tuple[Path, str]:
    session_id = _safe_id(session.get("session_id"), "session_id")
    path = _session_path(session_id).with_name("conversation.md")
    path.write_text(session_markdown(session), encoding="utf-8")
    return path, f"{session_id}_conversation.md"
