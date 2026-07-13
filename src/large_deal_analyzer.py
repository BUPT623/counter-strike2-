"""Large-ticket arbitrage analyzer.

This module intentionally skips the seven-day risk model and history backtest.
The goal is to surface only the top large-value price gaps by profit rate and
profit amount.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd

from Exchange_Rate import get_hkd_to_cny_rate
from analyzer import build_domestic_df, build_skinport_df, load_json
from logging_utils import setup_logging
from project_paths import (
    LARGE_DEAL_CONFIG_JSON,
    LARGE_DEAL_REPORT_JSON,
    LARGE_DOMESTIC_RAW_JSON,
    LARGE_SKINPORT_RAW_JSON,
)

JsonDict = Dict[str, Any]

DEFAULT_CONFIG: JsonDict = {
    "filters": {
        "skinport_min_price_hkd": 2300,
        "skinport_max_price_hkd": 12000,
        "uu_min_sell_price_rmb": 2000,
        "uu_max_sell_price_rmb": 10000,
        "min_uu_sell_num": 50,
    },
    "scoring": {
        "top_n": 5,
        "profit_rate_weight": 60,
        "profit_amount_weight": 40,
        "roi_cap": 0.25,
        "profit_cap_rmb": 1200,
    },
}


def load_large_config(path: str = LARGE_DEAL_CONFIG_JSON) -> JsonDict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if not os.path.exists(path):
        return config
    with open(path, "r", encoding="utf-8") as f:
        override = json.load(f)
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(config.get(section), dict):
            config[section].update(values)
        else:
            config[section] = values
    return config


def _bounded(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    if pd.isna(value):
        return 0.0
    return max(0.0, min(value / cap, 1.0))


def add_large_score_columns(df: pd.DataFrame, config: JsonDict) -> pd.DataFrame:
    scored = df.copy()
    scoring = config["scoring"]
    roi_cap = float(scoring["roi_cap"])
    profit_cap = float(scoring["profit_cap_rmb"])

    scored["deal_expected_exit_price"] = scored["yyyp_sell_price"]
    scored["deal_profit_rmb"] = scored["deal_expected_exit_price"] - scored["cost_rmb"]
    scored["deal_profit_rate"] = scored["deal_profit_rmb"] / scored["cost_rmb"]
    scored["best_profit_rate"] = scored["deal_profit_rate"]
    scored["best_profit_rmb"] = scored["deal_profit_rmb"]
    scored["recommended_exit_mode"] = "捡漏模式"

    scored["recommendation_score"] = (
        float(scoring["profit_rate_weight"]) * scored["deal_profit_rate"].apply(lambda value: _bounded(float(value or 0), roi_cap))
        + float(scoring["profit_amount_weight"]) * scored["deal_profit_rmb"].apply(lambda value: _bounded(float(value or 0), profit_cap))
    ).clip(lower=0, upper=100)

    scored["recommendation_grade"] = scored["recommendation_score"].apply(
        lambda score: "A" if score >= 75 else "B" if score >= 60 else "C" if score >= 35 else "D"
    )
    scored["candidate_category"] = "LARGE_DEAL"
    scored["display_category"] = "LARGE_DEAL"
    scored["display_category_cn"] = "大额捡漏"
    scored["risk_level"] = "不做风险模型"
    scored["risk_reasons"] = "大额捡漏模式：预期出货价按UU售价计算，仅按利润率和利润额排序，未做7天风险模型和历史回测"
    scored["flat_roi"] = scored["best_profit_rate"]
    scored["skinport_cost_cny"] = scored["cost_rmb"]
    scored["name_cn"] = scored.get("name_cn", scored.get("name"))
    return scored


def apply_large_filters(df: pd.DataFrame, config: JsonDict) -> tuple[pd.DataFrame, pd.DataFrame]:
    filters = config["filters"]
    work = df.copy()
    reasons: List[str] = []
    keep_mask = pd.Series(True, index=work.index)

    checks = [
        (work["min_price"] >= float(filters["skinport_min_price_hkd"]), f"Skinport低于{filters['skinport_min_price_hkd']}HKD"),
        (work["min_price"] <= float(filters["skinport_max_price_hkd"]), f"Skinport高于{filters['skinport_max_price_hkd']}HKD"),
        (work["yyyp_sell_price"] >= float(filters["uu_min_sell_price_rmb"]), f"UU售价低于{filters['uu_min_sell_price_rmb']}RMB"),
        (work["yyyp_sell_price"] <= float(filters["uu_max_sell_price_rmb"]), f"UU售价高于{filters['uu_max_sell_price_rmb']}RMB"),
        (work["yyyp_sell_num"] > int(filters["min_uu_sell_num"]), f"UU在售数量不大于{filters['min_uu_sell_num']}"),
        (work["yyyp_buy_price"] <= work["yyyp_sell_price"], "UU求购价高于UU售价"),
    ]
    reject_reasons = pd.Series("", index=work.index, dtype=object)
    for mask, reason in checks:
        failed = ~mask.fillna(False)
        reject_reasons.loc[failed] = reject_reasons.loc[failed].apply(lambda old: f"{old} | {reason}" if old else reason)
        keep_mask &= mask.fillna(False)
        reasons.append(reason)

    rejected = work[~keep_mask].copy()
    rejected["reject_reasons"] = reject_reasons[~keep_mask]
    return work[keep_mask].copy(), rejected


def build_summary(analyzed: pd.DataFrame, rejected: pd.DataFrame, config: JsonDict, exchange_rate: float) -> JsonDict:
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "exchange_rate_hkd_to_rmb": exchange_rate,
        "config_path": LARGE_DEAL_CONFIG_JSON,
        "matched_items": int(len(analyzed) + len(rejected)),
        "eligible_items": int(len(analyzed)),
        "rejected_items": int(len(rejected)),
        "top_n": int(config["scoring"]["top_n"]),
    }


def _records(frame: pd.DataFrame) -> List[JsonDict]:
    if frame.empty:
        return []
    cleaned = frame.copy().astype(object)
    cleaned = cleaned.where(pd.notna(cleaned), None)
    return cleaned.to_dict(orient="records")


def write_report(all_candidates: pd.DataFrame, recommendations: pd.DataFrame, rejected: pd.DataFrame, summary: JsonDict) -> str:
    os.makedirs(os.path.dirname(LARGE_DEAL_REPORT_JSON), exist_ok=True)
    payload = {
        "generated_at": summary["generated_at"],
        "summary": summary,
        "recommendations": _records(recommendations),
        "all_candidates": _records(all_candidates),
        "rejected_items": _records(rejected),
    }
    temp_file = LARGE_DEAL_REPORT_JSON + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    os.replace(temp_file, LARGE_DEAL_REPORT_JSON)
    return LARGE_DEAL_REPORT_JSON


def analyze_large(logger: Any) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, JsonDict]:
    config = load_large_config()
    for path, label in [
        (LARGE_SKINPORT_RAW_JSON, "large Skinport"),
        (LARGE_DOMESTIC_RAW_JSON, "large CSQAQ"),
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"{label} data not found: {path}")

    skinport_df = build_skinport_df(load_json(LARGE_SKINPORT_RAW_JSON))
    domestic_df = build_domestic_df(load_json(LARGE_DOMESTIC_RAW_JSON))
    merged = pd.merge(skinport_df, domestic_df, on="market_hash_name", how="inner")
    logger.info("Large-ticket inner join matched: %s items", len(merged))

    valid, rejected = apply_large_filters(merged, config)
    logger.info("Large-ticket eligible: %s | rejected: %s", len(valid), len(rejected))

    rate_info = get_hkd_to_cny_rate()
    exchange_rate = float(rate_info["cny_per_hkd"])
    priced = valid.copy()
    priced["exchange_rate_hkd_to_rmb"] = exchange_rate
    priced["cost_rmb"] = priced["min_price"] * exchange_rate
    priced["skinport_cost_cny"] = priced["cost_rmb"]
    scored = add_large_score_columns(priced, config)
    scored = scored.sort_values(["recommendation_score", "best_profit_rmb", "best_profit_rate"], ascending=False)
    recommendations = scored.head(int(config["scoring"]["top_n"])).copy()
    summary = build_summary(scored, rejected, config, exchange_rate)
    return scored, recommendations, rejected, summary


def main() -> None:
    logger = setup_logging("large_deal_analyzer", "large_deal_analyzer")
    logger.info("=== Large Ticket: Arbitrage Analyzer ===")
    try:
        all_candidates, recommendations, rejected, summary = analyze_large(logger)
        output_file = write_report(all_candidates, recommendations, rejected, summary)
        logger.info("Large-ticket JSON report saved: %s", output_file)
        logger.info("Top recommendations: %s", len(recommendations))
        for _, row in recommendations.iterrows():
            logger.info(
                "%s | score=%.1f | deal_roi=%.2f%% | deal_profit=%.2f RMB | uu_sell=%.2f",
                str(row.get("name_cn") or row.get("market_hash_name"))[:50],
                row.get("recommendation_score") or 0,
                (row.get("deal_profit_rate") or 0) * 100,
                row.get("deal_profit_rmb") or 0,
                row.get("yyyp_sell_price") or 0,
            )
        logger.info("[OK] Large-ticket analyzer complete.")
    except Exception as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
