"""Shared recommendation-pool selection used by the dashboard and notifier."""
from __future__ import annotations

import math
import os
from functools import lru_cache
from glob import glob
from typing import Any, Dict, List, Optional

import pandas as pd

from project_paths import OUTPUT_DIR, PROFIT_REPORT_JSON
from report_store import load_profit_json_sheet

PAGE_SIZE = 300
SPIKE_PROFIT_THRESHOLD = 0.10

CATEGORY_LABELS = {
    "STABLE_ARBITRAGE": "稳定搬砖",
    "SPIKE_RISK": "异常上涨",
    "SPIKE_RISK_PROFIT": "异常上涨",
    "HIGH_VOLATILITY": "异常波动",
    "DIP_OPPORTUNITY": "异常下跌",
    "TREND_CONFLICT": "趋势冲突",
    "CROSS_MARKET_DIVERGENCE": "跨平台异常",
    "NOT_RECOMMENDED": "未推荐",
    "INSUFFICIENT_DATA": "数据不足",
    "BACKTEST_STRONG": "回测强推荐",
}

SUBCATEGORY_LABELS = {
    "SPIKE_RISK": "异常上涨",
    "DIP_OPPORTUNITY": "异常下跌",
    "TREND_CONFLICT": "趋势冲突",
    "CROSS_MARKET_DIVERGENCE": "跨平台异常",
}


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def score_to_star_rating(value: Any) -> Optional[float]:
    number = to_float(value)
    if number is None:
        return None
    half_steps = round(max(0.0, min(100.0, number)) / 10.0)
    half_steps = max(1, min(10, half_steps))
    return half_steps / 2.0


def star_width(value: Any) -> str:
    rating = score_to_star_rating(value)
    if rating is None:
        return "0%"
    return f"{rating / 5.0 * 100:.0f}%"


def star_label(value: Any) -> str:
    rating = score_to_star_rating(value)
    if rating is None:
        return "-"
    return f"{rating:.1f}星"


def star_text(value: Any) -> str:
    rating = score_to_star_rating(value)
    if rating is None:
        return "-"
    full = int(rating)
    has_half = rating - full >= 0.5
    empty = max(0, 5 - full - (1 if has_half else 0))
    return ("★" * full) + ("½" if has_half else "") + ("☆" * empty)


def localize_category(value: Any) -> str:
    text = str(value or "")
    return CATEGORY_LABELS.get(text, text or "-")


def localize_subcategories(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    parts = [part.strip() for part in text.split("|")]
    labels = [SUBCATEGORY_LABELS.get(part, part) for part in parts if part]
    return " | ".join(dict.fromkeys(labels))


def clean_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    cleaned = df.replace({float("nan"): None})
    records = cleaned.to_dict(orient="records")
    for record in records:
        for key, value in list(record.items()):
            if isinstance(value, float) and math.isnan(value):
                record[key] = None
        display_category = record.get("display_category") or record.get("candidate_category")
        record["display_category_cn"] = localize_category(display_category)
        record["candidate_subcategory_cn"] = localize_subcategories(record.get("candidate_subcategory"))
    return records


def latest_report_path() -> Optional[str]:
    if os.path.exists(PROFIT_REPORT_JSON):
        return PROFIT_REPORT_JSON
    candidates = glob(os.path.join(OUTPUT_DIR, "profit_report*.xlsx"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


@lru_cache(maxsize=16)
def _load_report_sheet(path: str, mtime_ns: int, sheet_name: str) -> pd.DataFrame:
    del mtime_ns
    if path.lower().endswith(".json"):
        return load_profit_json_sheet(path, sheet_name)
    return pd.read_excel(path, sheet_name=sheet_name)


def load_report_sheet(sheet_name: str) -> pd.DataFrame:
    path = latest_report_path()
    if not path:
        return pd.DataFrame()
    stat = os.stat(path)
    try:
        return _load_report_sheet(path, stat.st_mtime_ns, sheet_name).copy()
    except ValueError:
        return pd.DataFrame()


def _apply_text_filter(df: pd.DataFrame, q: str) -> pd.DataFrame:
    if not q or df.empty:
        return df
    needle = q.lower()
    name_col = df.get("name_cn", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    hash_col = df.get("market_hash_name", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
    return df[name_col.str.contains(needle, regex=False) | hash_col.str.contains(needle, regex=False)]


def _apply_grade_filter(df: pd.DataFrame, grade: str) -> pd.DataFrame:
    if grade and grade != "ALL" and "recommendation_grade" in df.columns:
        return df[df["recommendation_grade"].fillna("") == grade]
    return df


def _sort_by_profit(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = [col for col in ["flat_roi", "recommendation_score", "stress_roi"] if col in df.columns]
    if not sort_cols:
        return df
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols))


def _sort_stable(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = [col for col in ["recommendation_score", "stress_roi", "flat_roi"] if col in df.columns]
    if not sort_cols:
        return df
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols))


def _tag_rows(df: pd.DataFrame, label: str) -> pd.DataFrame:
    tagged = df.copy()
    tagged["display_category"] = label
    return tagged


def _subcategory_contains(df: pd.DataFrame, keyword: str) -> pd.Series:
    if "candidate_subcategory" not in df.columns:
        return pd.Series(False, index=df.index)
    return df["candidate_subcategory"].fillna("").astype(str).str.contains(keyword, regex=False)


def build_highlight_recommendations(df: pd.DataFrame, grade: str = "ALL", q: str = "") -> pd.DataFrame:
    """Return the current featured pool: all stable items plus profitable spike-risk items."""
    df = _apply_grade_filter(_apply_text_filter(df, q), grade)
    if df.empty:
        return df

    frames: List[pd.DataFrame] = []
    category_col = df.get("candidate_category", pd.Series("", index=df.index)).fillna("")

    stable = _sort_stable(df[category_col == "STABLE_ARBITRAGE"]).copy()
    if not stable.empty:
        frames.append(_tag_rows(stable, "STABLE_ARBITRAGE"))

    spike_mask = _subcategory_contains(df, "SPIKE_RISK")
    if "flat_roi" in df.columns:
        spike_mask = spike_mask & (pd.to_numeric(df["flat_roi"], errors="coerce") > SPIKE_PROFIT_THRESHOLD)
    spike = _sort_by_profit(df[spike_mask])
    if not spike.empty:
        frames.append(_tag_rows(spike, "SPIKE_RISK_PROFIT"))

    if not frames:
        return df.head(0)
    highlights = pd.concat(frames, ignore_index=True)
    if "market_hash_name" in highlights.columns:
        highlights = highlights.drop_duplicates(subset=["market_hash_name"], keep="first")
    return highlights


def filter_recommendations_from_df(
    df: pd.DataFrame,
    category: str = "HIGHLIGHTS",
    grade: str = "ALL",
    q: str = "",
    limit: int = PAGE_SIZE,
) -> List[Dict[str, Any]]:
    if df.empty:
        return []

    if category == "HIGHLIGHTS":
        filtered = build_highlight_recommendations(df, grade, q)
        return clean_records(filtered)

    filtered = _apply_grade_filter(_apply_text_filter(df, q), grade)
    category_col = filtered.get("candidate_category", pd.Series("", index=filtered.index)).fillna("")
    if category == "SPIKE_RISK":
        filtered = filtered[_subcategory_contains(filtered, "SPIKE_RISK")]
    elif category == "DIP_OPPORTUNITY":
        filtered = filtered[_subcategory_contains(filtered, "DIP_OPPORTUNITY")]
    elif category and category != "ALL":
        filtered = filtered[category_col == category]

    if category == "STABLE_ARBITRAGE":
        filtered = _sort_stable(filtered)
    else:
        filtered = _sort_by_profit(filtered)

    if "display_category" not in filtered.columns and not filtered.empty:
        filtered = filtered.copy()
        filtered["display_category"] = filtered.get("candidate_category", "")
    return clean_records(filtered.head(limit))


def filter_recommendations(
    category: str = "HIGHLIGHTS",
    grade: str = "ALL",
    q: str = "",
    limit: int = PAGE_SIZE,
) -> List[Dict[str, Any]]:
    return filter_recommendations_from_df(load_report_sheet("all_items"), category, grade, q, limit)
