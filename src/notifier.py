"""Push notification and anti-noise helpers for the automation runner."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from project_paths import AUTOMATION_CONFIG_JSON, DATA_DIR, NOTIFICATION_STATE_DB, ROOT_DIR
from recommendation_pool import star_label, star_text, to_float

JsonDict = Dict[str, Any]

CHINA_TZ = timezone(timedelta(hours=8))
ITEM_TABLE = "notification_item_state"
DIGEST_TABLE = "notification_digest_state"

DEFAULT_AUTOMATION_CONFIG: JsonDict = {
    "scheduler": {
        "interval_minutes": 120,
        "success_jitter_seconds_min": 0,
        "success_jitter_seconds_max": 0,
        "failure_backoff_minutes": 45,
        "active_hours": {"enabled": True, "start": "09:00", "end": "24:00"},
        "between_scripts_delay_seconds_min": 3,
        "between_scripts_delay_seconds_max": 7,
        "run_skinport": True,
        "run_csqaq": True,
        "run_analyzer": True,
        "timeouts_minutes": {"skinport": 10, "csqaq": 120, "analyzer": 20},
    },
    "notifications": {
        "enabled": False,
        "provider": "serverchan",
        "dashboard_url": "http://127.0.0.1:8000/",
        "max_items_per_digest": 8,
        "digest_cooldown_minutes": 60,
        "same_item_cooldown_hours": 12,
        "score_change_threshold": 5,
        "profit_change_threshold": 0.03,
        "notify_on_empty": False,
        "quiet_hours": {
            "enabled": True,
            "start": "00:00",
            "end": "09:00",
            "allow_grade_a": True,
            "allow_profit_rate": 0.15,
        },
        "bark": {"base_url": "https://api.day.app", "key_env": "CS_APP_BARK_KEY"},
        "ntfy": {
            "server_url": "https://ntfy.sh",
            "topic_env": "CS_APP_NTFY_TOPIC",
            "token_env": "CS_APP_NTFY_TOKEN",
        },
        "serverchan": {
            "base_url": "https://sctapi.ftqq.com",
            "sendkey_env": "CS_APP_SERVERCHAN_SENDKEY",
        },
    },
}


def _deep_merge(base: JsonDict, override: JsonDict) -> JsonDict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_local_env(path: str | None = None) -> None:
    """Load a simple KEY=VALUE .env file without adding a dependency."""
    env_path = path or os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _env_bool(name: str) -> Optional[bool]:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_automation_config(path: str = AUTOMATION_CONFIG_JSON) -> JsonDict:
    load_local_env()
    config = deepcopy(DEFAULT_AUTOMATION_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config = _deep_merge(config, json.load(f))

    notify = config["notifications"]
    enabled = _env_bool("CS_APP_NOTIFY_ENABLED")
    if enabled is not None:
        notify["enabled"] = enabled
    provider = os.getenv("CS_APP_PUSH_PROVIDER")
    if provider:
        notify["provider"] = provider.strip().lower()
    dashboard_url = os.getenv("CS_APP_DASHBOARD_URL")
    if dashboard_url:
        notify["dashboard_url"] = dashboard_url.strip()
    return config


def init_state_db(db_path: str = NOTIFICATION_STATE_DB) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ITEM_TABLE} (
                market_hash_name TEXT PRIMARY KEY,
                last_sent_at TEXT NOT NULL,
                last_category TEXT,
                last_grade TEXT,
                last_score REAL,
                last_profit_rate REAL,
                last_risk_level TEXT
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {DIGEST_TABLE} (
                digest_hash TEXT PRIMARY KEY,
                last_sent_at TEXT NOT NULL,
                item_count INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def _now() -> datetime:
    return datetime.now(CHINA_TZ)


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(CHINA_TZ)


def _parse_hhmm(value: str) -> dt_time:
    hour, minute = value.split(":", 1)
    return dt_time(int(hour), int(minute), tzinfo=CHINA_TZ)


def is_quiet_time(config: JsonDict, now: datetime | None = None) -> bool:
    quiet = config.get("quiet_hours", {})
    if not quiet.get("enabled", True):
        return False
    current = (now or _now()).astimezone(CHINA_TZ).timetz()
    start = _parse_hhmm(str(quiet.get("start", "23:00")))
    end = _parse_hhmm(str(quiet.get("end", "08:00")))
    if start <= end:
        return start <= current < end
    return current >= start or current < end


def _grade_rank(value: Any) -> int:
    return {"A": 4, "B": 3, "C": 2, "D": 1}.get(str(value or "").upper(), 0)


def _item_key(item: JsonDict) -> str:
    return str(item.get("market_hash_name") or item.get("name_cn") or item.get("name") or "").strip()


def _profit_rate(item: JsonDict) -> Optional[float]:
    return to_float(item.get("flat_roi") if item.get("flat_roi") is not None else item.get("instant_profit_rate"))


def _is_high_priority(item: JsonDict, notification_config: JsonDict) -> bool:
    quiet = notification_config.get("quiet_hours", {})
    if quiet.get("allow_grade_a", True) and str(item.get("recommendation_grade") or "").upper() == "A":
        return True
    threshold = to_float(quiet.get("allow_profit_rate"))
    profit = _profit_rate(item)
    return threshold is not None and profit is not None and profit >= threshold


def _material_change(previous: JsonDict, item: JsonDict, notification_config: JsonDict) -> bool:
    if str(previous.get("last_category") or "") != str(item.get("display_category_cn") or item.get("display_category") or ""):
        return True
    if _grade_rank(item.get("recommendation_grade")) > _grade_rank(previous.get("last_grade")):
        return True

    score = to_float(item.get("recommendation_score"))
    previous_score = to_float(previous.get("last_score"))
    score_threshold = float(notification_config.get("score_change_threshold", 5))
    if score is not None and previous_score is not None and abs(score - previous_score) >= score_threshold:
        return True

    profit = _profit_rate(item)
    previous_profit = to_float(previous.get("last_profit_rate"))
    profit_threshold = float(notification_config.get("profit_change_threshold", 0.03))
    if profit is not None and previous_profit is not None and abs(profit - previous_profit) >= profit_threshold:
        return True
    return False


def _load_previous_item(conn: sqlite3.Connection, key: str) -> Optional[JsonDict]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(f"SELECT * FROM {ITEM_TABLE} WHERE market_hash_name = ?", (key,)).fetchone()
    return dict(row) if row else None


def _sort_for_digest(items: Iterable[JsonDict]) -> List[JsonDict]:
    return sorted(
        items,
        key=lambda item: (
            _grade_rank(item.get("recommendation_grade")),
            to_float(item.get("recommendation_score")) or 0.0,
            _profit_rate(item) or 0.0,
        ),
        reverse=True,
    )


def filter_sendable_items(
    items: List[JsonDict],
    notification_config: JsonDict,
    db_path: str = NOTIFICATION_STATE_DB,
    now: datetime | None = None,
) -> Tuple[List[JsonDict], JsonDict]:
    init_state_db(db_path)
    current_time = now or _now()
    quiet = is_quiet_time(notification_config, current_time)
    max_items = int(notification_config.get("max_items_per_digest", 8))
    same_item_cooldown_hours = float(notification_config.get("same_item_cooldown_hours", 12))
    selected: List[JsonDict] = []
    stats = {"quiet_suppressed": 0, "cooldown_suppressed": 0, "missing_key": 0, "overflow_suppressed": 0}

    with closing(sqlite3.connect(db_path)) as conn:
        for item in items:
            key = _item_key(item)
            if not key:
                stats["missing_key"] += 1
                continue
            if quiet and not _is_high_priority(item, notification_config):
                stats["quiet_suppressed"] += 1
                continue

            previous = _load_previous_item(conn, key)
            if previous:
                last_sent_at = _parse_iso_datetime(previous.get("last_sent_at"))
                elapsed_hours = (
                    (current_time - last_sent_at).total_seconds() / 3600.0
                    if last_sent_at is not None
                    else same_item_cooldown_hours + 1
                )
                if elapsed_hours < same_item_cooldown_hours and not _material_change(previous, item, notification_config):
                    stats["cooldown_suppressed"] += 1
                    continue
            selected.append(item)

    sorted_items = _sort_for_digest(selected)
    if len(sorted_items) > max_items:
        stats["overflow_suppressed"] = len(sorted_items) - max_items
    return sorted_items[:max_items], stats


def _digest_hash(items: List[JsonDict]) -> str:
    payload = [
        {
            "key": _item_key(item),
            "category": item.get("display_category_cn") or item.get("display_category"),
            "grade": item.get("recommendation_grade"),
            "score": round(to_float(item.get("recommendation_score")) or 0.0, 1),
            "profit": round(_profit_rate(item) or 0.0, 4),
        }
        for item in items
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def digest_in_cooldown(
    items: List[JsonDict],
    notification_config: JsonDict,
    db_path: str = NOTIFICATION_STATE_DB,
    now: datetime | None = None,
) -> bool:
    if not items:
        return False
    init_state_db(db_path)
    cooldown_minutes = float(notification_config.get("digest_cooldown_minutes", 60))
    current_time = now or _now()
    digest_hash = _digest_hash(items)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            f"SELECT last_sent_at FROM {DIGEST_TABLE} WHERE digest_hash = ?",
            (digest_hash,),
        ).fetchone()
    if not row:
        return False
    last_sent_at = _parse_iso_datetime(row["last_sent_at"])
    if last_sent_at is None:
        return False
    return (current_time - last_sent_at).total_seconds() < cooldown_minutes * 60.0


def mark_digest_sent(
    items: List[JsonDict],
    db_path: str = NOTIFICATION_STATE_DB,
    now: datetime | None = None,
) -> None:
    if not items:
        return
    init_state_db(db_path)
    current_time = (now or _now()).isoformat(timespec="seconds")
    digest_hash = _digest_hash(items)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            f"""
            INSERT INTO {DIGEST_TABLE} (digest_hash, last_sent_at, item_count)
            VALUES (?, ?, ?)
            ON CONFLICT(digest_hash) DO UPDATE SET
                last_sent_at = excluded.last_sent_at,
                item_count = excluded.item_count
            """,
            (digest_hash, current_time, len(items)),
        )
        for item in items:
            conn.execute(
                f"""
                INSERT INTO {ITEM_TABLE} (
                    market_hash_name, last_sent_at, last_category, last_grade,
                    last_score, last_profit_rate, last_risk_level
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_hash_name) DO UPDATE SET
                    last_sent_at = excluded.last_sent_at,
                    last_category = excluded.last_category,
                    last_grade = excluded.last_grade,
                    last_score = excluded.last_score,
                    last_profit_rate = excluded.last_profit_rate,
                    last_risk_level = excluded.last_risk_level
                """,
                (
                    _item_key(item),
                    current_time,
                    item.get("display_category_cn") or item.get("display_category"),
                    item.get("recommendation_grade"),
                    to_float(item.get("recommendation_score")),
                    _profit_rate(item),
                    item.get("risk_level"),
                ),
            )
        conn.commit()


def _fmt_money(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number:.2f}"


def _fmt_pct(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def _truncate(text: Any, max_len: int = 80) -> str:
    value = str(text or "").strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "..."


def build_recommendation_digest(
    items: List[JsonDict],
    total_count: int,
    suppressed_stats: JsonDict,
    dashboard_url: str,
) -> Tuple[str, str]:
    suppressed_count = sum(int(value) for value in suppressed_stats.values())
    title = f"CS-APP 推荐池 {len(items)}/{total_count} 条"
    lines = [
        f"本轮推荐池 {total_count} 条，推送 {len(items)} 条，防骚扰过滤 {suppressed_count} 条。",
    ]
    if dashboard_url:
        lines.append(f"前端看板：{dashboard_url}")
    lines.append("")

    for index, item in enumerate(items, start=1):
        name = item.get("name_cn") or item.get("name") or item.get("market_hash_name") or "-"
        category = item.get("display_category_cn") or item.get("display_category") or item.get("candidate_category") or "-"
        grade = item.get("recommendation_grade") or "-"
        score_text = star_text(item.get("recommendation_score"))
        score_hint = star_label(item.get("recommendation_score"))
        profit = _fmt_pct(_profit_rate(item))
        cost = _fmt_money(item.get("skinport_cost_cny") if item.get("skinport_cost_cny") is not None else item.get("cost_rmb"))
        buy_price = _fmt_money(item.get("yyyp_buy_price"))
        sell_price = _fmt_money(item.get("yyyp_sell_price"))
        risk = item.get("risk_level") or "-"
        reason = _truncate(item.get("risk_reasons") or item.get("recommendation_reasons"))
        lines.extend([
            f"{index}. {name}",
            f"   分类 {category} | 等级 {grade} | 推荐 {score_text}({score_hint}) | 利润 {profit} | 风险 {risk}",
            f"   成本 {cost} RMB | UU求购 {buy_price} | UU售价 {sell_price}",
        ])
        if reason:
            lines.append(f"   原因：{reason}")
    return title, "\n".join(lines)


class PushNotifier:
    def __init__(self, notification_config: JsonDict):
        self.config = notification_config
        self.provider = str(notification_config.get("provider") or "serverchan").lower()
        self.enabled = bool(notification_config.get("enabled", False))

    @classmethod
    def from_config(cls, notification_config: JsonDict) -> "PushNotifier":
        return cls(notification_config)

    def _provider_config(self) -> JsonDict:
        return self.config.get(self.provider, {})

    def is_ready(self) -> Tuple[bool, str]:
        if not self.enabled:
            return False, "notifications.disabled"
        provider_config = self._provider_config()
        if self.provider == "bark":
            if self._env_value(provider_config.get("key_env")):
                return True, "ready"
            return False, "missing CS_APP_BARK_KEY"
        if self.provider == "ntfy":
            if self._env_value(provider_config.get("topic_env")):
                return True, "ready"
            return False, "missing CS_APP_NTFY_TOPIC"
        if self.provider == "serverchan":
            if self._env_value(provider_config.get("sendkey_env")):
                return True, "ready"
            return False, "missing CS_APP_SERVERCHAN_SENDKEY"
        return False, f"unknown provider: {self.provider}"

    def _env_value(self, env_name: Any) -> str:
        return os.getenv(str(env_name or "")).strip() if env_name else ""

    def send(self, title: str, body: str, click_url: str = "") -> None:
        ready, reason = self.is_ready()
        if not ready:
            raise RuntimeError(reason)
        if self.provider == "bark":
            self._send_bark(title, body, click_url)
        elif self.provider == "ntfy":
            self._send_ntfy(title, body, click_url)
        elif self.provider == "serverchan":
            self._send_serverchan(title, body)
        else:
            raise RuntimeError(f"unknown provider: {self.provider}")

    def _send_bark(self, title: str, body: str, click_url: str) -> None:
        provider_config = self._provider_config()
        base_url = os.getenv("CS_APP_BARK_BASE_URL") or provider_config.get("base_url", "https://api.day.app")
        key = self._env_value(provider_config.get("key_env"))
        payload = {"title": title, "body": body, "group": "CS-APP", "level": "active"}
        if click_url:
            payload["url"] = click_url
        resp = requests.post(f"{str(base_url).rstrip('/')}/{key}", json=payload, timeout=15)
        resp.raise_for_status()

    def _send_ntfy(self, title: str, body: str, click_url: str) -> None:
        provider_config = self._provider_config()
        server_url = os.getenv("CS_APP_NTFY_SERVER") or provider_config.get("server_url", "https://ntfy.sh")
        topic = self._env_value(provider_config.get("topic_env"))
        token = self._env_value(provider_config.get("token_env"))
        headers = {"Title": title, "Priority": "3"}
        if click_url:
            headers["Click"] = click_url
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(
            f"{str(server_url).rstrip('/')}/{topic}",
            data=body.encode("utf-8"),
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()

    def _send_serverchan(self, title: str, body: str) -> None:
        provider_config = self._provider_config()
        base_url = os.getenv("CS_APP_SERVERCHAN_BASE_URL") or provider_config.get("base_url", "https://sctapi.ftqq.com")
        sendkey = self._env_value(provider_config.get("sendkey_env"))
        resp = requests.post(
            f"{str(base_url).rstrip('/')}/{sendkey}.send",
            data={"title": title, "desp": body},
            timeout=15,
        )
        resp.raise_for_status()


def send_recommendation_digest(
    all_recommendations: List[JsonDict],
    automation_config: JsonDict,
    logger: Any,
    db_path: str = NOTIFICATION_STATE_DB,
    dry_run: bool = False,
) -> JsonDict:
    notification_config = automation_config["notifications"]
    if not all_recommendations and not notification_config.get("notify_on_empty", False):
        return {"status": "skipped_empty", "sent_items": 0}

    selected, suppressed = filter_sendable_items(all_recommendations, notification_config, db_path=db_path)
    if not selected and not notification_config.get("notify_on_empty", False):
        return {"status": "skipped_suppressed", "sent_items": 0, **suppressed}

    if digest_in_cooldown(selected, notification_config, db_path=db_path):
        return {"status": "skipped_digest_cooldown", "sent_items": 0, **suppressed}

    dashboard_url = str(notification_config.get("dashboard_url") or "")
    title, body = build_recommendation_digest(selected, len(all_recommendations), suppressed, dashboard_url)
    if dry_run:
        logger.info("[DRY-RUN] Would push digest: %s\n%s", title, body)
        return {"status": "dry_run", "sent_items": len(selected), **suppressed}

    notifier = PushNotifier.from_config(notification_config)
    ready, reason = notifier.is_ready()
    if not ready:
        logger.info("Push notification skipped: %s", reason)
        return {"status": "skipped_not_configured", "sent_items": 0, "reason": reason, **suppressed}

    notifier.send(title, body, click_url=dashboard_url)
    mark_digest_sent(selected, db_path=db_path)
    return {"status": "sent", "sent_items": len(selected), **suppressed}
