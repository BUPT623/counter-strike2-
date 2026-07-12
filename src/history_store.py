"""SQLite snapshot storage for future model calibration."""
from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pandas as pd

from normalization import parse_float
from project_paths import MARKET_SNAPSHOT_DB, RECOMMENDATION_BACKTEST_DB

SNAPSHOT_COLUMNS = [
    "item_id", "name", "market_hash_name",
    "skinport_min_price", "skinport_mean_price", "skinport_median_price", "skinport_quantity",
    "yyyp_buy_price", "yyyp_sell_price", "yyyp_buy_num", "yyyp_sell_num",
    "buff_buy_price", "buff_sell_price", "buff_price_chg",
    "steam_buy_price", "steam_sell_price",
    "sell_price_1", "sell_price_7", "sell_price_15", "sell_price_30",
    "sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30",
    "collected_at",
]

BACKTEST_TABLE = "recommendation_backtest"

BACKTEST_COLUMNS = [
    "collected_at",
    "target_check_at",
    "market_hash_name",
    "name",
    "skinport_min_price",
    "skinport_cost_cny",
    "yyyp_buy_price",
    "yyyp_sell_price",
    "buff_buy_price",
    "buff_sell_price",
    "buff_price_chg",
    "recommendation_score",
    "recommendation_grade",
    "candidate_category",
    "candidate_subcategory",
    "yyyp_buy_price_7d",
    "actual_7d_roi",
    "resolved_at",
    "backtest_status",
]


def save_market_snapshot(df: pd.DataFrame, db_path: str = MARKET_SNAPSHOT_DB, logger: logging.Logger | None = None) -> None:
    logger = logger or logging.getLogger(__name__)
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        collected_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        snapshot = pd.DataFrame()
        snapshot["item_id"] = df.get("csqaq_id")
        snapshot["name"] = df.get("name_cn")
        snapshot["market_hash_name"] = df.get("market_hash_name")
        snapshot["skinport_min_price"] = df.get("min_price")
        snapshot["skinport_mean_price"] = df.get("mean_price")
        snapshot["skinport_median_price"] = df.get("median_price")
        snapshot["skinport_quantity"] = df.get("quantity")
        for col in SNAPSHOT_COLUMNS:
            if col in snapshot.columns or col in ("item_id", "name", "market_hash_name", "skinport_min_price", "skinport_mean_price", "skinport_median_price", "skinport_quantity"):
                continue
            if col == "collected_at":
                snapshot[col] = collected_at
            else:
                snapshot[col] = df.get(col)
        snapshot = snapshot[SNAPSHOT_COLUMNS]
        with closing(sqlite3.connect(db_path)) as conn:
            snapshot.to_sql("market_snapshots", conn, if_exists="append", index=False)
            conn.commit()
        logger.info("Saved market snapshot: %s rows -> %s", len(snapshot), db_path)
    except Exception as exc:
        logger.error("Failed to save market snapshot: %s", exc)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _ensure_backtest_schema(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {BACKTEST_TABLE} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at TEXT NOT NULL,
            target_check_at TEXT NOT NULL,
            market_hash_name TEXT NOT NULL,
            name TEXT,
            skinport_min_price REAL,
            skinport_cost_cny REAL,
            yyyp_buy_price REAL,
            yyyp_sell_price REAL,
            buff_buy_price REAL,
            buff_sell_price REAL,
            buff_price_chg REAL,
            recommendation_score REAL,
            recommendation_grade TEXT,
            candidate_category TEXT,
            candidate_subcategory TEXT,
            yyyp_buy_price_7d REAL,
            actual_7d_roi REAL,
            resolved_at TEXT,
            backtest_status TEXT NOT NULL DEFAULT 'PENDING'
        )
    """)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{BACKTEST_TABLE}_market_hash_name "
        f"ON {BACKTEST_TABLE}(market_hash_name)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{BACKTEST_TABLE}_target_check_at "
        f"ON {BACKTEST_TABLE}(target_check_at)"
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{BACKTEST_TABLE}_status "
        f"ON {BACKTEST_TABLE}(backtest_status)"
    )


def _current_yyyp_buy_prices(df: pd.DataFrame) -> Dict[str, float]:
    prices: Dict[str, float] = {}
    if df.empty or "market_hash_name" not in df.columns or "yyyp_buy_price" not in df.columns:
        return prices
    for _, row in df.iterrows():
        market_hash_name = row.get("market_hash_name")
        price = parse_float(row.get("yyyp_buy_price"))
        if market_hash_name and price is not None and price > 0:
            prices[str(market_hash_name)] = price
    return prices


def update_matured_backtests(
    conn: sqlite3.Connection,
    current_prices: Dict[str, float],
    now_utc: datetime,
) -> int:
    now_iso = now_utc.isoformat(timespec="seconds")
    rows = conn.execute(
        f"""
        SELECT id, market_hash_name, skinport_cost_cny
        FROM {BACKTEST_TABLE}
        WHERE backtest_status = 'PENDING'
          AND target_check_at <= ?
          AND yyyp_buy_price_7d IS NULL
        """,
        (now_iso,),
    ).fetchall()

    updated = 0
    for row_id, market_hash_name, skinport_cost_cny in rows:
        future_buy_price = current_prices.get(str(market_hash_name))
        cost = parse_float(skinport_cost_cny)
        if future_buy_price is None or cost is None or cost <= 0:
            continue
        actual_roi = (future_buy_price - cost) / cost
        conn.execute(
            f"""
            UPDATE {BACKTEST_TABLE}
            SET yyyp_buy_price_7d = ?,
                actual_7d_roi = ?,
                resolved_at = ?,
                backtest_status = 'RESOLVED'
            WHERE id = ?
            """,
            (future_buy_price, actual_roi, now_iso, row_id),
        )
        updated += 1
    return updated


def build_backtest_rows(df: pd.DataFrame, collected_at: datetime) -> pd.DataFrame:
    if df.empty or "economic_gate_pass" not in df.columns:
        return pd.DataFrame(columns=BACKTEST_COLUMNS)

    mask = df["economic_gate_pass"].map(_truthy)
    candidates = df[mask].copy()
    if candidates.empty:
        return pd.DataFrame(columns=BACKTEST_COLUMNS)

    collected_iso = collected_at.isoformat(timespec="seconds")
    target_iso = (collected_at + timedelta(days=7)).isoformat(timespec="seconds")
    rows = pd.DataFrame(index=candidates.index)
    rows["collected_at"] = collected_iso
    rows["target_check_at"] = target_iso
    rows["market_hash_name"] = candidates.get("market_hash_name")
    rows["name"] = candidates.get("name_cn")
    rows["skinport_min_price"] = candidates.get("min_price")
    rows["skinport_cost_cny"] = candidates.get("skinport_cost_cny")
    rows["yyyp_buy_price"] = candidates.get("yyyp_buy_price")
    rows["yyyp_sell_price"] = candidates.get("yyyp_sell_price")
    rows["buff_buy_price"] = candidates.get("buff_buy_price")
    rows["buff_sell_price"] = candidates.get("buff_sell_price")
    rows["buff_price_chg"] = candidates.get("buff_price_chg")
    rows["recommendation_score"] = candidates.get("recommendation_score")
    rows["recommendation_grade"] = candidates.get("recommendation_grade")
    rows["candidate_category"] = candidates.get("candidate_category")
    rows["candidate_subcategory"] = candidates.get("candidate_subcategory")
    rows["yyyp_buy_price_7d"] = None
    rows["actual_7d_roi"] = None
    rows["resolved_at"] = None
    rows["backtest_status"] = "PENDING"
    return rows[BACKTEST_COLUMNS]


def _db_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def insert_backtest_rows(conn: sqlite3.Connection, rows: pd.DataFrame) -> None:
    if rows.empty:
        return
    placeholders = ", ".join(["?"] * len(BACKTEST_COLUMNS))
    columns = ", ".join(BACKTEST_COLUMNS)
    values = [
        tuple(_db_value(row[column]) for column in BACKTEST_COLUMNS)
        for _, row in rows.iterrows()
    ]
    conn.executemany(
        f"INSERT INTO {BACKTEST_TABLE} ({columns}) VALUES ({placeholders})",
        values,
    )


def save_recommendation_backtest_snapshots(
    df: pd.DataFrame,
    db_path: str = RECOMMENDATION_BACKTEST_DB,
    logger: logging.Logger | None = None,
) -> Dict[str, int | str]:
    """
    Store only items that passed the economic gate and update matured 7-day rows.

    Realized ROI uses the original Skinport CNY cost and the future YYYP buy
    price: (yyyp_buy_price_7d - skinport_cost_cny) / skinport_cost_cny.
    """
    logger = logger or logging.getLogger(__name__)
    stats: Dict[str, int | str] = {
        "db_path": db_path,
        "inserted": 0,
        "updated_7d": 0,
        "pending_total": 0,
        "resolved_total": 0,
    }
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        now_utc = datetime.now(timezone.utc)
        current_prices = _current_yyyp_buy_prices(df)
        rows = build_backtest_rows(df, now_utc)

        with closing(sqlite3.connect(db_path)) as conn:
            _ensure_backtest_schema(conn)
            updated = update_matured_backtests(conn, current_prices, now_utc)
            insert_backtest_rows(conn, rows)
            pending_total = conn.execute(
                f"SELECT COUNT(*) FROM {BACKTEST_TABLE} WHERE backtest_status = 'PENDING'"
            ).fetchone()[0]
            resolved_total = conn.execute(
                f"SELECT COUNT(*) FROM {BACKTEST_TABLE} WHERE backtest_status = 'RESOLVED'"
            ).fetchone()[0]
            conn.commit()

        stats.update({
            "inserted": int(len(rows)),
            "updated_7d": int(updated),
            "pending_total": int(pending_total),
            "resolved_total": int(resolved_total),
        })
        logger.info(
            "Saved recommendation backtest snapshots: inserted=%s updated_7d=%s pending=%s resolved=%s -> %s",
            stats["inserted"],
            stats["updated_7d"],
            stats["pending_total"],
            stats["resolved_total"],
            db_path,
        )
    except Exception as exc:
        logger.error("Failed to save recommendation backtest snapshots: %s", exc)
        stats["error"] = f"{type(exc).__name__}: {exc}"
    return stats
