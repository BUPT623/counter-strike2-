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
df.columns = [
    "CSQAQ_ID", "皮肤名称", "英文标准名",
    "悠悠有品售价(RMB)", "悠悠有品求购价(RMB)",
    "悠悠有品在售数量", "悠悠有品求购数量",
]
df = df.sort_values("悠悠有品售价(RMB)", ascending=False)

df.to_excel(OUTPUT, index=False)

print(f"[OK] 已导出 {len(df)} 条记录 -> {OUTPUT}")
yyyp_sell = df["悠悠有品售价(RMB)"].dropna()
print(f"     悠悠有品售价区间:   RMB {yyyp_sell.min():.2f} ~ {yyyp_sell.max():.2f}")
print(f"     悠悠有品求购价区间: RMB {df['悠悠有品求购价(RMB)'].min():.2f} ~ {df['悠悠有品求购价(RMB)'].max():.2f}")
