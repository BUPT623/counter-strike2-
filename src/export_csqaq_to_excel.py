"""
导出工具 -- domestic_raw.json -> domestic_raw.xlsx
用法: python src/export_csqaq_to_excel.py
"""
import json
import pandas as pd

from project_paths import DOMESTIC_RAW_JSON, DOMESTIC_RAW_XLSX

INPUT = DOMESTIC_RAW_JSON
OUTPUT = DOMESTIC_RAW_XLSX

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

df = pd.DataFrame(data)
for yyyp_col, uu_col in [
    ("yyyp_sell_price", "uu_sell_price"),
    ("yyyp_buy_price", "uu_buy_price"),
    ("yyyp_sell_num", "uu_sell_num"),
    ("yyyp_buy_num", "uu_buy_num"),
]:
    if yyyp_col in df.columns and uu_col in df.columns:
        df = df.drop(columns=[uu_col])
rename_map = {
    "csqaq_id": "CSQAQ_ID",
    "name": "皮肤名称",
    "market_hash_name": "英文标准名",
    "yyyp_sell_price": "悠悠有品售价(RMB)",
    "yyyp_buy_price": "悠悠有品求购价(RMB)",
    "yyyp_sell_num": "悠悠有品在售数量",
    "yyyp_buy_num": "悠悠有品求购数量",
    "uu_sell_price": "悠悠有品售价(RMB)",
    "uu_buy_price": "悠悠有品求购价(RMB)",
    "uu_sell_num": "悠悠有品在售数量",
    "uu_buy_num": "悠悠有品求购数量",
}
df = df.rename(columns=rename_map)
sort_col = "悠悠有品售价(RMB)" if "悠悠有品售价(RMB)" in df.columns else "uu_sell_price"
df = df.sort_values(sort_col, ascending=False)

df.to_excel(OUTPUT, index=False)

print(f"[OK] 已导出 {len(df)} 条记录 -> {OUTPUT}")
if "悠悠有品售价(RMB)" in df.columns:
    yyyp_sell = df["悠悠有品售价(RMB)"].dropna()
    print(f"     悠悠有品售价区间:   RMB {yyyp_sell.min():.2f} ~ {yyyp_sell.max():.2f}")
if "悠悠有品求购价(RMB)" in df.columns:
    yyyp_buy = df["悠悠有品求购价(RMB)"].dropna()
    print(f"     悠悠有品求购价区间: RMB {yyyp_buy.min():.2f} ~ {yyyp_buy.max():.2f}")
