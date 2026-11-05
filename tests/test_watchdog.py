"""watchdog 判斷邏輯測試（不連網）。執行：python -m unittest tests.test_watchdog"""
import unittest
from unittest import mock

import watchdog


class T(unittest.TestCase):
    def run_check(self, trading, markers):
        with mock.patch.object(watchdog, "is_trading_day", return_value=trading), \
             mock.patch.object(watchdog, "read_marker", side_effect=lambda p: markers[p]):
            return watchdog.check("2026-11-05")

    def test_ok(self):
        self.assertEqual(self.run_check(True, {"data/last_text_push.txt": "2026-11-05",
                                               "data/last_push.txt": "2026-11-05"}), [])

    def test_missing_both(self):
        p = self.run_check(True, {"data/last_text_push.txt": "2026-11-04",
                                  "data/last_push.txt": "2026-11-04"})
        self.assertEqual(len(p), 2)
        self.assertIn("2026-11-04", p[0])

    def test_missing_chart_only(self):
        p = self.run_check(True, {"data/last_text_push.txt": "2026-11-05",
                                  "data/last_push.txt": None})
        self.assertEqual(p, ["圖表：未推播（記號停在 無）"])

    def test_holiday_no_alert(self):
        self.assertEqual(self.run_check(False, {}), [])

    def test_calendar_error_alerts(self):
        with mock.patch.object(watchdog, "is_trading_day", side_effect=ValueError("bad json")):
            p = watchdog.check("2026-11-05")
        self.assertTrue(p and "無法確認" in p[0])

    def test_trading_day_parse(self):
        resp = mock.Mock()
        resp.json.return_value = {"stat": "OK", "data": [["115/11/04", "", "", "", "1"],
                                                         ["115/11/05", "", "", "", "1"]]}
        with mock.patch.object(watchdog, "get_retry", return_value=resp):
            self.assertTrue(watchdog.is_trading_day("2026-11-05"))
            self.assertFalse(watchdog.is_trading_day("2026-11-06"))


if __name__ == "__main__":
    unittest.main()
