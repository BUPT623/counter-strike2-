"""
Sprint 3 -- 跨平台比价分析器 (悠悠有品专用)
=============================================
Inner-joins Skinport (HKD) and 悠悠有品 (RMB) on native market_hash_name.
No translation layer — domestic data already carries the English key from CSQAQ.
"""
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

from Exchange_Rate import get_hkd_to_cny_rate
from filters import MAX_SKINPORT_PRICE_HKD, MAX_UU_PRICE_RMB
from pricing import add_pricing_columns
from project_paths import DOMESTIC_RAW_JSON, PROFIT_REPORT_XLSX, SKINPORT_RAW_JSON

JsonDict = Dict[str, Any]

# ---- constants ----
MAX_PROFIT_RATE = 0.60       # 利润率上限 (>50% = 数据污染)
MIN_UU_SELL_NUM = 40         # 在售数量下限 (流动性红线)
MIN_UU_BUY_NUM = 10          # 求购数量下限 (流动性红线)

SKINPORT_FILE = SKINPORT_RAW_JSON
DOMESTIC_FILE = DOMESTIC_RAW_JSON
OUTPUT_FILE = PROFIT_REPORT_XLSX


def load_json(path: str) -> List[JsonDict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_domestic_df(domestic: List[JsonDict]) -> pd.DataFrame:
    """
    Build domestic DataFrame directly from CSQAQ native fields.
    No translation — market_hash_name comes from the API detail endpoint.
    Falls back to market_hash_name for Chinese display if `name` is missing.
    """
    rows: List[JsonDict] = []
    skipped_no_key = 0
    for item in domestic:
        mhn = item.get("market_hash_name")
        if not mhn:
            skipped_no_key += 1
            continue
        name_cn = item.get("name") or mhn  # fallback: English name as display
        rows.append({
            "market_hash_name": mhn,
            "name_cn": name_cn,
            "uu_sell_price": item.get("uu_sell_price"),
            "uu_buy_price": item.get("uu_buy_price"),
            "uu_sell_num": item.get("uu_sell_num", 0),
            "uu_buy_num": item.get("uu_buy_num", 0),
        })
    print(f"  Domestic valid (with market_hash_name): {len(rows)}, skipped: {skipped_no_key}")
    return pd.DataFrame(rows)


def analyze(
    skinport_df: pd.DataFrame,
    domestic_df: pd.DataFrame,
    exchange_rate_hkd_to_rmb: float,
) -> pd.DataFrame:
    """Inner join on native market_hash_name, compute profit, apply filters."""
    merged = pd.merge(
        skinport_df, domestic_df,
        on="market_hash_name", how="inner",
    )
    print(f"  Inner join matched: {len(merged)} items")

    before = len(merged)
    merged = merged[
        merged["min_price"].notna()
        & merged["uu_sell_price"].notna()
        & merged["uu_buy_price"].notna()
    ]
    print(f"  Filter required prices present: {before} -> {len(merged)} ({before - len(merged)} removed)")

    before = len(merged)
    merged = merged[merged["uu_buy_price"] <= merged["uu_sell_price"]]
    print(f"  Filter uu_buy_price <= uu_sell_price: {before} -> {len(merged)} ({before - len(merged)} removed)")

    before = len(merged)
    merged = merged[merged["min_price"] <= MAX_SKINPORT_PRICE_HKD]
    print(f"  Filter Skinport <= {MAX_SKINPORT_PRICE_HKD:.0f} HKD: {before} -> {len(merged)} ({before - len(merged)} removed)")

    before = len(merged)
    merged = merged[merged["uu_sell_price"] <= MAX_UU_PRICE_RMB]
    print(f"  Filter UU sell <= {MAX_UU_PRICE_RMB:.0f} RMB: {before} -> {len(merged)} ({before - len(merged)} removed)")

    merged = add_pricing_columns(merged, exchange_rate_hkd_to_rmb)

    # --- Hard filters ---
    before = len(merged)

    merged = merged[merged["uu_sell_num"] >= MIN_UU_SELL_NUM]
    after_sell = len(merged)
    print(f"  Filter uu_sell_num >= {MIN_UU_SELL_NUM}: {before} -> {after_sell} ({before - after_sell} removed)")

    before = after_sell
    merged = merged[merged["uu_buy_num"] >= MIN_UU_BUY_NUM]
    after_buy = len(merged)
    print(f"  Filter uu_buy_num >= {MIN_UU_BUY_NUM}: {before} -> {after_buy} ({before - after_buy} removed)")

    before = after_buy
    merged = merged[
        (merged["raw_profit_rate"] >= 0.0) & (merged["raw_profit_rate"] <= MAX_PROFIT_RATE)
    ]
    print(f"  Filter 0% <= listing profit <= {MAX_PROFIT_RATE*100:.0f}%: {before} -> {len(merged)} ({before - len(merged)} removed)")

    merged = merged.sort_values("raw_profit_rate", ascending=False)
    return merged


def build_unlocked_output_path(path: str) -> str:
    output_dir = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"{stem}_{timestamp}{ext}")


def export(merged: pd.DataFrame) -> str:
    """Export YYYP-only report to Excel."""
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    report = merged[[
        "name_cn", "cost_rmb",
        "exchange_rate_hkd_to_rmb",
        "uu_sell_price", "uu_buy_price",
        "uu_spread_rmb", "uu_spread_rate",
        "listing_fill_coefficient", "listing_expected_exit_price", "listing_profit_rate",
        "instant_expected_exit_price", "instant_profit_rate",
        "uu_sell_num", "uu_buy_num",
    ]].copy()

    report.columns = [
        "中文名称", "海外折算人民币成本",
        "本次HKD兑RMB汇率",
        "悠悠有品售价(RMB)", "悠悠有品求购价(RMB)",
        "买卖价差(RMB)", "买卖价差率(%)",
        "挂单动态成交系数", "挂单预期出货价(RMB)", "挂单预期利润率(%)",
        "即时出货价(RMB)", "即时出售利润率(%)",
        "在售数量", "求购数量",
    ]

    report["买卖价差率(%)"] = (report["买卖价差率(%)"] * 100).round(2)
    report["本次HKD兑RMB汇率"] = report["本次HKD兑RMB汇率"].round(6)
    for column in [
        "海外折算人民币成本",
        "悠悠有品售价(RMB)",
        "悠悠有品求购价(RMB)",
        "买卖价差(RMB)",
        "挂单预期出货价(RMB)",
        "即时出货价(RMB)",
    ]:
        report[column] = report[column].round(2)
    report["挂单动态成交系数"] = report["挂单动态成交系数"].round(4)
    report["挂单预期利润率(%)"] = (report["挂单预期利润率(%)"] * 100).round(2)
    report["即时出售利润率(%)"] = (report["即时出售利润率(%)"] * 100).round(2)

    try:
        report.to_excel(OUTPUT_FILE, index=False)
        output_file = OUTPUT_FILE
    except PermissionError:
        output_file = build_unlocked_output_path(OUTPUT_FILE)
        report.to_excel(output_file, index=False)
        print(f"  [WARN] Default report is open or locked, saved a new copy instead.")

    print(f"  Report saved: {output_file}")
    return output_file


def main() -> None:
    print("=== Sprint 3: Cross-Platform Arbitrage Analyzer (UU Only) ===\n")

    try:
        print("[1/4] Loading data sources ...")
        for fpath, label in [
            (SKINPORT_FILE, "Skinport (Sprint 1)"),
            (DOMESTIC_FILE, "Domestic (Sprint 2)"),
        ]:
            if not os.path.exists(fpath):
                print(f"[ERROR] {fpath} not found. Run {label} first.", file=sys.stderr)
                sys.exit(1)

        skinport = load_json(SKINPORT_FILE)
        domestic = load_json(DOMESTIC_FILE)
        print(f"  Skinport: {len(skinport)}  |  Domestic (UU): {len(domestic)}")

        print("\n[2/4] Building DataFrames & aligning on market_hash_name ...")
        skinport_df = pd.DataFrame(skinport)
        domestic_df = build_domestic_df(domestic)

        print("\n[3/4] Computing profit margins & applying filters ...")
        rate_info = get_hkd_to_cny_rate()
        exchange_rate = float(rate_info["cny_per_hkd"])
        merged = analyze(skinport_df, domestic_df, exchange_rate)

        print("\n[4/4] Exporting report ...")
        output_file = export(merged)

        print("\n=== Top 5 悠悠有品 Profit ===")
        for _, row in merged.head(5).iterrows():
            raw_pct = row["raw_profit_rate"] * 100
            instant_pct = row["instant_profit_rate"] * 100
            print(f"  {row['name_cn'][:45]}")
            print(f"    Cost: RMB {row['cost_rmb']:.2f}  |  Listing exit: RMB {row['listing_expected_exit_price']:.2f}")
            print(f"    Instant exit: RMB {row['instant_expected_exit_price']:.2f}")
            print(f"    UU sell: RMB {row['uu_sell_price']:.2f}  |  UU buy: RMB {row['uu_buy_price']:.2f}")
            print(f"    挂单系数: {row['listing_fill_coefficient']:.3f}  |  在售: {int(row['uu_sell_num'])}  |  求购: {int(row['uu_buy_num'])}")
            print(f"    Listing profit: {raw_pct:.1f}%  |  Instant profit: {instant_pct:.1f}%")
            print()

        print(f"[OK] Sprint 3 complete -- {len(merged)} items in {output_file}\n")

    except FileNotFoundError as exc:
        print(f"[ERROR] File not found: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyError as exc:
        print(f"[ERROR] Missing expected field: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
