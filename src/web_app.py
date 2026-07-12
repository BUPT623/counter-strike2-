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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(APP_DIR, "templates")
STATIC_DIR = os.path.join(APP_DIR, "static")
PAGE_SIZE = 80

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


def short_time(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    text = str(value)
    return text.replace("T", " ").replace("+00:00", " UTC")[:19]


templates.env.filters["money"] = fmt_money
templates.env.filters["pct"] = fmt_pct
templates.env.filters["score"] = fmt_score
templates.env.filters["short_time"] = short_time


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
    return records


def latest_report_path() -> Optional[str]:
    candidates = glob(os.path.join(OUTPUT_DIR, "profit_report*.xlsx"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


@lru_cache(maxsize=16)
def _load_report_sheet(path: str, mtime_ns: int, sheet_name: str) -> pd.DataFrame:
    del mtime_ns  # cache key only
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
    df = load_report_sheet("all_items")
    if df.empty:
        return []
    if category and category != "ALL" and "candidate_category" in df.columns:
        df = df[df["candidate_category"].fillna("") == category]
    if grade and grade != "ALL" and "recommendation_grade" in df.columns:
        df = df[df["recommendation_grade"].fillna("") == grade]
    if q:
        needle = q.lower()
        name_col = df.get("name_cn", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
        hash_col = df.get("market_hash_name", pd.Series("", index=df.index)).fillna("").astype(str).str.lower()
        df = df[name_col.str.contains(needle, regex=False) | hash_col.str.contains(needle, regex=False)]

    sort_cols = [col for col in ["recommendation_score", "stress_roi", "expected_roi"] if col in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols, ascending=[False] * len(sort_cols))
    return _clean_records(df.head(limit))


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
        "recommendations": filter_recommendations("STABLE_ARBITRAGE", "ALL", ""),
        "backtests": filter_backtests("PENDING", "ALL", ""),
        "category": "STABLE_ARBITRAGE",
        "grade": "ALL",
        "status": "PENDING",
        "q": "",
    }
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/partials/recommendations", response_class=HTMLResponse)
def recommendations_partial(
    request: Request,
    category: str = Query("STABLE_ARBITRAGE"),
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
