import os
import sqlite3
import sys
import tempfile
from contextlib import closing
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from history_store import BACKTEST_TABLE, save_recommendation_backtest_snapshots


def sample_row(name: str, economic_gate_pass: bool) -> dict:
    return {
        "market_hash_name": name,
        "name_cn": f"{name} 中文名",
        "min_price": 100.0,
        "skinport_cost_cny": 86.61,
        "yyyp_buy_price": 100.0,
        "yyyp_sell_price": 105.0,
        "buff_buy_price": 99.0,
        "buff_sell_price": 104.0,
        "buff_price_chg": 0.01,
        "recommendation_score": 55.0,
        "recommendation_grade": "C",
        "candidate_category": "STABLE_ARBITRAGE",
        "candidate_subcategory": "",
        "economic_gate_pass": economic_gate_pass,
    }


class HistoryStoreTests(unittest.TestCase):
    def test_backtest_db_only_keeps_economic_gate_passed_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "recommendation_backtest.sqlite3")
            df = pd.DataFrame([
                sample_row("A", True),
                sample_row("B", False),
            ])

            stats = save_recommendation_backtest_snapshots(df, db_path=db_path)

            self.assertEqual(stats["inserted"], 1)
            with closing(sqlite3.connect(db_path)) as conn:
                rows = conn.execute(
                    f"SELECT market_hash_name, recommendation_grade, backtest_status FROM {BACKTEST_TABLE}"
                ).fetchall()

            self.assertEqual(rows, [("A", "C", "PENDING")])

    def test_matured_backtest_updates_7d_buy_price_and_realized_roi(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "recommendation_backtest.sqlite3")
            save_recommendation_backtest_snapshots(pd.DataFrame(), db_path=db_path)

            old_collected = datetime.now(timezone.utc) - timedelta(days=8)
            old_target = datetime.now(timezone.utc) - timedelta(days=1)
            with closing(sqlite3.connect(db_path)) as conn:
                conn.execute(
                    f"""
                    INSERT INTO {BACKTEST_TABLE} (
                        collected_at, target_check_at, market_hash_name, skinport_cost_cny,
                        yyyp_buy_price, yyyp_sell_price, recommendation_score,
                        recommendation_grade, backtest_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
                    """,
                    (
                        old_collected.isoformat(timespec="seconds"),
                        old_target.isoformat(timespec="seconds"),
                        "A",
                        100.0,
                        110.0,
                        115.0,
                        50.0,
                        "C",
                    ),
                )
                conn.commit()

            current = pd.DataFrame([{
                "market_hash_name": "A",
                "yyyp_buy_price": 120.0,
                "economic_gate_pass": False,
            }])
            stats = save_recommendation_backtest_snapshots(current, db_path=db_path)

            self.assertEqual(stats["updated_7d"], 1)
            with closing(sqlite3.connect(db_path)) as conn:
                row = conn.execute(
                    f"SELECT yyyp_buy_price_7d, actual_7d_roi, backtest_status FROM {BACKTEST_TABLE}"
                ).fetchone()

            self.assertAlmostEqual(row[0], 120.0)
            self.assertAlmostEqual(row[1], 0.20)
            self.assertEqual(row[2], "RESOLVED")


if __name__ == "__main__":
    unittest.main()
