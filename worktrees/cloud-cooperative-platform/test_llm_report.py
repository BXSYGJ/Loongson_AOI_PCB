import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_reporter
import main


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class LlmReportTests(unittest.TestCase):
    def test_chat_request_sends_thinking_flag_at_top_level(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured.update(json.loads(request.data.decode("utf-8")))
            return _FakeResponse(
                {
                    "choices": [
                        {"message": {"content": "report"}, "finish_reason": "stop"}
                    ],
                    "usage": {"total_tokens": 12},
                }
            )

        with patch.object(llm_reporter, "_image_data_url", return_value="data:image/jpeg;base64,AA=="):
            with patch.object(llm_reporter, "_api_key", return_value="test-key"):
                with patch.object(llm_reporter.urllib.request, "urlopen", side_effect=fake_urlopen):
                    llm_reporter._call_chat_completions(
                        "prompt",
                        Path("image.jpg"),
                        "qwen3.7-max-2026-06-08",
                        False,
                    )

        self.assertIs(captured["enable_thinking"], False)
        self.assertEqual(captured["model"], "qwen3.7-max-2026-06-08")

    def test_model_choices_are_allowlisted_and_max_uses_visual_snapshot(self) -> None:
        self.assertEqual(
            llm_reporter.resolve_model_choice("qwen3.7-max"),
            ("qwen3.7-max", "qwen3.7-max-2026-06-08"),
        )
        self.assertEqual(
            llm_reporter.resolve_model_choice("qwen3.6-flash"),
            ("qwen3.6-flash", "qwen3.6-flash"),
        )
        with self.assertRaises(llm_reporter.LlmReportError):
            llm_reporter.resolve_model_choice("arbitrary-model")

    def test_status_exposes_three_model_choices(self) -> None:
        with patch.object(llm_reporter, "_api_key", return_value="test-key"):
            with patch.object(llm_reporter, "_local_config_cache", {}):
                status = llm_reporter.status()
        self.assertEqual(status["model"], "qwen3.7-plus")
        self.assertEqual(
            [item["id"] for item in status["available_models"]],
            ["qwen3.7-plus", "qwen3.6-flash", "qwen3.7-max"],
        )
        max_item = status["available_models"][2]
        self.assertEqual(max_item["executed_model"], "qwen3.7-max-2026-06-08")

    def test_generate_report_defaults_to_non_thinking_and_returns_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / "annotated.jpg"
            result_path = root / "result.json"
            report_md = root / "diagnosis_report.md"
            report_json = root / "diagnosis_report.json"
            image_path.write_bytes(b"image")
            result_path.write_text(
                json.dumps({"defects": [{"label": "short_circuit", "confidence": 0.7, "bbox": [1, 2, 3, 4]}]}),
                encoding="utf-8",
            )
            target = llm_reporter.ReportTarget(
                target_type="phone",
                target={"upload_id": "upload_test"},
                image_path=image_path,
                result_json_path=result_path,
                report_md_path=report_md,
                report_json_path=report_json,
                title="phone test",
            )
            with patch.object(llm_reporter, "_api_key", return_value="test-key"):
                with patch.object(llm_reporter, "resolve_report_target", return_value=target):
                    with patch.object(
                        llm_reporter,
                        "_call_chat_completions",
                        return_value=("### report", {"usage": {"total_tokens": 9}, "finish_reason": "stop"}),
                    ) as call:
                        result = llm_reporter.generate_report(
                            {
                                "target_type": "phone",
                                "upload_id": "upload_test",
                                "model": "qwen3.7-max",
                            }
                        )

            self.assertIs(result["thinking_enabled"], False)
            self.assertGreaterEqual(result["elapsed_ms"], 0)
            self.assertEqual(result["model"], "qwen3.7-max")
            self.assertEqual(result["executed_model"], "qwen3.7-max-2026-06-08")
            self.assertEqual(call.call_args.args[2], "qwen3.7-max-2026-06-08")
            self.assertIs(call.call_args.args[3], False)
            saved = json.loads(report_json.read_text(encoding="utf-8"))
            self.assertIs(saved["thinking_enabled"], False)
            self.assertEqual(saved["model"], "qwen3.7-max")
            self.assertEqual(saved["executed_model"], "qwen3.7-max-2026-06-08")

    def test_resolve_report_download_requires_existing_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upload_dir = root / "upload_test"
            upload_dir.mkdir()
            report_path = upload_dir / "diagnosis_report.md"
            with patch.object(llm_reporter.camera_trigger, "PHONE_UPLOAD_DIR", root):
                with self.assertRaises(llm_reporter.LlmReportError):
                    llm_reporter.resolve_report_download({"target_type": "phone", "upload_id": "upload_test"})

            report_path.write_text("# report", encoding="utf-8")
            with patch.object(llm_reporter.camera_trigger, "PHONE_UPLOAD_DIR", root):
                report_path, filename = llm_reporter.resolve_report_download(
                    {"target_type": "phone", "upload_id": "upload_test"}
                )
            self.assertEqual(report_path, upload_dir / "diagnosis_report.md")
            self.assertEqual(filename, "phone_upload_test_diagnosis_report.md")

    def test_download_endpoint_returns_markdown_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "diagnosis_report.md"
            report_path.write_text("# report", encoding="utf-8")
            with patch.object(
                llm_reporter,
                "resolve_report_download",
                return_value=(report_path, "phone_upload_test_diagnosis_report.md"),
            ):
                response = asyncio.run(
                    main.api_llm_report_download(
                        target_type="phone",
                        scan_id=None,
                        row=None,
                        col=None,
                        upload_id="upload_test",
                    )
                )
        self.assertIn("phone_upload_test_diagnosis_report.md", response.headers["content-disposition"])
        self.assertTrue(response.media_type.startswith("text/markdown"))


if __name__ == "__main__":
    unittest.main()
