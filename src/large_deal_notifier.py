"""Bark push helper for large-ticket recommendations."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from notifier import (
    PushNotifier,
    digest_in_cooldown,
    filter_sendable_items,
    load_automation_config,
    mark_digest_sent,
)
from project_paths import LARGE_DEAL_REPORT_JSON, LARGE_NOTIFICATION_STATE_DB
from recommendation_pool import star_label, star_text, to_float

JsonDict = Dict[str, Any]


def load_large_recommendations(path: str = LARGE_DEAL_REPORT_JSON) -> List[JsonDict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    return list(payload.get("recommendations") or [])[:5]


def _fmt_money(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number:,.2f}"


def _fmt_pct(value: Any) -> str:
    number = to_float(value)
    return "-" if number is None else f"{number * 100:.2f}%"


def _display_items(items: List[JsonDict]) -> List[JsonDict]:
    display: List[JsonDict] = []
    for item in items[:5]:
        copied = dict(item)
        copied["display_category_cn"] = "大额捡漏"
        copied["display_category"] = "LARGE_DEAL"
        copied["risk_level"] = "未做风险模型"
        copied["flat_roi"] = copied.get("best_profit_rate")
        display.append(copied)
    return display


def build_large_digest(items: List[JsonDict]) -> tuple[str, str]:
    title = f"CS-APP 大额捡漏 Top {len(items)}"
    lines = [
        "大额模式：预期出货价=UU售价，仅按捡漏利润率和利润额排序；未做7天风险模型，不进入历史回测。",
        "",
    ]
    for index, item in enumerate(items, start=1):
        name = item.get("name_cn") or item.get("name") or item.get("market_hash_name") or "-"
        score = item.get("recommendation_score")
        lines.extend([
            f"{index}. {name}",
            f"   推荐 {star_text(score)}({star_label(score)}) | 等级 {item.get('recommendation_grade') or '-'} | 模式 {item.get('recommended_exit_mode') or '-'}",
            f"   捡漏利润 {_fmt_money(item.get('deal_profit_rmb'))} RMB / {_fmt_pct(item.get('deal_profit_rate'))}",
            f"   预期出货价(UU售价) {_fmt_money(item.get('deal_expected_exit_price'))}",
            f"   成本 {_fmt_money(item.get('skinport_cost_cny') or item.get('cost_rmb'))} RMB | Skinport {_fmt_money(item.get('min_price'))} HKD",
            f"   UU求购 {_fmt_money(item.get('yyyp_buy_price'))} | UU售价 {_fmt_money(item.get('yyyp_sell_price'))} | 在售 {int(to_float(item.get('yyyp_sell_num')) or 0)}",
        ])
    return title, "\n".join(lines)


def send_large_deal_digest(
    automation_config: JsonDict,
    logger: Any,
    dry_run: bool = False,
    db_path: str = LARGE_NOTIFICATION_STATE_DB,
) -> JsonDict:
    raw_items = _display_items(load_large_recommendations())
    if not raw_items:
        return {"status": "skipped_empty", "sent_items": 0}

    notification_config = dict(automation_config["notifications"])
    notification_config["max_items_per_digest"] = 5
    selected, suppressed = filter_sendable_items(raw_items, notification_config, db_path=db_path)
    if not selected:
        return {"status": "skipped_suppressed", "sent_items": 0, **suppressed}

    if digest_in_cooldown(selected, notification_config, db_path=db_path):
        return {"status": "skipped_digest_cooldown", "sent_items": 0, **suppressed}

    title, body = build_large_digest(selected)
    if dry_run:
        logger.info("[DRY-RUN] Would push large-ticket digest: %s\n%s", title, body)
        return {"status": "dry_run", "sent_items": len(selected), **suppressed}

    notifier = PushNotifier.from_config(notification_config)
    ready, reason = notifier.is_ready()
    if not ready:
        logger.info("Large-ticket push skipped: %s", reason)
        return {"status": "skipped_not_configured", "sent_items": 0, "reason": reason, **suppressed}

    notifier.send(title, body, click_url=str(notification_config.get("dashboard_url") or ""))
    mark_digest_sent(selected, db_path=db_path)
    return {"status": "sent", "sent_items": len(selected), **suppressed}


def main() -> None:
    from logging_utils import setup_logging

    logger = setup_logging("large_deal_notifier", "large_deal_notifier")
    config = load_automation_config()
    logger.info("Large-ticket push stats: %s", send_large_deal_digest(config, logger))


if __name__ == "__main__":
    main()
