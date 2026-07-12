"""
Sprint 3 -- 跨平台比价与 7 天风险分析器
=======================================
Inner-joins Skinport (HKD) and 悠悠有品/CSQAQ (RMB) on market_hash_name.
The report keeps the original arbitrage fields and adds seven-day risk sheets.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List
from urllib.parse import quote

import pandas as pd

from Exchange_Rate import get_hkd_to_cny_rate
from config_loader import load_config
from excel_report import export_profit_workbook
from filters import MAX_SKINPORT_PRICE_HKD, MAX_UU_PRICE_RMB
from history_store import save_market_snapshot
from logging_utils import setup_logging
from normalization import parse_float
from pricing import add_pricing_columns
from project_paths import DOMESTIC_RAW_JSON, PROFIT_REPORT_XLSX, RISK_CONFIG_JSON, SKINPORT_RAW_JSON
from risk_model import add_risk_columns
from risk_semantics import diagnose_rate_fields

JsonDict = Dict[str, Any]

SKINPORT_FILE = SKINPORT_RAW_JSON
DOMESTIC_FILE = DOMESTIC_RAW_JSON
OUTPUT_FILE = PROFIT_REPORT_XLSX

DOMESTIC_FIELDS = [
    "csqaq_id", "name", "market_hash_name",
    "yyyp_buy_price", "yyyp_sell_price",
    "sell_price_1", "sell_price_7", "sell_price_15", "sell_price_30",
    "sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30",
    "yyyp_buy_num", "yyyp_sell_num",
    "buff_price_chg", "buff_buy_price", "buff_sell_price",
    "steam_buy_price", "steam_sell_price",
    "uu_buy_price", "uu_sell_price", "uu_buy_num", "uu_sell_num",
]

SKINPORT_FIELDS = [
    "market_hash_name", "min_price", "mean_price", "median_price", "max_price",
    "quantity", "currency", "updated_at", "item_page",
]

NUMERIC_COLUMNS = [
    "min_price", "mean_price", "median_price", "max_price", "quantity",
    "yyyp_buy_price", "yyyp_sell_price", "yyyp_buy_num", "yyyp_sell_num",
    "uu_buy_price", "uu_sell_price", "uu_buy_num", "uu_sell_num",
    "sell_price_1", "sell_price_7", "sell_price_15", "sell_price_30",
    "sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30",
    "buff_price_chg", "buff_buy_price", "buff_sell_price",
    "steam_buy_price", "steam_sell_price",
]


def load_json(path: str) -> List[JsonDict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _coalesce(item: JsonDict, *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return None


def build_domestic_df(domestic: List[JsonDict]) -> pd.DataFrame:
    rows: List[JsonDict] = []
    skipped_no_key = 0
    for item in domestic:
        mhn = item.get("market_hash_name")
        if not mhn:
            skipped_no_key += 1
            continue

        row: JsonDict = {field: item.get(field) for field in DOMESTIC_FIELDS}
        row["market_hash_name"] = mhn
        row["name_cn"] = item.get("name") or mhn
        row["yyyp_sell_price"] = _coalesce(item, "yyyp_sell_price", "uu_sell_price")
        row["yyyp_buy_price"] = _coalesce(item, "yyyp_buy_price", "uu_buy_price")
        row["yyyp_sell_num"] = _coalesce(item, "yyyp_sell_num", "uu_sell_num") or 0
        row["yyyp_buy_num"] = _coalesce(item, "yyyp_buy_num", "uu_buy_num") or 0
        row["uu_sell_price"] = row["yyyp_sell_price"]
        row["uu_buy_price"] = row["yyyp_buy_price"]
        row["uu_sell_num"] = row["yyyp_sell_num"]
        row["uu_buy_num"] = row["yyyp_buy_num"]
        row["yyyp_url"] = f"https://www.csqaq.com/search?keyword={quote(str(mhn))}"
        rows.append(row)
    df = pd.DataFrame(rows)
    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def build_skinport_df(skinport: List[JsonDict]) -> pd.DataFrame:
    rows: List[JsonDict] = []
    for item in skinport:
        row = {field: item.get(field) for field in SKINPORT_FIELDS}
        mhn = row.get("market_hash_name")
        if not mhn:
            continue
        if not row.get("item_page"):
            row["item_page"] = f"https://skinport.com/market?search={quote(str(mhn))}"
        rows.append(row)
    df = pd.DataFrame(rows)
    for column in ("min_price", "mean_price", "median_price", "max_price", "quantity"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _reject_reasons(row: pd.Series) -> str:
    reasons: List[str] = []
    min_price = parse_float(row.get("min_price"))
    yyyp_sell = parse_float(row.get("yyyp_sell_price"))
    yyyp_buy = parse_float(row.get("yyyp_buy_price"))
    if min_price is None or min_price <= 0:
        reasons.append("Skinport最低价缺失或非法")
    if yyyp_sell is None or yyyp_sell <= 0:
        reasons.append("悠悠最低挂售价缺失或非法")
    if yyyp_buy is None or yyyp_buy <= 0:
        reasons.append("悠悠最高求购价缺失或非法")
    if min_price is not None and min_price > MAX_SKINPORT_PRICE_HKD:
        reasons.append(f"Skinport价格超过{MAX_SKINPORT_PRICE_HKD:.0f}HKD小额上限")
    if yyyp_sell is not None and yyyp_sell > MAX_UU_PRICE_RMB:
        reasons.append(f"悠悠售价超过{MAX_UU_PRICE_RMB:.0f}RMB小额上限")
    if yyyp_buy is not None and yyyp_sell is not None and yyyp_buy > yyyp_sell:
        reasons.append("悠悠求购价高于最低挂售价")
    return " | ".join(reasons)


def split_valid_and_rejected(merged: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = merged.copy()
    work["reject_reasons"] = work.apply(_reject_reasons, axis=1)
    rejected = work[work["reject_reasons"] != ""].copy()
    valid = work[work["reject_reasons"] == ""].drop(columns=["reject_reasons"]).copy()
    return valid, rejected


def add_legacy_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    mapping = {
        "中文名称": "name_cn",
        "海外折算人民币成本": "cost_rmb",
        "本次HKD兑RMB汇率": "exchange_rate_hkd_to_rmb",
        "悠悠有品售价(RMB)": "yyyp_sell_price",
        "悠悠有品求购价(RMB)": "yyyp_buy_price",
        "买卖价差(RMB)": "uu_spread_rmb",
        "买卖价差率(%)": "uu_spread_rate",
        "挂单动态成交系数": "listing_fill_coefficient",
        "挂单预期出货价(RMB)": "listing_expected_exit_price",
        "挂单预期利润率(%)": "listing_profit_rate",
        "即时出货价(RMB)": "instant_expected_exit_price",
        "即时出售利润率(%)": "instant_profit_rate",
        "在售数量": "yyyp_sell_num",
        "求购数量": "yyyp_buy_num",
        "Skinport链接": "item_page",
        "悠悠链接": "yyyp_url",
    }
    for display, source in mapping.items():
        if source in out.columns:
            out[display] = out[source]
    return out


def build_summary(
    analyzed: pd.DataFrame,
    rejected: pd.DataFrame,
    diagnostics: pd.DataFrame,
    exchange_rate: float,
    config_path: str,
) -> pd.DataFrame:
    rows: List[JsonDict] = [
        {"metric": "calculated_at", "value": pd.Timestamp.now(tz="Asia/Shanghai").isoformat()},
        {"metric": "exchange_rate_hkd_to_rmb", "value": exchange_rate},
        {"metric": "config_path", "value": config_path},
        {"metric": "analyzed_items", "value": len(analyzed)},
        {"metric": "rejected_items", "value": len(rejected)},
    ]
    if "candidate_category" in analyzed.columns:
        for category, count in analyzed["candidate_category"].fillna("UNKNOWN").value_counts().items():
            rows.append({"metric": f"category_{category}", "value": int(count)})
    if "risk_level" in analyzed.columns:
        for level, count in analyzed["risk_level"].fillna("UNKNOWN").value_counts().items():
            rows.append({"metric": f"risk_level_{level}", "value": int(count)})
    for _, diag in diagnostics.iterrows():
        rows.append({
            "metric": f"{diag.get('field')}_meaning",
            "value": f"{diag.get('inferred_meaning')} / {diag.get('confidence_level')} / match={diag.get('match_ratio')}",
        })
    return pd.DataFrame(rows)


def analyze(
    skinport_df: pd.DataFrame,
    domestic_df: pd.DataFrame,
    exchange_rate_hkd_to_rmb: float,
    config: Dict[str, Any],
    logger: Any,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    merged = pd.merge(skinport_df, domestic_df, on="market_hash_name", how="inner")
    logger.info("Inner join matched: %s items", len(merged))

    diagnostics = diagnose_rate_fields(merged, config)
    for _, row in diagnostics.iterrows():
        logger.info(
            "%s semantic: valid=%s median_error=%s match=%.2f confidence=%s inferred=%s",
            row.get("field"),
            row.get("valid_sample_count"),
            row.get("median_abs_error"),
            row.get("match_ratio") or 0.0,
            row.get("confidence_level"),
            row.get("inferred_meaning"),
        )

    valid, rejected = split_valid_and_rejected(merged)
    logger.info("Valid fundamentals: %s | rejected: %s", len(valid), len(rejected))

    priced = add_pricing_columns(valid, exchange_rate_hkd_to_rmb)

    semantic_row = diagnostics[diagnostics["field"] == "sell_price_7"]
    semantic_confidence = "LOW"
    if not semantic_row.empty:
        semantic_confidence = str(semantic_row.iloc[0].get("confidence_level") or "LOW")
        if str(semantic_row.iloc[0].get("inferred_meaning")) == "unknown":
            semantic_confidence = "LOW"

    analyzed = add_risk_columns(priced, config, semantic_confidence)
    analyzed = add_legacy_display_columns(analyzed)
    analyzed = analyzed.sort_values("risk_adjusted_score", ascending=False)

    summary = build_summary(analyzed, rejected, diagnostics, exchange_rate_hkd_to_rmb, RISK_CONFIG_JSON)
    return analyzed, rejected, diagnostics, summary


def main() -> None:
    logger = setup_logging("analyzer", "analyzer")
    logger.info("=== Sprint 3: Cross-Platform Arbitrage Analyzer (UU + 7d Risk) ===")

    try:
        logger.info("[1/5] Loading config and data sources ...")
        config = load_config(logger=logger)
        for fpath, label in [
            (SKINPORT_FILE, "Skinport (Sprint 1)"),
            (DOMESTIC_FILE, "Domestic (Sprint 2)"),
        ]:
            if not os.path.exists(fpath):
                logger.error("%s not found. Run %s first.", fpath, label)
                sys.exit(1)

        skinport = load_json(SKINPORT_FILE)
        domestic = load_json(DOMESTIC_FILE)
        logger.info("Skinport: %s | Domestic (UU): %s", len(skinport), len(domestic))

        logger.info("[2/5] Building DataFrames and matching on market_hash_name ...")
        skinport_df = build_skinport_df(skinport)
        domestic_df = build_domestic_df(domestic)

        logger.info("[3/5] Fetching startup exchange rate and computing risk ...")
        rate_info = get_hkd_to_cny_rate()
        exchange_rate = float(rate_info["cny_per_hkd"])
        analyzed, rejected, diagnostics, summary = analyze(
            skinport_df, domestic_df, exchange_rate, config, logger
        )

        logger.info("[4/5] Saving historical snapshot ...")
        save_market_snapshot(analyzed, logger=logger)

        logger.info("[5/5] Exporting Excel workbook ...")
        output_file = export_profit_workbook(analyzed, rejected, diagnostics, summary, OUTPUT_FILE)
        logger.info("Excel report saved: %s", output_file)

        top_stable = analyzed[analyzed["candidate_category"] == "STABLE_ARBITRAGE"].head(5)
        logger.info("Top stable candidates: %s", len(top_stable))
        for _, row in top_stable.iterrows():
            logger.info(
                "%s | stress_roi=%.2f%% | risk=%s %.1f | score=%.3f",
                str(row.get("name_cn"))[:45],
                (row.get("stress_roi") or 0) * 100,
                row.get("risk_level"),
                row.get("risk_score") or 0,
                row.get("risk_adjusted_score") or 0,
            )

        logger.info("[OK] Sprint 3 complete -- %s items analyzed.", len(analyzed))

    except FileNotFoundError as exc:
        logger.error("File not found: %s", exc)
        sys.exit(1)
    except KeyError as exc:
        logger.error("Missing expected field: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("%s: %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
