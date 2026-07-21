from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import CloudSettings


class DetectorError(RuntimeError):
    pass


def now_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 2)


def load_label_maps(config_path: Path) -> tuple[dict[int, str], dict[str, str]]:
    default_names = {
        0: "dry_solder_joint",
        1: "improper_installation",
        2: "pcb_damage",
        3: "short_circuit",
        4: "insufficient_solder",
        5: "reverse_component",
        6: "excess_solder",
    }
    if not config_path.exists():
        return default_names, {}

    section: str | None = None
    names: dict[int, str] = {}
    cn_names: dict[str, str] = {}
    for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")) and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        if section == "names":
            match = re.match(r"\s*(\d+)\s*:\s*(.+?)\s*$", line)
            if match:
                names[int(match.group(1))] = match.group(2).strip().strip("'\"")
        elif section == "cn_names":
            match = re.match(r"\s*([0-9A-Za-z_]+)\s*:\s*(.*?)\s*$", line)
            if match:
                cn_names[match.group(1)] = match.group(2).strip().strip("'\"")
    return names or default_names, cn_names


def draw_detection(image: Any, bbox: list[int], label: str, confidence: float, color: tuple[int, int, int] = (0, 180, 0)) -> None:
    import cv2

    x1, y1, x2, y2 = [int(value) for value in bbox]
    text = f"{label} {confidence:.2f}"
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.putText(image, text, (x1, max(y1 - 8, 16)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def tile_starts(length: int, tile: int, stride: int) -> list[int]:
    if length <= tile:
        return [0]
    starts = list(range(0, max(1, length - tile + 1), stride))
    last = max(0, length - tile)
    if starts[-1] != last:
        starts.append(last)
    return starts


def bbox_iou(a: list[int], b: list[int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
        return 0.0
    inter = float((inter_x2 - inter_x1) * (inter_y2 - inter_y1))
    area_a = float(max(1, ax2 - ax1) * max(1, ay2 - ay1))
    area_b = float(max(1, bx2 - bx1) * max(1, by2 - by1))
    return inter / max(area_a + area_b - inter, 1.0)


def nms_detections(detections: list[dict[str, Any]], iou_threshold: float = 0.45) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = {}
    for detection in detections:
        by_type.setdefault(str(detection.get("type", "")), []).append(detection)
    for items in by_type.values():
        items = sorted(items, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
        while items:
            best = items.pop(0)
            kept.append(best)
            items = [item for item in items if bbox_iou(best["bbox"], item["bbox"]) < iou_threshold]
    return sorted(kept, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)


def profile_config(profile: str) -> dict[str, Any]:
    if profile == "precision":
        return {"profile": "precision", "label": "高精模式", "tile_sizes": [512, 640, 800, 960], "imgszs": [640, 960, 1280], "overlap": 0.5}
    return {"profile": "fast", "label": "快速模式", "tile_sizes": [640], "imgszs": [640], "overlap": 0.5}


class BaseDetector:
    def detect(self, raw_path: Path, annotated_path: Path, result_json_path: Path, profile: str) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        raise NotImplementedError


class FakeDetector(BaseDetector):
    def __init__(self, settings: CloudSettings) -> None:
        self.settings = settings

    def detect(self, raw_path: Path, annotated_path: Path, result_json_path: Path, profile: str) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        start = time.perf_counter()
        import cv2

        image = cv2.imread(str(raw_path))
        if image is None:
            raise DetectorError(f"Could not read image: {raw_path}")
        height, width = image.shape[:2]
        bbox = [max(0, width // 4), max(0, height // 4), min(width, width * 3 // 4), min(height, height * 3 // 4)]
        defects = [{"type": "mock_defect", "label": "mock_defect", "bbox": bbox, "confidence": 0.99}]
        annotated = image.copy()
        draw_detection(annotated, bbox, "mock_defect", 0.99)
        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        result_json_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(annotated_path), annotated)
        result_json_path.write_text(json.dumps(defects, ensure_ascii=False, indent=2), encoding="utf-8")
        return defects, now_ms(start), {"strategy": "fake", "profile": profile}

    def status(self) -> dict[str, Any]:
        return {"backend": "fake", "model_exists": self.settings.model_path.exists(), "config_exists": self.settings.config_path.exists()}


class OnnxTiledDetector(BaseDetector):
    def __init__(self, settings: CloudSettings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._net: Any = None
        self._names: dict[int, str] | None = None
        self._cn_names: dict[str, str] | None = None
        self._last_error: str | None = None
        self._resolved_input_imgsz: int | None = None

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._net is not None:
                return
            if not self.settings.model_path.exists():
                raise DetectorError(f"ONNX model does not exist: {self.settings.model_path}")
            try:
                import cv2

                self._names, self._cn_names = load_label_maps(self.settings.config_path)
                self._resolve_input_imgsz()
                self._net = cv2.dnn.readNetFromONNX(str(self.settings.model_path))
                try:
                    self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                    self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                except Exception:
                    pass
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                raise

    def _resolve_input_imgsz(self) -> int:
        if self._resolved_input_imgsz:
            return self._resolved_input_imgsz

        if self.settings.onnx_input_imgsz and int(self.settings.onnx_input_imgsz) > 0:
            self._resolved_input_imgsz = int(self.settings.onnx_input_imgsz)
            return self._resolved_input_imgsz

        try:
            import onnx  # type: ignore[import-not-found]

            model = onnx.load(str(self.settings.model_path))
            if model.graph.input:
                dims = model.graph.input[0].type.tensor_type.shape.dim
                if len(dims) >= 4:
                    height = int(dims[2].dim_value or 0)
                    width = int(dims[3].dim_value or 0)
                    if height > 0 and width > 0 and height == width:
                        self._resolved_input_imgsz = height
                        return self._resolved_input_imgsz
        except Exception:
            pass

        probed = self._probe_input_imgsz()
        if probed:
            self._resolved_input_imgsz = probed
            return self._resolved_input_imgsz

        self._resolved_input_imgsz = int(self.settings.onnx_input_imgsz or self.settings.yolo_imgsz or 640)
        return self._resolved_input_imgsz

    def _probe_input_imgsz(self) -> int | None:
        try:
            import cv2
            import numpy as np
        except Exception:
            return None

        candidates: list[int] = []
        for value in (self.settings.onnx_input_imgsz, self.settings.yolo_imgsz, 640, 960, 1024, 1280):
            if value and int(value) > 0 and int(value) not in candidates:
                candidates.append(int(value))

        for size in candidates:
            try:
                net = cv2.dnn.readNetFromONNX(str(self.settings.model_path))
                blob = np.zeros((1, 3, size, size), dtype=np.float32)
                net.setInput(blob)
                net.forward()
                return size
            except Exception:
                continue
        return None

    def letterbox(self, image: Any, imgsz: int) -> tuple[Any, float, float, float]:
        import cv2

        height, width = image.shape[:2]
        scale = min(imgsz / max(width, 1), imgsz / max(height, 1))
        new_width = max(1, int(round(width * scale)))
        new_height = max(1, int(round(height * scale)))
        resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)
        pad_x = (imgsz - new_width) / 2.0
        pad_y = (imgsz - new_height) / 2.0
        left = int(round(pad_x - 0.1))
        right = int(round(pad_x + 0.1))
        top = int(round(pad_y - 0.1))
        bottom = int(round(pad_y + 0.1))
        padded = cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
        return padded, scale, float(left), float(top)

    def predict_frame(self, image: Any, imgsz: int) -> list[dict[str, Any]]:
        import cv2
        import numpy as np

        self.ensure_loaded()
        assert self._net is not None
        assert self._names is not None
        assert self._cn_names is not None
        fixed_imgsz = self._resolve_input_imgsz()
        if fixed_imgsz and imgsz != fixed_imgsz:
            raise DetectorError(f"ONNX fixed input is {fixed_imgsz}; requested {imgsz}.")
        original_height, original_width = image.shape[:2]
        input_image, scale, pad_x, pad_y = self.letterbox(image, imgsz)
        blob = cv2.dnn.blobFromImage(input_image, scalefactor=1 / 255.0, size=(imgsz, imgsz), swapRB=True, crop=False)
        self._net.setInput(blob)
        try:
            output = self._net.forward()
        except cv2.error as exc:
            raise DetectorError(
                f"OpenCV DNN forward failed at imgsz={imgsz}. "
                f"Configured AOI_ONNX_INPUT_IMGSZ={self.settings.onnx_input_imgsz}. "
                "Set AOI_ONNX_INPUT_IMGSZ and AOI_YOLO_IMGSZ to the ONNX export size, e.g. 1024."
            ) from exc
        if isinstance(output, (list, tuple)):
            output = output[0]
        predictions = np.asarray(output)
        if predictions.ndim == 3:
            predictions = predictions[0]
        if predictions.ndim != 2:
            raise DetectorError(f"Unsupported ONNX output shape: {tuple(np.asarray(output).shape)}")
        class_count = len(self._names)
        if predictions.shape[0] == class_count + 4:
            predictions = predictions.T
        elif predictions.shape[1] != class_count + 4:
            raise DetectorError(f"Unsupported ONNX output shape: {tuple(np.asarray(output).shape)}")

        raw: list[dict[str, Any]] = []
        for prediction in predictions:
            scores = prediction[4 : 4 + class_count]
            class_id = int(np.argmax(scores))
            confidence = float(scores[class_id])
            if confidence < self.settings.yolo_conf:
                continue
            cx, cy, width, height = [float(value) for value in prediction[:4]]
            x1 = int(round((cx - width / 2.0 - pad_x) / scale))
            y1 = int(round((cy - height / 2.0 - pad_y) / scale))
            x2 = int(round((cx + width / 2.0 - pad_x) / scale))
            y2 = int(round((cy + height / 2.0 - pad_y) / scale))
            x1 = max(0, min(original_width - 1, x1))
            y1 = max(0, min(original_height - 1, y1))
            x2 = max(x1 + 1, min(original_width, x2))
            y2 = max(y1 + 1, min(original_height, y2))
            defect_type = self._names.get(class_id, str(class_id))
            raw.append({"type": defect_type, "label": self._cn_names.get(defect_type, defect_type), "bbox": [x1, y1, x2, y2], "confidence": round(confidence, 4)})
        return nms_detections(raw)

    def compatible_imgszs(self, imgszs: list[int]) -> tuple[list[int], list[int]]:
        fixed = int(self._resolve_input_imgsz())
        compatible = [value for value in imgszs if int(value) == fixed]
        skipped = [value for value in imgszs if int(value) != fixed]
        if not compatible:
            compatible = [fixed]
        return compatible, skipped

    def detect(self, raw_path: Path, annotated_path: Path, result_json_path: Path, profile: str) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        start = time.perf_counter()
        import cv2

        image = cv2.imread(str(raw_path))
        if image is None:
            raise DetectorError(f"Could not read image: {raw_path}")
        height, width = image.shape[:2]
        config = profile_config(profile)
        imgszs, skipped_imgszs = self.compatible_imgszs([int(value) for value in config["imgszs"]])
        profile_results: list[dict[str, Any]] = []
        all_detections: list[dict[str, Any]] = []
        for tile_size in [int(value) for value in config["tile_sizes"]]:
            stride = max(1, int(round(tile_size * (1.0 - float(config["overlap"])))))
            xs = tile_starts(width, tile_size, stride)
            ys = tile_starts(height, tile_size, stride)
            for imgsz in imgszs:
                raw_detections: list[dict[str, Any]] = []
                tile_count = 0
                for y in ys:
                    for x in xs:
                        crop = image[y : min(height, y + tile_size), x : min(width, x + tile_size)]
                        if crop.size == 0:
                            continue
                        tile_count += 1
                        for detection in self.predict_frame(crop, imgsz):
                            x1, y1, x2, y2 = [int(value) for value in detection["bbox"]]
                            mapped = dict(detection)
                            mapped["bbox"] = [
                                max(0, min(width, x + x1)),
                                max(0, min(height, y + y1)),
                                max(0, min(width, x + x2)),
                                max(0, min(height, y + y2)),
                            ]
                            mapped["source_tile"] = {"x": x, "y": y, "width": int(crop.shape[1]), "height": int(crop.shape[0]), "tile_size": tile_size, "imgsz": imgsz}
                            raw_detections.append(mapped)
                detections = nms_detections(raw_detections)
                all_detections.extend(detections)
                profile_results.append(
                    {
                        "tile_size": tile_size,
                        "imgsz": imgsz,
                        "tile_stride": stride,
                        "tile_count": tile_count,
                        "raw_detection_count": len(raw_detections),
                        "nms_detection_count": len(detections),
                        "best_confidence": max((float(item.get("confidence", 0.0)) for item in detections), default=0.0),
                    }
                )
        final_detections = nms_detections(all_detections)
        annotated = image.copy()
        for detection in final_detections:
            draw_detection(annotated, detection["bbox"], str(detection.get("label") or detection.get("type")), float(detection.get("confidence", 0.0)))
        annotated_path.parent.mkdir(parents=True, exist_ok=True)
        result_json_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(annotated_path), annotated)
        result_json_path.write_text(json.dumps(final_detections, ensure_ascii=False, indent=2), encoding="utf-8")
        details = {
            "strategy": "profile_ensemble" if len(profile_results) > 1 else "tiled",
            "profile": config["profile"],
            "profile_label": config["label"],
            "tile_sizes": config["tile_sizes"],
            "imgszs": imgszs,
            "requested_imgszs": config["imgszs"],
            "skipped_imgszs": skipped_imgszs,
            "tile_overlap": config["overlap"],
            "profile_summaries": profile_results,
        }
        return final_detections, now_ms(start), details

    def status(self) -> dict[str, Any]:
        return {
            "backend": "onnx_opencv",
            "model": str(self.settings.model_path),
            "model_exists": self.settings.model_path.exists(),
            "config": str(self.settings.config_path),
            "config_exists": self.settings.config_path.exists(),
            "imgsz": self.settings.yolo_imgsz,
            "onnx_input_imgsz": self._resolved_input_imgsz or self._resolve_input_imgsz(),
            "configured_onnx_input_imgsz": self.settings.onnx_input_imgsz,
            "conf": self.settings.yolo_conf,
            "last_error": self._last_error,
        }


def create_detector(settings: CloudSettings) -> BaseDetector:
    if settings.detector_backend == "fake":
        return FakeDetector(settings)
    return OnnxTiledDetector(settings)
