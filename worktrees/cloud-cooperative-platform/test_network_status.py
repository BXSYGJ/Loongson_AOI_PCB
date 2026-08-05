import unittest
from unittest.mock import patch

from starlette.requests import Request

import main
import llm_reporter


class NetworkStatusTests(unittest.TestCase):
    def test_llm_report_default_timeout_is_120_seconds(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            with patch.object(llm_reporter, "_local_config_cache", {}):
                self.assertEqual(llm_reporter._timeout_seconds(), 120.0)

    def test_request_origin_is_the_primary_phone_url(self) -> None:
        request = Request(
            {
                "type": "http",
                "scheme": "http",
                "server": ("116.205.117.33", 8020),
                "path": "/api/system/status",
                "query_string": b"",
                "headers": [(b"host", b"116.205.117.33:8020")],
            }
        )

        with patch.object(main, "local_ipv4_addresses", return_value=["192.168.0.124"]):
            status = main.network_status(request)

        self.assertEqual(status["phone_urls"][0], "http://116.205.117.33:8020/phone")
        self.assertIn("http://192.168.0.124:8020/phone", status["phone_urls"])

    def test_real_mode_always_keeps_traceable_cell_fusion_results(self) -> None:
        self.assertTrue(main.should_skip_mosaic_detection("real", "local_dnn"))
        self.assertTrue(main.should_skip_mosaic_detection("real", "huawei_cloud"))
        self.assertTrue(main.should_skip_mosaic_detection("real", "phone_browser"))
        self.assertFalse(main.should_skip_mosaic_detection("simulation", "local_dnn"))


if __name__ == "__main__":
    unittest.main()
