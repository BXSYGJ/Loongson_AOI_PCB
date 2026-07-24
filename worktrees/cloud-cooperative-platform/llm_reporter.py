import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import camera_trigger


DEFAULT_LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_LLM_MODEL = "qwen3.7-plus"
DEFAULT_TIMEOUT_SECONDS = 120.0
LOW_CONFIDENCE_THRESHOLD = 0.5
LLM_MODEL_CHOICES = (
    {
        "id": "qwen3.7-plus",
        "label": "Qwen3.7 Plus",
        "executed_model": "qwen3.7-plus",
    },
    {
        "id": "qwen3.6-flash",
        "label": "Qwen3.6 Flash",
        "executed_model": "qwen3.6-flash",
    },
    {
        "id": "qwen3.7-max",
        "label": "Qwen3.7 Max",
        "executed_model": "qwen3.7-max-2026-06-08",
    },
)
_LLM_MODEL_MAP = {item["id"]: item for item in LLM_MODEL_CHOICES}

_last_error: str | None = None
_local_config_cache: dict[str, str] | None = None


class LlmReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReportTarget:
    target_type: str
    target: dict[str, Any]
    image_path: Path
    result_json_path: Path
    report_md_path: Path
    report_json_path: Path
    title: str


def status() -> dict[str, Any]:
    model, executed_model = resolve_model_choice(None)
    return {
        "configured": bool(_api_key()),
        "base_url": _base_url(),
        "model": model,
        "executed_model": executed_model,
        "available_models": [dict(item) for item in LLM_MODEL_CHOICES],
        "timeout_seconds": _timeout_seconds(),
        "last_error": _last_error,
    }


def generate_report(payload: dict[str, Any]) -> dict[str, Any]:
    global _last_error
    if not _api_key():
        _last_error = "LLM API key is not configured."
        raise LlmReportError("LLM 未配置：请设置 LOONGSON_AOI_LLM_API_KEY。")

    target = resolve_report_target(payload)
    defects = _read_result_json(target.result_json_path)
    prompt = _build_prompt(target, defects)
    thinking_enabled = _bool_value(payload.get("enable_thinking"), default=False)
    model, executed_model = resolve_model_choice(payload.get("model"))
    started = time.perf_counter()
    try:
        report_markdown, response_meta = _call_chat_completions(
            prompt,
            target.image_path,
            executed_model,
            thinking_enabled,
        )
    except Exception as exc:
        _last_error = str(exc)
        raise
    generated_at = datetime.now().isoformat(timespec="seconds")
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)

    target.report_md_path.parent.mkdir(parents=True, exist_ok=True)
    target.report_md_path.write_text(report_markdown, encoding="utf-8")

    report_json = {
        "ok": True,
        "target_type": target.target_type,
        "target": target.target,
        "title": target.title,
        "model": model,
        "executed_model": executed_model,
        "thinking_enabled": thinking_enabled,
        "generated_at": generated_at,
        "elapsed_ms": elapsed_ms,
        "image": camera_trigger._rel(target.image_path),
        "result_json": camera_trigger._rel(target.result_json_path),
        "defect_count": len(defects),
        "report_markdown": report_markdown,
        "usage": response_meta.get("usage"),
        "finish_reason": response_meta.get("finish_reason"),
    }
    target.report_json_path.write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    _last_error = None
    return {
        "ok": True,
        "target_type": target.target_type,
        "target": target.target,
        "report_markdown": report_markdown,
        "report_json": camera_trigger._rel(target.report_json_path),
        "report_path": camera_trigger._rel(target.report_md_path),
        "generated_at": generated_at,
        "model": model,
        "executed_model": executed_model,
        "thinking_enabled": thinking_enabled,
        "elapsed_ms": elapsed_ms,
        "usage": response_meta.get("usage"),
        "defect_count": len(defects),
    }


def resolve_report_download(payload: dict[str, Any]) -> tuple[Path, str]:
    target_type = str(payload.get("target_type", "")).strip().lower()
    if target_type == "cell":
        scan_id = _safe_id(payload.get("scan_id"), "scan_id")
        row = _safe_int(payload.get("row"), "row")
        col = _safe_int(payload.get("col"), "col")
        report_path = (
            camera_trigger.SCAN_OUTPUT_DIR
            / scan_id
            / f"r{row:03d}_c{col:03d}"
            / "diagnosis_report.md"
        )
        prefix = f"cell_{scan_id}_r{row:03d}_c{col:03d}"
    elif target_type == "mosaic":
        scan_id = _safe_id(payload.get("scan_id"), "scan_id")
        report_path = camera_trigger.SCAN_OUTPUT_DIR / scan_id / "mosaic" / "diagnosis_report.md"
        prefix = f"mosaic_{scan_id}"
    elif target_type == "phone":
        upload_id = _safe_id(payload.get("upload_id"), "upload_id")
        report_path = camera_trigger.PHONE_UPLOAD_DIR / upload_id / "diagnosis_report.md"
        prefix = f"phone_{upload_id}"
    else:
        raise LlmReportError("target_type 必须是 cell、mosaic 或 phone。")

    if not report_path.is_file():
        raise LlmReportError("诊断报告尚未生成。")
    return report_path, f"{prefix}_diagnosis_report.md"


def resolve_report_target(payload: dict[str, Any]) -> ReportTarget:
    target_type = str(payload.get("target_type", "")).strip().lower()
    if target_type == "cell":
        scan_id = _safe_id(payload.get("scan_id"), "scan_id")
        row = _safe_int(payload.get("row"), "row")
        col = _safe_int(payload.get("col"), "col")
        cell_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id / f"r{row:03d}_c{col:03d}"
        image_path = _first_existing(cell_dir / "annotated.jpg", cell_dir / "raw.jpg", cell_dir / "raw.png")
        result_json_path = _require_file(cell_dir / "result.json", "单格检测 JSON")
        return ReportTarget(
            target_type="cell",
            target={"scan_id": scan_id, "row": row, "col": col},
            image_path=image_path,
            result_json_path=result_json_path,
            report_md_path=cell_dir / "diagnosis_report.md",
            report_json_path=cell_dir / "diagnosis_report.json",
            title=f"单格区域 r{row:03d}_c{col:03d}",
        )

    if target_type == "mosaic":
        scan_id = _safe_id(payload.get("scan_id"), "scan_id")
        mosaic_dir = camera_trigger.SCAN_OUTPUT_DIR / scan_id / "mosaic"
        image_path = _first_existing(mosaic_dir / "mosaic_annotated.jpg", mosaic_dir / "mosaic_raw.jpg", mosaic_dir / "mosaic_raw.png")
        result_json_path = _require_file(mosaic_dir / "mosaic_result.json", "整板检测 JSON")
        return ReportTarget(
            target_type="mosaic",
            target={"scan_id": scan_id},
            image_path=image_path,
            result_json_path=result_json_path,
            report_md_path=mosaic_dir / "diagnosis_report.md",
            report_json_path=mosaic_dir / "diagnosis_report.json",
            title=f"整板拼接结果 {scan_id}",
        )

    if target_type == "phone":
        upload_id = _safe_id(payload.get("upload_id"), "upload_id")
        upload_dir = camera_trigger.PHONE_UPLOAD_DIR / upload_id
        image_path = _first_existing(upload_dir / "annotated.jpg", upload_dir / "raw.jpg")
        result_json_path = _require_file(upload_dir / "result.json", "手机照片检测 JSON")
        return ReportTarget(
            target_type="phone",
            target={"upload_id": upload_id},
            image_path=image_path,
            result_json_path=result_json_path,
            report_md_path=upload_dir / "diagnosis_report.md",
            report_json_path=upload_dir / "diagnosis_report.json",
            title=f"手机整图结果 {upload_id}",
        )

    raise LlmReportError("target_type 必须是 cell、mosaic 或 phone。")


def _build_prompt(target: ReportTarget, defects: list[dict[str, Any]]) -> str:
    defect_lines = []
    for index, defect in enumerate(defects, start=1):
        label = defect.get("label") or defect.get("type") or defect.get("name") or "unknown"
        confidence = _float_or_none(defect.get("confidence", defect.get("conf")))
        bbox = defect.get("bbox") or defect.get("xyxy") or []
        note = "，低置信度，建议复核" if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD else ""
        confidence_text = f"{confidence:.3f}" if confidence is not None else "未知"
        defect_lines.append(f"{index}. 类别={label}，置信度={confidence_text}，bbox={bbox}{note}")
    defect_summary = "\n".join(defect_lines) if defect_lines else "未发现模型输出的缺陷框。"
    result_json = json.dumps(defects, ensure_ascii=False, indent=2)
    if len(result_json) > 12000:
        result_json = result_json[:12000] + "\n...（JSON 已截断，仅用于报告摘要）"

    return f"""请根据随附的 PCB AOI 标注图和检测 JSON，生成一份中文 Markdown 诊断报告。

报告对象：{target.title}
目标类型：{target.target_type}

必须遵守：
1. 开头必须明确写出：诊断基于机器视觉检测结果，并非最终人工判定。
2. 必须引用缺陷类别、置信度、bbox 位置框信息。
3. 对置信度低于 {LOW_CONFIDENCE_THRESHOLD:.2f} 的缺陷标注为“建议复核”，不要过度诊断。
4. 如果没有缺陷，给出“未发现明显缺陷，但仍建议人工抽检复核”的结论。
5. 不要编造图中不存在的器件编号；只能基于图片可见信息和检测 JSON 推断。

请包含以下 Markdown 小节：
### 缺陷概述
### 风险等级
### 可能原因
### 复检建议
### 维修建议
### 置信度说明

缺陷摘要：
{defect_summary}

检测 JSON：
```json
{result_json}
```
"""


def _call_chat_completions(
    prompt: str,
    image_path: Path,
    model: str,
    enable_thinking: bool,
) -> tuple[str, dict[str, Any]]:
    image_url = _image_data_url(image_path)
    body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是工业 AOI PCB 缺陷诊断助手，擅长把机器视觉结果整理成谨慎、可复核的中文工程报告。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        "temperature": 0.2,
        "enable_thinking": enable_thinking,
    }
    request = urllib.request.Request(
        _chat_url(),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "HTTP-Referer": os.getenv("LOONGSON_AOI_LLM_APP_URL", "http://localhost").strip() or "http://localhost",
            "X-Title": os.getenv("LOONGSON_AOI_LLM_APP_TITLE", "Loongson AOI PCB").strip() or "Loongson AOI PCB",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_timeout_seconds()) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LlmReportError(f"LLM API 返回 HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise LlmReportError(f"LLM API 连接失败: {exc.reason}") from exc

    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        content = choice["message"]["content"]
    except Exception as exc:
        raise LlmReportError("LLM API 响应格式无法解析。") from exc
    if not isinstance(content, str) or not content.strip():
        raise LlmReportError("LLM API 返回了空报告。")
    return content.strip(), {
        "usage": data.get("usage"),
        "finish_reason": choice.get("finish_reason"),
    }


def _read_result_json(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LlmReportError(f"检测 JSON 无法读取: {path}") from exc
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("defects", "detections", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise LlmReportError("检测 JSON 中没有可用的缺陷列表。")


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _safe_id(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z_-]{6,120}", text):
        raise LlmReportError(f"{name} 格式不合法。")
    return text


def _safe_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except Exception as exc:
        raise LlmReportError(f"{name} 必须是整数。") from exc
    if number < 0 or number > 10000:
        raise LlmReportError(f"{name} 超出允许范围。")
    return number


def _first_existing(*paths: Path) -> Path:
    for path in paths:
        if path.exists() and path.is_file():
            return path
    names = ", ".join(str(path) for path in paths)
    raise LlmReportError(f"缺少报告图片文件: {names}")


def _require_file(path: Path, label: str) -> Path:
    if not path.exists() or not path.is_file():
        raise LlmReportError(f"缺少{label}: {path}")
    return path


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _bool_value(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise LlmReportError("enable_thinking 必须是布尔值。")


def _local_config() -> dict[str, str]:
    global _local_config_cache
    if _local_config_cache is not None:
        return _local_config_cache
    config: dict[str, str] = {}
    candidates = [
        Path(os.getenv("LOONGSON_AOI_LLM_CONFIG_FILE", "")).expanduser() if os.getenv("LOONGSON_AOI_LLM_CONFIG_FILE") else None,
        Path(__file__).with_name("llm_config.local.ps1"),
        Path(__file__).with_name(".env"),
    ]
    assignment = re.compile(r"\$(ApiKey|Model|BaseUrl|TimeoutSeconds|WorkspaceId)\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
    export_assignment = re.compile(r"\$env:(LOONGSON_AOI_LLM_[A-Z0-9_]+)\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
    for path in candidates:
        if path is None or not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in assignment.finditer(text):
            config[match.group(1).lower()] = match.group(2).strip()
        for match in export_assignment.finditer(text):
            config[match.group(1).upper()] = match.group(2).strip()
    _local_config_cache = config
    return config


def _config_value(env_name: str, ps_name: str, default: str = "") -> str:
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    local = _local_config()
    return (local.get(env_name) or local.get(ps_name.lower()) or default).strip()


def _api_key() -> str:
    return _config_value("LOONGSON_AOI_LLM_API_KEY", "ApiKey")


def _base_url() -> str:
    return _config_value("LOONGSON_AOI_LLM_BASE_URL", "BaseUrl", DEFAULT_LLM_BASE_URL).rstrip("/")


def _model() -> str:
    configured = _config_value("LOONGSON_AOI_LLM_MODEL", "Model", DEFAULT_LLM_MODEL) or DEFAULT_LLM_MODEL
    if configured in _LLM_MODEL_MAP:
        return configured
    for item in LLM_MODEL_CHOICES:
        if configured == item["executed_model"]:
            return item["id"]
    return DEFAULT_LLM_MODEL


def resolve_model_choice(value: Any) -> tuple[str, str]:
    model = str(value or "").strip() or _model()
    choice = _LLM_MODEL_MAP.get(model)
    if choice is None:
        allowed = "、".join(item["id"] for item in LLM_MODEL_CHOICES)
        raise LlmReportError(f"不支持的 LLM 模型；可选值为：{allowed}。")
    return choice["id"], choice["executed_model"]


def _timeout_seconds() -> float:
    value = _config_value("LOONGSON_AOI_LLM_TIMEOUT_SECONDS", "TimeoutSeconds", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        return max(5.0, float(value))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _chat_url() -> str:
    base = _base_url()
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"
