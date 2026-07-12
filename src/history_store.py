"""SQLite snapshot storage for future model calibration."""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd

from project_paths import MARKET_SNAPSHOT_DB

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
        with sqlite3.connect(db_path) as conn:
            snapshot.to_sql("market_snapshots", conn, if_exists="append", index=False)
        logger.info("Saved market snapshot: %s rows -> %s", len(snapshot), db_path)
    except Exception as exc:
        logger.error("Failed to save market snapshot: %s", exc)
