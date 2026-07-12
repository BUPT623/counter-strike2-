"""Local FastAPI dashboard for CS2 arbitrage recommendations and backtests."""
from __future__ import annotations

import math
import os
import sqlite3
from contextlib import closing
from functools import lru_cache
from glob import glob
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from project_paths import OUTPUT_DIR, RECOMMENDATION_BACKTEST_DB
from recommendation_pool import (
    filter_recommendations as pool_filter_recommendations,
    latest_report_path as pool_latest_report_path,
    load_report_sheet as pool_load_report_sheet,
    star_label,
    star_width,
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(APP_DIR, "templates")
STATIC_DIR = os.path.join(APP_DIR, "static")
PAGE_SIZE = 300
SPIKE_PROFIT_THRESHOLD = 0.10

app = FastAPI(title="CS-APP Dashboard")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


def fmt_money(value: Any) -> str:
    number = _to_float(value)
    return "-" if number is None else f"{number:,.2f}"


def fmt_pct(value: Any) -> str:
    number = _to_float(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def fmt_score(value: Any) -> str:
    number = _to_float(value)
    return "-" if number is None else f"{number:.1f}"


def fmt_qty(value: Any) -> str:
    number = _to_float(value)
    return "-" if number is None else f"{int(number):,}"


def short_time(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    text = str(value)
    return text.replace("T", " ").replace("+00:00", " UTC")[:19]


templates.env.filters["money"] = fmt_money
templates.env.filters["pct"] = fmt_pct
templates.env.filters["score"] = fmt_score
templates.env.filters["qty"] = fmt_qty
templates.env.filters["short_time"] = short_time
templates.env.filters["star_label"] = star_label
templates.env.filters["star_width"] = star_width

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


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
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


def _backtest_record_to_recommendation(row: Dict[str, Any]) -> Dict[str, Any]:
    cost = _to_float(row.get("skinport_cost_cny"))
    buy_price = _to_float(row.get("yyyp_buy_price"))
    actual_roi = _to_float(row.get("actual_7d_roi"))
    flat_roi = actual_roi if actual_roi is not None else (
        (buy_price - cost) / cost if buy_price is not None and cost and cost > 0 else None
    )
    status = row.get("backtest_status") or "-"
    target = short_time(row.get("target_check_at"))
    return {
        "name_cn": row.get("name"),
        "market_hash_name": row.get("market_hash_name"),
        "display_category": "BACKTEST_STRONG",
        "candidate_category": row.get("candidate_category"),
        "candidate_subcategory": row.get("candidate_subcategory"),
        "recommendation_grade": row.get("recommendation_grade"),
        "recommendation_score": row.get("recommendation_score"),
        "risk_level": "回测",
        "risk_score": None,
        "min_price": row.get("skinport_min_price"),
        "yyyp_buy_price": row.get("yyyp_buy_price"),
        "yyyp_sell_price": row.get("yyyp_sell_price"),
        "yyyp_sell_num": None,
        "flat_roi": flat_roi,
        "risk_reasons": f"来自7天回测库强推荐样本；状态 {status}；目标回测 {target}",
        "recommendation_reasons": "",
    }


def filter_backtest_strong_recommendations(grade: str, q: str, limit: int = 20) -> pd.DataFrame:
    where = ["(recommendation_score > 60 OR recommendation_grade IN ('A', 'B'))"]
    params: List[Any] = []
    if grade and grade != "ALL":
        where.append("recommendation_grade = ?")
        params.append(grade)
    if q:
        where.append("(name LIKE ? OR market_hash_name LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    params.append(limit)
    rows = query_all(
        f"""
        SELECT *
        FROM recommendation_backtest
        WHERE {" AND ".join(where)}
        ORDER BY recommendation_score DESC, collected_at DESC
        LIMIT ?
        """,
        tuple(params),
    )
    return pd.DataFrame([_backtest_record_to_recommendation(row) for row in rows])


def build_highlight_recommendations(df: pd.DataFrame, grade: str, q: str) -> pd.DataFrame:
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


def latest_report_path() -> Optional[str]:
    return pool_latest_report_path()


@lru_cache(maxsize=16)
def _load_report_sheet(path: str, mtime_ns: int, sheet_name: str) -> pd.DataFrame:
    del mtime_ns  # cache key only
    return pd.read_excel(path, sheet_name=sheet_name)


def load_report_sheet(sheet_name: str) -> pd.DataFrame:
    return pool_load_report_sheet(sheet_name)


def backtest_exists() -> bool:
    return os.path.exists(RECOMMENDATION_BACKTEST_DB)


def query_one(sql: str, params: Tuple[Any, ...] = ()) -> Any:
    if not backtest_exists():
        return None
    with closing(sqlite3.connect(RECOMMENDATION_BACKTEST_DB)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    if not backtest_exists():
        return []
    with closing(sqlite3.connect(RECOMMENDATION_BACKTEST_DB)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]


def build_summary() -> Dict[str, Any]:
    all_items = load_report_sheet("all_items")
    report_path = latest_report_path()
    summary = {
        "report_path": report_path or "-",
        "report_name": os.path.basename(report_path) if report_path else "-",
        "all_items": int(len(all_items)),
        "stable_count": 0,
        "high_count": 0,
        "scored_count": 0,
        "pending_count": 0,
        "resolved_count": 0,
        "latest_collected_at": "-",
        "avg_actual_roi": None,
    }

    if not all_items.empty and "candidate_category" in all_items.columns:
        counts = all_items["candidate_category"].fillna("UNKNOWN").value_counts()
        summary["stable_count"] = int(counts.get("STABLE_ARBITRAGE", 0))
        summary["high_count"] = int(counts.get("HIGH_VOLATILITY", 0))
    if not all_items.empty and "recommendation_stage" in all_items.columns:
        summary["scored_count"] = int((all_items["recommendation_stage"] == "SCORED").sum())

    status_rows = query_all(
        "SELECT backtest_status, COUNT(*) AS count FROM recommendation_backtest GROUP BY backtest_status"
    )
    for row in status_rows:
        if row["backtest_status"] == "PENDING":
            summary["pending_count"] = int(row["count"])
        elif row["backtest_status"] == "RESOLVED":
            summary["resolved_count"] = int(row["count"])

    latest = query_one("SELECT MAX(collected_at) AS collected_at FROM recommendation_backtest")
    if latest and latest.get("collected_at"):
        summary["latest_collected_at"] = latest["collected_at"]

    avg_roi = query_one(
        "SELECT AVG(actual_7d_roi) AS avg_roi FROM recommendation_backtest WHERE backtest_status = 'RESOLVED'"
    )
    if avg_roi:
        summary["avg_actual_roi"] = avg_roi.get("avg_roi")
    return summary


def filter_recommendations(
    category: str,
    grade: str,
    q: str,
    limit: int = PAGE_SIZE,
) -> List[Dict[str, Any]]:
    return pool_filter_recommendations(category, grade, q, limit)


def filter_backtests(status: str, grade: str, q: str, limit: int = PAGE_SIZE) -> List[Dict[str, Any]]:
    where = []
    params: List[Any] = []
    if status and status != "ALL":
        where.append("backtest_status = ?")
        params.append(status)
    if grade and grade != "ALL":
        where.append("recommendation_grade = ?")
        params.append(grade)
    if q:
        where.append("(name LIKE ? OR market_hash_name LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    params.append(limit)
    return query_all(
        f"""
        SELECT *
        FROM recommendation_backtest
        {where_sql}
        ORDER BY
            CASE backtest_status WHEN 'PENDING' THEN 0 ELSE 1 END,
            recommendation_score DESC,
            collected_at DESC
        LIMIT ?
        """,
        tuple(params),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "summary": build_summary(),
        "recommendations": filter_recommendations("HIGHLIGHTS", "ALL", ""),
        "backtests": filter_backtests("PENDING", "ALL", ""),
        "category": "HIGHLIGHTS",
        "grade": "ALL",
        "status": "PENDING",
        "q": "",
    }
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/partials/recommendations", response_class=HTMLResponse)
def recommendations_partial(
    request: Request,
    category: str = Query("HIGHLIGHTS"),
    grade: str = Query("ALL"),
    q: str = Query(""),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/recommendations_table.html",
        {
            "recommendations": filter_recommendations(category, grade, q),
        },
    )


@app.get("/partials/backtests", response_class=HTMLResponse)
def backtests_partial(
    request: Request,
    status: str = Query("PENDING"),
    grade: str = Query("ALL"),
    q: str = Query(""),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/backtests_table.html",
        {
            "backtests": filter_backtests(status, grade, q),
        },
    )


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"ok": True, "report": latest_report_path(), "backtest_db": RECOMMENDATION_BACKTEST_DB})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CS_APP_PORT", "8000"))
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
