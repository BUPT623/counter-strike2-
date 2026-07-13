import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from large_deal_analyzer import add_large_score_columns, apply_large_filters


class LargeDealAnalyzerTests(unittest.TestCase):
    def test_large_filters_require_price_range_and_sell_count(self):
        cfg = {
            "filters": {
                "skinport_min_price_hkd": 2300,
                "skinport_max_price_hkd": 12000,
                "uu_min_sell_price_rmb": 2000,
                "uu_max_sell_price_rmb": 10000,
                "min_uu_sell_num": 50,
            }
        }
        df = pd.DataFrame([
            {"min_price": 3000, "yyyp_sell_price": 3500, "yyyp_buy_price": 3300, "yyyp_sell_num": 51},
            {"min_price": 2000, "yyyp_sell_price": 3500, "yyyp_buy_price": 3300, "yyyp_sell_num": 51},
            {"min_price": 3000, "yyyp_sell_price": 3500, "yyyp_buy_price": 3300, "yyyp_sell_num": 50},
        ])

        valid, rejected = apply_large_filters(df, cfg)

        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 2)

    def test_large_score_uses_profit_rate_and_amount(self):
        cfg = {
            "scoring": {
                "profit_rate_weight": 60,
                "profit_amount_weight": 40,
                "roi_cap": 0.25,
                "profit_cap_rmb": 1200,
            }
        }
        df = pd.DataFrame([
            {
                "cost_rmb": 3000,
                "yyyp_sell_price": 3600,
            },
            {
                "cost_rmb": 3000,
                "yyyp_sell_price": 3150,
            },
        ])

        scored = add_large_score_columns(df, cfg)

        self.assertGreater(scored.iloc[0]["recommendation_score"], scored.iloc[1]["recommendation_score"])
        self.assertEqual(scored.iloc[0]["recommended_exit_mode"], "捡漏模式")
        self.assertAlmostEqual(scored.iloc[0]["deal_profit_rate"], 0.20)


if __name__ == "__main__":
    unittest.main()
