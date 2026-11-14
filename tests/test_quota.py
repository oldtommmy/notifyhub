import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from notifyhub import quota


def sample_quota():
    return {
        "plan_type": "pro",
        "primary_used_percent": 79,
        "primary_remaining_percent": 21,
        "primary_window_minutes": 10080,
        "primary_resets_at": int(time.time()) + 3 * 86400,
        "credits_has": True,
        "credits_unlimited": False,
        "credits_balance": 64629.16,
        "reset_credits": 3,
        "spend_control_reached": False,
        "rate_limit_reached_type": None,
        "fetched_at": int(time.time()),
        "stale": False,
    }


class QuotaTests(unittest.TestCase):
    def test_parse_result(self):
        parsed = quota.parse_result(
            {
                "rateLimits": {
                    "planType": "pro",
                    "primary": {"usedPercent": 79, "windowDurationMins": 10080, "resetsAt": 1794631764},
                    "secondary": None,
                    "credits": {"hasCredits": True, "unlimited": False, "balance": "64629.1662145000"},
                    "spendControlReached": False,
                    "rateLimitReachedType": None,
                },
                "rateLimitResetCredits": {"availableCount": 3},
            }
        )
        self.assertEqual(parsed["primary_remaining_percent"], 21)
        self.assertEqual(parsed["reset_credits"], 3)
        self.assertAlmostEqual(parsed["credits_balance"], 64629.1662145)

    def test_format_line(self):
        line = quota.format_line(sample_quota())
        self.assertIn("剩余额度: 21%", line)
        self.assertIn("3 天后重置", line)
        self.assertIn("重置券 3", line)

    def test_format_line_limited(self):
        data = sample_quota()
        data["spend_control_reached"] = True
        self.assertEqual(quota.format_line(data), "剩余额度: 已受限")

    def test_format_line_credits_only(self):
        data = sample_quota()
        data["primary_remaining_percent"] = None
        data["primary_resets_at"] = None
        data["reset_credits"] = 0
        self.assertEqual(quota.format_line(data), "剩余额度: Credits 64,629")

    def test_get_uses_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            with patch("notifyhub.quota.fetch", return_value=sample_quota()) as fetch:
                first = quota.get(path=path, max_age=600)
                second = quota.get(path=path, max_age=600)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first["primary_remaining_percent"], 21)
            self.assertEqual(second["primary_remaining_percent"], 21)

    def test_get_returns_stale_cache_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            data = sample_quota()
            data["fetched_at"] = int(time.time()) - 1000
            path.write_text(json.dumps(data), encoding="utf-8")
            with patch("notifyhub.quota.fetch", side_effect=quota.QuotaError("offline")):
                result = quota.get(path=path, max_age=10)
            self.assertTrue(result["stale"])
            self.assertEqual(result["primary_remaining_percent"], 21)


if __name__ == "__main__":
    unittest.main()
