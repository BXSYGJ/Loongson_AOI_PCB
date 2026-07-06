import json
import os
import platform
import re
import threading
import time
from pathlib import Path
from typing import Any

import camera_trigger


DEFAULT_CLASS_NAMES = {
    0: "missing_hole",
    1: "mouse_bite",
    2: "open_circuit",
    3: "short_circuit",
    4: "spur",
    5: "spurious_copper",
    6: "missing_component",
    7: "improper_installation",
    8: "excess_solder",
    9: "dry_solder_joint",
    10: "reverse_component",
}

DEFAULT_CN_NAMES = {
    "missing_hole": "漏孔",
    "mouse_bite": "鼠咬",
    "open_circuit": "断路",
    "short_circuit": "短路",
    "spur": "毛刺",
    "spurious_copper": "杂铜",
    "missing_component": "缺件",
    "improper_installation": "安装不良",
    "excess_solder": "多锡",
    "dry_solder_joint": "虚焊",
    "reverse_component": "反向",
}


class LocalDnnError(RuntimeError):
    pass


def _env_float(name: str, fallback: float) -> float:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return fallback
    try:
        return float(value)
    except ValueError:
        return fallback


def _env_int(name: str, fallback: int) -> int:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return fallback
    try:
        return int(float(value))
    except ValueError:
        return fallback


def _env_bool(name: str, fallback: bool) -> bool:
    value = os.getenv(name)
    if value is None or not str(value).strip():
        return fallback
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _is_loongson_host() -> bool:
    return "loong" in platform.machine().lower()


class LocalDnnDetector:
    """OpenCV DNN detector adapted from loong_detect/detect_opencv_dnn.py."""

    def __init__(self) -> None:
        self.edge_safe_mode = _env_bool("LOONGSON_AOI_EDGE_SAFE_MODE", _is_loongson_host())
        self.model_path = Path(os.getenv("LOONGSON_AOI_LOCAL_DNN_MODEL", str(camera_trigger.DEFAULT_WEIGHTS))).expanduser()
        self.config_path = Path(os.getenv("LOONGSON_AOI_LOCAL_DNN_CONFIG", str(camera_trigger.DEFAULT_CONFIG))).expanduser()
        self.imgsz = _env_int("LOONGSON_AOI_LOCAL_DNN_IMGSZ", 1024)
        self.conf = _env_float("LOONGSON_AOI_LOCAL_DNN_CONF", _env_float("LOONGSON_AOI_YOLO_CONF", 0.5))
        self.iou = _env_float("LOONGSON_AOI_LOCAL_DNN_IOU", 0.45)
        self.opencv_threads = _env_int("LOONGSON_AOI_LOCAL_DNN_OPENCV_THREADS", 1 if self.edge_safe_mode else 0)
        self.max_source_dim = _env_int("LOONGSON_AOI_LOCAL_DNN_MAX_SOURCE_DIM", 1280 if self.edge_safe_mode else 0)
        self._lock = threading.Lock()
        self._net: Any | None = None
        self._class_names: dict[int, str] = dict(DEFAULT_CLASS_NAMES)
        self._cn_names: dict[str, str] = dict(DEFAULT_CN_NAMES)
        self._loaded_at: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "backend": "opencv_dnn",
            "configured": self.model_path.exists(),
            "model": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "config": str(self.config_path),
            "config_exists": self.config_path.exists(),
            "imgsz": self.imgsz,
            "conf": self.conf,
            "iou": self.iou,
            "edge_safe_mode": self.edge_safe_mode,
            "opencv_threads": self.opencv_threads,
            "max_source_dim": self.max_source_dim,
            "loaded": self._net is not None,
            "loaded_at": self._loaded_at,
            "last_error": self._last_error,
        }

    def ensure_loaded(self) -> None:
        if self._net is not None:
            return
        with self._lock:
            if self._net is not None:
                return
            try:
                if not self.model_path.exists():
                    raise LocalDnnError(f"Local DNN ONNX model not found: {self.model_path}")
                cv2 = camera_trigger._import_cv2()
                if self.opencv_threads > 0:
                    cv2.setNumThreads(self.opencv_threads)
                self._class_names, self._cn_names = self._load_label_maps()
                net = cv2.dnn.readNetFromONNX(str(self.model_path))
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                self._net = net
                self._loaded_at = time.strftime("%Y-%m-%dT%H:%M:%S")
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def detect_image(self, raw_path: str | Path, annotated_path: str | Path, result_json_path: str | Path) -> dict[str, Any]:
        self.ensure_loaded()
        cv2 = camera_trigger._import_cv2()
        import numpy as np
        raw_candidate = Path(raw_path).expanduser()
        raw = raw_candidate if raw_candidate.is_absolute() else camera_trigger.resolve_artifact(raw_path)
        annotated = Path(annotated_path)
        result_json = Path(result_json_path)
        image = cv2.imread(str(raw))
        if image is None:
            raise LocalDnnError(f"Could not read image: {raw}")
        image = self._resize_for_edge_safe_mode(image, cv2)

        started = time.perf_counter()
        padded, scale, pad_x, pad_y = self._letterbox(image, self.imgsz, cv2, np)
        blob = cv2.dnn.blobFromImage(padded, 1.0 / 255.0, (self.imgsz, self.imgsz), swapRB=True, crop=False)
        with self._lock:
            if self._net is None:
                raise LocalDnnError("Local DNN model is not loaded.")
            self._net.setInput(blob)
            output = self._net.forward()
        raw_detections = self._postprocess(output, image.shape[:2], scale, pad_x, pad_y, self.conf, self.iou, cv2, np)

        detections: list[dict[str, Any]] = []
        annotated_image = image.copy()
        for raw_detection in raw_detections:
            class_id = int(raw_detection["class_id"])
            defect_type = self._class_names.get(class_id, f"class_{class_id}")
            label = self._cn_names.get(defect_type, defect_type)
            bbox = raw_detection["bbox"]
            confidence = float(raw_detection["confidence"])
            detections.append(
                {
                    "type": defect_type,
                    "label": label,
                    "bbox": bbox,
                    "confidence": round(confidence, 4),
                }
            )
            self._draw_detection(annotated_image, bbox, label, confidence, cv2)

        annotated.parent.mkdir(parents=True, exist_ok=True)
        result_json.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(annotated), annotated_image):
            raise LocalDnnError(f"Failed to write annotated image: {annotated}")
        result_json.write_text(json.dumps(detections, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            camera_trigger.RESULT_IMAGE.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(camera_trigger.RESULT_IMAGE), annotated_image)
            camera_trigger.RESULT_JSON.write_text(json.dumps(detections, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        inference_ms = (time.perf_counter() - started) * 1000.0
        self._last_error = None
        return {
            "ok": True,
            "defects": detections,
            "raw_image": self._safe_rel(raw),
            "annotated_image": camera_trigger._rel(annotated),
            "result_json": camera_trigger._rel(result_json),
            "inference_ms": round(inference_ms, 2),
            "inference_location": "local_dnn",
            "detection_platform": "local_dnn",
            "local_fallback": False,
            "detection_details": {
                "backend": "opencv_dnn",
                "imgsz": self.imgsz,
                "conf": self.conf,
                "iou": self.iou,
                "model": str(self.model_path),
                "edge_safe_mode": self.edge_safe_mode,
                "opencv_threads": self.opencv_threads,
                "max_source_dim": self.max_source_dim,
            },
        }

    def _resize_for_edge_safe_mode(self, image: Any, cv2: Any) -> Any:
        if self.max_source_dim <= 0:
            return image
        height, width = image.shape[:2]
        longest = max(height, width)
        if longest <= self.max_source_dim:
            return image
        scale = float(self.max_source_dim) / float(longest)
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        return cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _safe_rel(path: Path) -> str:
        try:
            return camera_trigger._rel(path)
        except Exception:
            return str(path)

    def _load_label_maps(self) -> tuple[dict[int, str], dict[str, str]]:
        if not self.config_path.exists():
            return dict(DEFAULT_CLASS_NAMES), dict(DEFAULT_CN_NAMES)
        try:
            import yaml  # type: ignore

            with self.config_path.open("r", encoding="utf-8") as file:
                config = yaml.safe_load(file) or {}
            names = {int(class_id): str(name) for class_id, name in (config.get("names") or {}).items()}
            cn_names = {str(key): str(value) for key, value in (config.get("cn_names") or {}).items()}
            return names or dict(DEFAULT_CLASS_NAMES), cn_names or dict(DEFAULT_CN_NAMES)
        except Exception:
            text = self.config_path.read_text(encoding="utf-8", errors="ignore")
            names = self._parse_yaml_mapping(text, "names")
            cn_names_raw = self._parse_yaml_mapping(text, "cn_names")
            class_names = {int(key): value for key, value in names.items() if str(key).isdigit()}
            cn_names = {str(key): value for key, value in cn_names_raw.items()}
            return class_names or dict(DEFAULT_CLASS_NAMES), cn_names or dict(DEFAULT_CN_NAMES)

    @staticmethod
    def _parse_yaml_mapping(text: str, section: str) -> dict[str, str]:
        result: dict[str, str] = {}
        in_section = False
        for line in text.splitlines():
            raw = line.rstrip()
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if re.match(rf"^{re.escape(section)}\s*:\s*$", raw):
                in_section = True
                continue
            if in_section and re.match(r"^\S", raw):
                break
            if in_section:
                match = re.match(r"^\s*([^:]+)\s*:\s*[\"']?(.+?)[\"']?\s*$", raw)
                if match:
                    result[match.group(1).strip().strip("\"'")] = match.group(2).strip().strip("\"'")
        return result

    @staticmethod
    def _letterbox(image: Any, size: int, cv2: Any, np: Any) -> tuple[Any, float, int, int]:
        height, width = image.shape[:2]
        scale = min(float(size) / width, float(size) / height)
        new_width, new_height = int(round(width * scale)), int(round(height * scale))
        left = int(round((size - new_width) / 2 - 0.1))
        top = int(round((size - new_height) / 2 - 0.1))
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        canvas[top : top + new_height, left : left + new_width] = resized
        return canvas, scale, left, top

    @staticmethod
    def _postprocess(
        output: Any,
        image_shape: tuple[int, int],
        scale: float,
        pad_x: int,
        pad_y: int,
        conf_thres: float,
        iou_thres: float,
        cv2: Any,
        np: Any,
    ) -> list[dict[str, Any]]:
        pred = np.squeeze(output)
        if pred.ndim == 1:
            pred = pred.reshape(1, -1)
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T

        boxes_xywh = pred[:, :4]
        scores = pred[:, 4:]
        if scores.size == 0:
            return []
        class_ids = np.argmax(scores, axis=1)
        confidences = scores[np.arange(scores.shape[0]), class_ids]
        keep = confidences >= conf_thres
        if not np.any(keep):
            return []

        boxes_xywh = boxes_xywh[keep]
        confidences = confidences[keep]
        class_ids = class_ids[keep]

        nms_boxes = []
        height, width = image_shape
        for cx, cy, box_width, box_height in boxes_xywh:
            x1 = (cx - box_width / 2 - pad_x) / scale
            y1 = (cy - box_height / 2 - pad_y) / scale
            x2 = (cx + box_width / 2 - pad_x) / scale
            y2 = (cy + box_height / 2 - pad_y) / scale
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(width - 1, int(x2)), min(height - 1, int(y2))
            nms_boxes.append([x1, y1, max(0, x2 - x1), max(0, y2 - y1)])

        indices = cv2.dnn.NMSBoxes(nms_boxes, confidences.tolist(), conf_thres, iou_thres)
        detections: list[dict[str, Any]] = []
        for index in np.array(indices).reshape(-1):
            x, y, box_width, box_height = nms_boxes[int(index)]
            detections.append(
                {
                    "class_id": int(class_ids[int(index)]),
                    "bbox": [x, y, x + box_width, y + box_height],
                    "confidence": float(confidences[int(index)]),
                }
            )
        return detections

    @staticmethod
    def _draw_detection(image: Any, bbox: list[int], label: str, confidence: float, cv2: Any) -> None:
        x1, y1, x2, y2 = [int(value) for value in bbox]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 180, 0), 2)
        cv2.putText(
            image,
            f"{label} {confidence:.2f}",
            (x1, max(y1 - 8, 16)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 180, 0),
            2,
        )


local_dnn_detector = LocalDnnDetector()
