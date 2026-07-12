"""
导出工具 -- skinport_raw.json -> skinport_raw.xlsx
用法: python src/export_skinport_to_excel.py
"""
import json
import pandas as pd

from project_paths import SKINPORT_RAW_JSON, SKINPORT_RAW_XLSX

INPUT = SKINPORT_RAW_JSON
OUTPUT = SKINPORT_RAW_XLSX

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

df = pd.DataFrame(data)
df = df.rename(columns={
    "market_hash_name": "皮肤名称",
    "min_price": "最低售价(HKD)",
    "mean_price": "均价(HKD)",
    "median_price": "中位价(HKD)",
    "max_price": "最高价(HKD)",
    "quantity": "在售数量",
})
df = df.sort_values("最低售价(HKD)", ascending=False)

df.to_excel(OUTPUT, index=False)

print(f"[OK] 已导出 {len(df)} 条记录 -> {OUTPUT}")
print(f"     价格区间: HKD {df['最低售价(HKD)'].min():.2f} ~ HKD {df['最低售价(HKD)'].max():.2f}")
