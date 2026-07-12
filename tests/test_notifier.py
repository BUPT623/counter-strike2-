import os
import sys
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from notifier import CHINA_TZ, DEFAULT_AUTOMATION_CONFIG, filter_sendable_items, mark_digest_sent


def sample_item(name: str, score: float = 50.0, grade: str = "C", profit: float = 0.06) -> dict:
    return {
        "market_hash_name": name,
        "name_cn": f"{name} 中文名",
        "display_category_cn": "稳定搬砖",
        "recommendation_grade": grade,
        "recommendation_score": score,
        "flat_roi": profit,
        "risk_level": "LOW",
    }


class NotifierTests(unittest.TestCase):
    def notification_config(self) -> dict:
        return deepcopy(DEFAULT_AUTOMATION_CONFIG["notifications"])

    def test_quiet_hours_suppress_normal_items_but_allow_grade_a(self):
        cfg = self.notification_config()
        now = datetime(2026, 1, 2, 0, 30, tzinfo=CHINA_TZ)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "notify.sqlite3")
            selected, stats = filter_sendable_items(
                [sample_item("normal"), sample_item("urgent", grade="A")],
                cfg,
                db_path=db_path,
                now=now,
            )

        self.assertEqual([item["market_hash_name"] for item in selected], ["urgent"])
        self.assertEqual(stats["quiet_suppressed"], 1)

    def test_same_item_cooldown_suppresses_until_material_change(self):
        cfg = self.notification_config()
        cfg["quiet_hours"]["enabled"] = False
        now = datetime(2026, 1, 1, 10, 0, tzinfo=CHINA_TZ)
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "notify.sqlite3")
            selected, _ = filter_sendable_items([sample_item("a", score=50)], cfg, db_path=db_path, now=now)
            self.assertEqual(len(selected), 1)
            mark_digest_sent(selected, db_path=db_path, now=now)

            selected_again, stats = filter_sendable_items(
                [sample_item("a", score=50)],
                cfg,
                db_path=db_path,
                now=now + timedelta(hours=1),
            )
            self.assertEqual(selected_again, [])
            self.assertEqual(stats["cooldown_suppressed"], 1)

            changed, _ = filter_sendable_items(
                [sample_item("a", score=56)],
                cfg,
                db_path=db_path,
                now=now + timedelta(hours=1),
            )
            self.assertEqual(len(changed), 1)


if __name__ == "__main__":
    unittest.main()
