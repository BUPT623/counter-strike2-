import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from automation_runner import is_in_active_window


class AutomationRunnerTests(unittest.TestCase):
    def test_active_window_includes_9_to_before_24(self):
        cfg = {
            "scheduler": {
                "active_hours": {"enabled": True, "start": "09:00", "end": "24:00"}
            }
        }
        tz = timezone(timedelta(hours=8))

        self.assertFalse(is_in_active_window(cfg, datetime(2026, 1, 1, 8, 59, tzinfo=tz)))
        self.assertTrue(is_in_active_window(cfg, datetime(2026, 1, 1, 9, 0, tzinfo=tz)))
        self.assertTrue(is_in_active_window(cfg, datetime(2026, 1, 1, 23, 59, tzinfo=tz)))
        self.assertFalse(is_in_active_window(cfg, datetime(2026, 1, 2, 0, 0, tzinfo=tz)))


if __name__ == "__main__":
    unittest.main()
