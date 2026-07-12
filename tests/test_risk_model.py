import os
import sys
import tempfile
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from config_loader import DEFAULT_CONFIG
from excel_report import export_profit_workbook
from normalization import normalize_rate, normalize_return_to_7d, parse_float
from risk_model import (
    add_risk_columns,
    analyze_row,
    buy_sell_num_ratio,
    order_imbalance,
    risk_level,
    stress_change,
    weighted_std,
    yyyp_spread_rate,
)
from risk_semantics import diagnose_rate_fields


def base_row(**overrides):
    row = {
        "market_hash_name": "AK-47 | Test (Field-Tested)",
        "name_cn": "测试饰品",
        "min_price": 100.0,
        "median_price": 110.0,
        "max_price": 120.0,
        "quantity": 10,
        "skinport_cost_cny": 100.0,
        "yyyp_buy_price": 112.0,
        "yyyp_sell_price": 116.0,
        "yyyp_buy_num": 40,
        "yyyp_sell_num": 60,
        "sell_price_rate_1": 0.0,
        "sell_price_rate_7": 0.01,
        "sell_price_rate_15": 0.01,
        "sell_price_rate_30": 0.01,
        "buff_buy_price": 111.0,
        "buff_sell_price": 115.0,
        "buff_price_chg": 0.01,
        "steam_buy_price": None,
        "steam_sell_price": None,
    }
    row.update(overrides)
    return row


class NormalizationTests(unittest.TestCase):
    def test_percent_rate_becomes_decimal(self):
        self.assertAlmostEqual(normalize_rate("5%"), 0.05)

    def test_decimal_rate_is_not_divided_again(self):
        self.assertAlmostEqual(normalize_rate(0.05), 0.05)

    def test_invalid_price_string_returns_none(self):
        self.assertIsNone(parse_float("--"))
        self.assertIsNone(parse_float("bad-price"))

    def test_return_to_7d(self):
        self.assertAlmostEqual(normalize_return_to_7d(0.01, 1), (1.01 ** 7) - 1)
        self.assertAlmostEqual(normalize_return_to_7d(0.07, 7), 0.07)

    def test_missing_period_degrades(self):
        result = analyze_row(base_row(sell_price_rate_1=None, sell_price_rate_15=None), DEFAULT_CONFIG, "HIGH")
        self.assertIn("7", result["trend_periods_used"])
        self.assertNotIn("15", result["trend_periods_used"])


class SemanticTests(unittest.TestCase):
    def test_sell_price_7_delta_semantic_detection(self):
        df = pd.DataFrame([
            {"yyyp_sell_price": 100.0, "sell_price_7": -5.0, "sell_price_rate_7": 100 / 105 - 1},
            {"yyyp_sell_price": 110.0, "sell_price_7": 10.0, "sell_price_rate_7": 110 / 100 - 1},
        ])
        diag = diagnose_rate_fields(df, DEFAULT_CONFIG)
        row = diag[diag["field"] == "sell_price_7"].iloc[0]
        self.assertEqual(row["inferred_meaning"], "absolute_price_change")
        self.assertEqual(row["confidence_level"], "HIGH")

    def test_missing_baseline_is_unknown(self):
        diag = diagnose_rate_fields(pd.DataFrame([{"yyyp_sell_price": 100.0}]), DEFAULT_CONFIG)
        row = diag[diag["field"] == "sell_price_7"].iloc[0]
        self.assertEqual(row["inferred_meaning"], "unknown")
        self.assertEqual(row["valid_sample_count"], 0)

    def test_mismatched_semantic_low_confidence(self):
        df = pd.DataFrame([
            {"yyyp_sell_price": 100.0, "sell_price_7": 90.0, "sell_price_rate_7": 0.50},
            {"yyyp_sell_price": 110.0, "sell_price_7": 100.0, "sell_price_rate_7": -0.40},
        ])
        diag = diagnose_rate_fields(df, DEFAULT_CONFIG)
        row = diag[diag["field"] == "sell_price_7"].iloc[0]
        self.assertEqual(row["confidence_level"], "LOW")


class RiskFormulaTests(unittest.TestCase):
    def test_weighted_std(self):
        self.assertAlmostEqual(weighted_std([0.0, 0.1], [1.0, 1.0]), 0.05)

    def test_yyyp_spread_rate(self):
        self.assertAlmostEqual(yyyp_spread_rate(90, 100), 0.10)
        self.assertIsNone(yyyp_spread_rate(90, 0))

    def test_order_imbalance_and_ratio(self):
        self.assertAlmostEqual(order_imbalance(30, 10), 20 / 41)
        self.assertAlmostEqual(buy_sell_num_ratio(30, 10), 3.0)

    def test_stress_change_is_capped(self):
        row = base_row(yyyp_buy_num=1, yyyp_sell_num=1000)
        result = analyze_row(row, DEFAULT_CONFIG, "HIGH")
        self.assertGreaterEqual(result["stress_change_7d"], -DEFAULT_CONFIG["risk_model"]["max_stress_drop"])

    def test_risk_score_boundaries(self):
        self.assertEqual(risk_level(0, DEFAULT_CONFIG), "LOW")
        self.assertEqual(risk_level(35, DEFAULT_CONFIG), "MEDIUM")
        self.assertEqual(risk_level(55, DEFAULT_CONFIG), "HIGH")
        self.assertEqual(risk_level(90, DEFAULT_CONFIG), "VERY_HIGH")

    def test_buff_missing_degrades_without_crash(self):
        result = analyze_row(base_row(buff_buy_price=None, buff_sell_price=None), DEFAULT_CONFIG, "HIGH")
        self.assertIsNone(result["yyyp_buff_buy_premium"])
        self.assertGreaterEqual(result["risk_score"], 0)


class ClassificationTests(unittest.TestCase):
    def test_sample_a_stable_arbitrage(self):
        result = analyze_row(base_row(), DEFAULT_CONFIG, "HIGH")
        self.assertEqual(result["candidate_category"], "STABLE_ARBITRAGE")

    def test_sample_b_spike_risk(self):
        result = analyze_row(base_row(
            sell_price_rate_1=0.04,
            sell_price_rate_7=0.01,
            sell_price_rate_15=0.0,
            sell_price_rate_30=0.0,
            buff_price_chg=-0.01,
        ), DEFAULT_CONFIG, "HIGH")
        self.assertIn("SPIKE_RISK", result["candidate_subcategory"])

    def test_sample_c_dip_opportunity(self):
        result = analyze_row(base_row(
            sell_price_rate_1=-0.02,
            sell_price_rate_7=-0.12,
            sell_price_rate_15=0.0,
            sell_price_rate_30=0.0,
            yyyp_buy_price=120.0,
        ), DEFAULT_CONFIG, "HIGH")
        self.assertIn("DIP_OPPORTUNITY", result["candidate_subcategory"])

    def test_sample_d_not_recommended_or_high_volatility(self):
        result = analyze_row(base_row(
            yyyp_buy_price=101.0,
            yyyp_sell_price=130.0,
            yyyp_buy_num=1,
            yyyp_sell_num=500,
        ), DEFAULT_CONFIG, "HIGH")
        self.assertIn(result["candidate_category"], {"NOT_RECOMMENDED", "HIGH_VOLATILITY"})

    def test_trend_conflict_classification(self):
        result = analyze_row(base_row(
            sell_price_rate_1=0.03,
            sell_price_rate_7=-0.08,
            sell_price_rate_15=0.05,
            sell_price_rate_30=-0.05,
        ), DEFAULT_CONFIG, "HIGH")
        self.assertIn("TREND_CONFLICT", result["candidate_subcategory"])

    def test_single_bad_item_does_not_abort_batch(self):
        df = pd.DataFrame([base_row(), {"market_hash_name": "bad", "yyyp_buy_price": "bad"}])
        result = add_risk_columns(df, DEFAULT_CONFIG, "LOW")
        self.assertEqual(len(result), 2)
        self.assertIn("candidate_category", result.columns)


class OutputTests(unittest.TestCase):
    def test_excel_multi_sheet_output_and_old_fields_preserved(self):
        df = add_risk_columns(pd.DataFrame([base_row()]), DEFAULT_CONFIG, "HIGH")
        df["中文名称"] = df["name_cn"]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "report.xlsx")
            out = export_profit_workbook(
                df,
                pd.DataFrame([{"market_hash_name": "bad", "reject_reasons": "测试"}]),
                pd.DataFrame([{"field": "sell_price_7", "confidence_level": "HIGH"}]),
                pd.DataFrame([{"metric": "x", "value": 1}]),
                path,
            )
            self.assertTrue(os.path.exists(out))
            with pd.ExcelFile(out) as xls:
                sheets = xls.sheet_names
            self.assertIn("stable_arbitrage", sheets)
            self.assertIn("all_items", sheets)
            all_items = pd.read_excel(out, sheet_name="all_items")
            self.assertIn("中文名称", all_items.columns)


if __name__ == "__main__":
    unittest.main()
