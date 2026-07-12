import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from recommendation_pool import filter_recommendations_from_df, star_label, star_text


class RecommendationPoolTests(unittest.TestCase):
    def test_score_maps_to_half_star_steps(self):
        self.assertEqual(star_label(0), "0.5星")
        self.assertEqual(star_text(0), "½☆☆☆☆")
        self.assertEqual(star_label(68.7), "3.5星")
        self.assertEqual(star_label(100), "5.0星")

    def test_highlights_keep_all_stable_and_profitable_spikes_only(self):
        df = pd.DataFrame([
            {
                "market_hash_name": "stable-a",
                "name_cn": "稳定A",
                "candidate_category": "STABLE_ARBITRAGE",
                "candidate_subcategory": "",
                "recommendation_score": 30,
                "recommendation_grade": "C",
                "stress_roi": 0.01,
                "flat_roi": 0.04,
            },
            {
                "market_hash_name": "spike-good",
                "name_cn": "上涨高利润",
                "candidate_category": "HIGH_VOLATILITY",
                "candidate_subcategory": "SPIKE_RISK",
                "recommendation_score": 65,
                "recommendation_grade": "B",
                "stress_roi": -0.01,
                "flat_roi": 0.11,
            },
            {
                "market_hash_name": "spike-low",
                "name_cn": "上涨低利润",
                "candidate_category": "HIGH_VOLATILITY",
                "candidate_subcategory": "SPIKE_RISK",
                "recommendation_score": 70,
                "recommendation_grade": "B",
                "stress_roi": 0.00,
                "flat_roi": 0.08,
            },
            {
                "market_hash_name": "stable-a",
                "name_cn": "稳定A重复上涨",
                "candidate_category": "HIGH_VOLATILITY",
                "candidate_subcategory": "SPIKE_RISK",
                "recommendation_score": 90,
                "recommendation_grade": "A",
                "stress_roi": 0.02,
                "flat_roi": 0.20,
            },
        ])

        result = filter_recommendations_from_df(df, "HIGHLIGHTS", "ALL", "")
        keys = [row["market_hash_name"] for row in result]

        self.assertEqual(keys, ["stable-a", "spike-good"])
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(result[0]["display_category_cn"], "稳定搬砖")
        self.assertEqual(result[1]["display_category_cn"], "异常上涨")


if __name__ == "__main__":
    unittest.main()
