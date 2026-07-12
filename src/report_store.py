"""JSON report writer/reader helpers for analyzer output."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import pandas as pd

from project_paths import PROFIT_REPORT_JSON

JsonDict = Dict[str, Any]


def _clean_frame(frame: pd.DataFrame) -> List[JsonDict]:
    if frame is None or frame.empty:
        return []
    cleaned = frame.loc[:, ~frame.columns.duplicated()].copy()
    cleaned = cleaned.where(pd.notna(cleaned), None)
    return cleaned.to_dict(orient="records")


def export_profit_json_report(
    all_items: pd.DataFrame,
    rejected_items: pd.DataFrame,
    diagnostics: pd.DataFrame,
    summary: pd.DataFrame,
    output_file: str = PROFIT_REPORT_JSON,
) -> str:
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "sheets": {
            "all_items": _clean_frame(all_items),
            "rejected_items": _clean_frame(rejected_items),
            "semantic_diagnostics": _clean_frame(diagnostics),
            "model_summary": _clean_frame(summary),
        },
    }
    temp_file = output_file + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    os.replace(temp_file, output_file)
    return output_file


def load_profit_json_sheet(path: str, sheet_name: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    rows = (payload.get("sheets") or {}).get(sheet_name) or []
    return pd.DataFrame(rows)
