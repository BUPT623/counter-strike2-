"""Stage 4 automation runner: scheduled fetch, analysis, and push digest."""
from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from logging_utils import setup_logging
from notifier import load_automation_config, send_recommendation_digest
from project_paths import ROOT_DIR
from recommendation_pool import filter_recommendations

JsonDict = Dict[str, Any]
CHINA_TZ = timezone(timedelta(hours=8))

SCRIPT_STEPS = [
    ("skinport", "skinport_client.py"),
    ("csqaq", "csqaq_client.py"),
    ("analyzer", "analyzer.py"),
]


def _script_path(script_name: str) -> str:
    return os.path.join(ROOT_DIR, "src", script_name)


def _timeout_seconds(config: JsonDict, step_name: str) -> int:
    minutes = config.get("scheduler", {}).get("timeouts_minutes", {}).get(step_name)
    return int(float(minutes or 30) * 60)


def _friendly_pause(config: JsonDict, logger: Any) -> None:
    scheduler = config["scheduler"]
    low = float(scheduler.get("between_scripts_delay_seconds_min", 3))
    high = float(scheduler.get("between_scripts_delay_seconds_max", 7))
    if high < low:
        high = low
    seconds = random.uniform(low, high)
    logger.info("Friendly pause before next step: %.1fs", seconds)
    time.sleep(seconds)


def _parse_minutes(value: str) -> int:
    text = str(value).strip()
    hour_text, minute_text = text.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour == 24 and minute == 0:
        return 24 * 60
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid time value: {value}")
    return hour * 60 + minute


def is_in_active_window(config: JsonDict, now: datetime | None = None) -> bool:
    active = config.get("scheduler", {}).get("active_hours", {})
    if not active.get("enabled", False):
        return True
    current = (now or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
    current_minutes = current.hour * 60 + current.minute
    start_minutes = _parse_minutes(str(active.get("start", "09:00")))
    end_minutes = _parse_minutes(str(active.get("end", "24:00")))
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def run_script_step(step_name: str, script_name: str, config: JsonDict, logger: Any) -> None:
    command = [sys.executable, _script_path(script_name)]
    logger.info("Running %s: %s", step_name, " ".join(command))
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        timeout=_timeout_seconds(config, step_name),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{step_name} failed with exit code {result.returncode}")
    logger.info("%s completed.", step_name)


def run_collection_pipeline(config: JsonDict, logger: Any, skip_fetch: bool = False) -> None:
    scheduler = config["scheduler"]
    enabled_steps = {
        "skinport": bool(scheduler.get("run_skinport", True)) and not skip_fetch,
        "csqaq": bool(scheduler.get("run_csqaq", True)) and not skip_fetch,
        "analyzer": bool(scheduler.get("run_analyzer", True)),
    }

    active_steps: List[tuple[str, str]] = [
        (step_name, script_name)
        for step_name, script_name in SCRIPT_STEPS
        if enabled_steps.get(step_name, False)
    ]
    for index, (step_name, script_name) in enumerate(active_steps):
        if index > 0:
            _friendly_pause(config, logger)
        run_script_step(step_name, script_name, config, logger)


def summarize_recommendations(items: List[JsonDict]) -> JsonDict:
    categories: Dict[str, int] = {}
    grades: Dict[str, int] = {}
    for item in items:
        category = str(item.get("display_category_cn") or item.get("display_category") or "-")
        grade = str(item.get("recommendation_grade") or "-")
        categories[category] = categories.get(category, 0) + 1
        grades[grade] = grades.get(grade, 0) + 1
    return {"count": len(items), "categories": categories, "grades": grades}


def run_once(
    config: JsonDict,
    logger: Any,
    skip_fetch: bool = False,
    no_push: bool = False,
    dry_run: bool = False,
    ignore_window: bool = False,
) -> bool:
    try:
        logger.info("=== Stage 4 automation cycle started ===")
        if not ignore_window and not is_in_active_window(config):
            active = config.get("scheduler", {}).get("active_hours", {})
            logger.info("Outside active window %s-%s; skipped.", active.get("start"), active.get("end"))
            return True
        if dry_run:
            logger.info("[DRY-RUN] Skipping platform requests and analyzer execution.")
        else:
            run_collection_pipeline(config, logger, skip_fetch=skip_fetch)

        recommendations = filter_recommendations("HIGHLIGHTS", "ALL", "")
        summary = summarize_recommendations(recommendations)
        logger.info(
            "Recommendation pool: %s items | categories=%s | grades=%s",
            summary["count"],
            summary["categories"],
            summary["grades"],
        )

        if no_push:
            logger.info("Push step skipped by --no-push.")
        else:
            push_stats = send_recommendation_digest(recommendations, config, logger, dry_run=dry_run)
            logger.info("Push stats: %s", push_stats)

        logger.info("=== Stage 4 automation cycle finished ===")
        return True
    except subprocess.TimeoutExpired as exc:
        logger.error("Step timeout after %ss: %s", exc.timeout, exc.cmd)
    except Exception as exc:
        logger.error("Automation cycle failed: %s: %s", type(exc).__name__, exc)
    return False


def _sleep_seconds(config: JsonDict, success: bool) -> float:
    scheduler = config["scheduler"]
    if not success:
        return float(scheduler.get("failure_backoff_minutes", 45)) * 60.0
    base = float(scheduler.get("interval_minutes", 180)) * 60.0
    low = float(scheduler.get("success_jitter_seconds_min", 300))
    high = float(scheduler.get("success_jitter_seconds_max", 900))
    if high < low:
        high = low
    return base + random.uniform(low, high)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CS-APP periodic fetch/analyze/push automation.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one cycle and exit")
    mode.add_argument("--loop", action="store_true", help="run forever; this is also the default")
    parser.add_argument("--config", default=None, help="path to automation_config.json")
    parser.add_argument("--skip-fetch", action="store_true", help="skip Skinport/CSQAQ and analyze existing JSON data")
    parser.add_argument("--no-push", action="store_true", help="do not send mobile push notifications")
    parser.add_argument("--dry-run", action="store_true", help="do not call platform APIs, analyzer, or push provider")
    parser.add_argument("--ignore-window", action="store_true", help="ignore configured active hours")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = setup_logging("automation_runner", "automation_runner")
    config = load_automation_config(args.config) if args.config else load_automation_config()
    run_forever = args.loop or not args.once

    while True:
        success = run_once(
            config,
            logger,
            skip_fetch=args.skip_fetch,
            no_push=args.no_push,
            dry_run=args.dry_run,
            ignore_window=args.ignore_window,
        )
        if not run_forever:
            return 0 if success else 1

        seconds = _sleep_seconds(config, success)
        logger.info("Next cycle in %.1f minutes.", seconds / 60.0)
        try:
            time.sleep(seconds)
        except KeyboardInterrupt:
            logger.info("Automation stopped by user.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
