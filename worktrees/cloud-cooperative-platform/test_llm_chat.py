import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import llm_chat
import main


class LlmChatTests(unittest.TestCase):
    class _FakeStreamResponse:
        def __init__(self, lines):
            self.lines = lines

        def __enter__(self):
            return iter(self.lines)

        def __exit__(self, exc_type, exc, traceback):
            return False

    def test_scan_and_phone_sessions_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(llm_chat, "CHAT_OUTPUT_DIR", root):
                scan_session = llm_chat.ensure_session(
                    {"mode": "real", "scan_id": "scan_20260723_001"}
                )
                phone_session = llm_chat.ensure_session(
                    {"mode": "phone", "upload_id": "upload_20260723_001"},
                    validate_source=False,
                )
                llm_chat.append_message(scan_session, {"role": "user", "content": "scan"})
                llm_chat.append_message(phone_session, {"role": "user", "content": "phone"})

                self.assertNotEqual(scan_session["session_id"], phone_session["session_id"])
                self.assertEqual(llm_chat.load_session(scan_session["session_id"])["messages"][0]["content"], "scan")
                self.assertEqual(llm_chat.load_session(phone_session["session_id"])["messages"][0]["content"], "phone")

    def test_phone_context_rejects_more_than_nine_images(self) -> None:
        payload = {
            "mode": "phone",
            "selected_upload_ids": [f"upload_{index:08d}" for index in range(10)],
        }
        with self.assertRaisesRegex(llm_chat.LlmChatError, "9"):
            llm_chat.resolve_chat_context(payload)

    def test_recent_history_keeps_twelve_rounds(self) -> None:
        messages = []
        for index in range(15):
            messages.append({"role": "user", "content": f"question-{index}"})
            messages.append({"role": "assistant", "content": f"answer-{index}"})
        recent = llm_chat.recent_messages(messages, rounds=12)
        self.assertEqual(len(recent), 24)
        self.assertEqual(recent[0]["content"], "question-3")
        self.assertEqual(recent[-1]["content"], "answer-14")

    def test_openai_sse_parser_separates_reasoning_content_and_usage(self) -> None:
        lines = [
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            b'data: {"choices":[],"usage":{"total_tokens":12}}\n',
            b'data: [DONE]\n',
        ]
        events = list(llm_chat.parse_openai_sse(lines))
        self.assertEqual(events[0], {"type": "reasoning", "content": "thinking"})
        self.assertEqual(events[1], {"type": "content", "content": "hello"})
        self.assertEqual(events[2], {"type": "usage", "usage": {"total_tokens": 12}})
        self.assertEqual(events[3], {"type": "done"})

    def test_report_is_appended_without_replacing_existing_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(llm_chat, "CHAT_OUTPUT_DIR", root):
                session = llm_chat.ensure_session(
                    {"mode": "real", "scan_id": "scan_20260723_002"}
                )
                llm_chat.append_message(session, {"role": "user", "content": "existing question"})
                llm_chat.append_report(
                    {"target_type": "mosaic", "scan_id": "scan_20260723_002"},
                    {"report_markdown": "### 新版报告", "model": "qwen3.7-plus"},
                )
                saved = llm_chat.load_session(session["session_id"])

        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["messages"][1]["kind"], "report")
        self.assertEqual(saved["messages"][1]["content"], "### 新版报告")

    def test_markdown_export_contains_context_and_messages(self) -> None:
        session = {
            "session_id": "chat_real_scan_1",
            "scope": {"mode": "real", "scan_id": "scan_1"},
            "messages": [
                {"role": "user", "content": "比较风险", "context_summary": "已选择 2 个缺陷"},
                {"role": "assistant", "content": "先复检高风险项"},
            ],
        }
        markdown = llm_chat.session_markdown(session)
        self.assertIn("已选择 2 个缺陷", markdown)
        self.assertIn("比较风险", markdown)
        self.assertIn("先复检高风险项", markdown)

    def test_chat_stream_endpoint_returns_event_stream(self) -> None:
        class FakeRequest:
            async def json(self):
                return {"mode": "real", "scan_id": "scan_123456", "message": "分析"}

        with patch.object(llm_chat, "stream_chat", return_value=iter(["event: done\ndata: {}\n\n"])):
            response = asyncio.run(main.api_llm_chat_stream(FakeRequest()))

        self.assertTrue(response.media_type.startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_report_endpoint_appends_report_to_chat_session(self) -> None:
        class FakeRequest:
            async def json(self):
                return {"target_type": "mosaic", "scan_id": "scan_123456", "mode": "real"}

        report = {"report_markdown": "### report", "model": "qwen3.7-plus"}
        with patch.object(llm_reporter := main.llm_reporter, "generate_report", return_value=report):
            with patch.object(llm_chat, "append_report") as append:
                result = asyncio.run(main.api_llm_report(FakeRequest()))

        self.assertEqual(result, report)
        append.assert_called_once_with(
            {"target_type": "mosaic", "scan_id": "scan_123456", "mode": "real"},
            report,
        )

    def test_completed_stream_persists_answer_but_not_reasoning_content(self) -> None:
        response = self._FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"reasoning_content":"private reasoning"}}]}\n',
                b'data: {"choices":[{"delta":{"content":"final answer"}}]}\n',
                b'data: {"choices":[],"usage":{"total_tokens":21}}\n',
                b'data: [DONE]\n',
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(llm_chat, "CHAT_OUTPUT_DIR", Path(tmp)):
                with patch.object(llm_chat.llm_reporter, "status", return_value={"configured": True}):
                    with patch.object(
                        llm_chat,
                        "resolve_chat_context",
                        return_value={
                            "summary": "validated context",
                            "images": [Path("image.jpg")],
                            "defects": [],
                            "snapshot": {"scan_id": "scan_20260723_003"},
                        },
                    ):
                        with patch.object(llm_chat, "_image_data_url", return_value="data:image/jpeg;base64,AA=="):
                            with patch.object(
                                llm_chat.llm_reporter,
                                "resolve_model_choice",
                                return_value=("qwen3.7-plus", "qwen3.7-plus"),
                            ):
                                with patch.object(llm_chat, "_chat_request", return_value=response):
                                    events = list(
                                        llm_chat.stream_chat(
                                            {
                                                "mode": "real",
                                                "scan_id": "scan_20260723_003",
                                                "message": "analyze",
                                            }
                                        )
                                    )
                session = llm_chat.ensure_session(
                    {"mode": "real", "scan_id": "scan_20260723_003"}
                )
                saved = llm_chat.load_session(session["session_id"])

        rendered = "".join(events)
        self.assertIn("event: phase", rendered)
        self.assertIn("event: delta", rendered)
        self.assertIn("event: done", rendered)
        self.assertNotIn("private reasoning", rendered)
        self.assertEqual([item["role"] for item in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["messages"][1]["content"], "final answer")
        self.assertEqual(saved["messages"][1]["usage"]["total_tokens"], 21)

    def test_interrupted_stream_does_not_persist_incomplete_assistant_answer(self) -> None:
        response = self._FakeStreamResponse(
            [
                b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
                b'data: {"choices":[{"delta":{"content":" answer"}}]}\n',
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(llm_chat, "CHAT_OUTPUT_DIR", Path(tmp)):
                with patch.object(llm_chat.llm_reporter, "status", return_value={"configured": True}):
                    with patch.object(
                        llm_chat,
                        "resolve_chat_context",
                        return_value={"summary": "context", "images": [], "defects": [], "snapshot": {}},
                    ):
                        with patch.object(
                            llm_chat.llm_reporter,
                            "resolve_model_choice",
                            return_value=("qwen3.7-plus", "qwen3.7-plus"),
                        ):
                            with patch.object(llm_chat, "_chat_request", return_value=response):
                                stream = llm_chat.stream_chat(
                                    {
                                        "mode": "real",
                                        "scan_id": "scan_20260723_004",
                                        "message": "analyze",
                                    }
                                )
                                next(stream)
                                next(stream)
                                next(stream)
                                stream.close()
                session = llm_chat.ensure_session(
                    {"mode": "real", "scan_id": "scan_20260723_004"}
                )
                saved = llm_chat.load_session(session["session_id"])

        self.assertEqual([item["role"] for item in saved["messages"]], ["user"])


if __name__ == "__main__":
    unittest.main()
