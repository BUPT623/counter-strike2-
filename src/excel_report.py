"""Excel report writer for arbitrage and seven-day risk analysis."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List

import pandas as pd

from project_paths import PROFIT_REPORT_XLSX

PERCENT_KEYWORDS = (
    "rate", "roi", "change", "dispersion", "premium", "imbalance",
    "利润率", "价差率", "收益率", "涨跌", "偏离",
)
MONEY_KEYWORDS = ("price", "cost", "profit", "income", "成本", "价格", "利润", "出货价")


def build_unlocked_output_path(path: str) -> str:
    output_dir = os.path.dirname(path)
    stem, ext = os.path.splitext(os.path.basename(path))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"{stem}_{timestamp}{ext}")


def _sort_candidates(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = [col for col in ["risk_adjusted_score", "stress_roi", "expected_profit", "yyyp_buy_num"] if col in df.columns]
    if not sort_cols:
        return df
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols))


def _sheet_frames(all_items: pd.DataFrame, rejected_items: pd.DataFrame, diagnostics: pd.DataFrame, summary: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    all_items = all_items.loc[:, ~all_items.columns.duplicated()].copy()
    rejected_items = rejected_items.loc[:, ~rejected_items.columns.duplicated()].copy()
    subcategory = all_items.get("candidate_subcategory", pd.Series("", index=all_items.index)).fillna("")
    category = all_items.get("candidate_category", pd.Series("", index=all_items.index)).fillna("")
    return {
        "stable_arbitrage": _sort_candidates(all_items[category == "STABLE_ARBITRAGE"].copy()),
        "high_volatility": _sort_candidates(all_items[category == "HIGH_VOLATILITY"].copy()),
        "spike_risk": _sort_candidates(all_items[subcategory.str.contains("SPIKE_RISK", na=False)].copy()),
        "dip_opportunity": _sort_candidates(all_items[subcategory.str.contains("DIP_OPPORTUNITY", na=False)].copy()),
        "all_items": _sort_candidates(all_items.copy()),
        "rejected_items": rejected_items.copy(),
        "semantic_diagnostics": diagnostics.copy(),
        "model_summary": summary.copy(),
    }


def _format_workbook(writer: pd.ExcelWriter, frames: Dict[str, pd.DataFrame]) -> None:
    workbook = writer.book
    for sheet_name, frame in frames.items():
        ws = workbook[sheet_name]
        for idx, column in enumerate(frame.columns, start=1):
            values = [str(column)] + ["" if pd.isna(v) else str(v) for v in frame[column].head(200).tolist()]
            width = min(max(len(v) for v in values) + 2, 42)
            ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

            lower_name = str(column).lower()
            if any(key in lower_name or key in str(column) for key in PERCENT_KEYWORDS):
                for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                    for c in cell:
                        c.number_format = "0.00%"
            elif any(key in lower_name or key in str(column) for key in MONEY_KEYWORDS):
                for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                    for c in cell:
                        c.number_format = "0.00"
        ws.freeze_panes = "A2"


def export_profit_workbook(
    all_items: pd.DataFrame,
    rejected_items: pd.DataFrame,
    diagnostics: pd.DataFrame,
    summary: pd.DataFrame,
    output_file: str = PROFIT_REPORT_XLSX,
) -> str:
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    frames = _sheet_frames(all_items, rejected_items, diagnostics, summary)

    target = output_file
    try:
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            for sheet_name, frame in frames.items():
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
            _format_workbook(writer, frames)
    except PermissionError:
        target = build_unlocked_output_path(output_file)
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            for sheet_name, frame in frames.items():
                frame.to_excel(writer, sheet_name=sheet_name, index=False)
            _format_workbook(writer, frames)
    return target
