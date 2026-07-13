"""Automation runner for the large-ticket arbitrage hunter."""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from typing import Any, Dict, List

from automation_runner import is_in_active_window
from large_deal_notifier import send_large_deal_digest
from logging_utils import setup_logging
from notifier import load_automation_config
from project_paths import LARGE_DEAL_CONFIG_JSON, ROOT_DIR

JsonDict = Dict[str, Any]

SCRIPT_STEPS = [
    ("large_skinport", "large_deal_skinport_client.py"),
    ("large_csqaq", "large_deal_csqaq_client.py"),
    ("large_analyzer", "large_deal_analyzer.py"),
]


def load_large_config(path: str = LARGE_DEAL_CONFIG_JSON) -> JsonDict:
    if not os.path.exists(path):
        return {"scheduler": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _script_path(script_name: str) -> str:
    return os.path.join(ROOT_DIR, "src", script_name)


def _timeout_seconds(config: JsonDict, step_name: str) -> int:
    minutes = (config.get("scheduler") or {}).get("timeouts_minutes", {}).get(step_name.replace("large_", ""))
    return int(float(minutes or 30) * 60)


def _friendly_pause(config: JsonDict, logger: Any) -> None:
    scheduler = config.get("scheduler") or {}
    low = float(scheduler.get("between_scripts_delay_seconds_min", 3))
    high = float(scheduler.get("between_scripts_delay_seconds_max", 7))
    if high < low:
        high = low
    seconds = random.uniform(low, high)
    logger.info("Friendly pause before next large-ticket step: %.1fs", seconds)
    time.sleep(seconds)


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


def run_large_pipeline(config: JsonDict, logger: Any, skip_fetch: bool = False) -> None:
    active_steps: List[tuple[str, str]] = [
        (step_name, script_name)
        for step_name, script_name in SCRIPT_STEPS
        if not skip_fetch or step_name == "large_analyzer"
    ]
    for index, (step_name, script_name) in enumerate(active_steps):
        if index > 0:
            _friendly_pause(config, logger)
        run_script_step(step_name, script_name, config, logger)


def run_once(
    large_config: JsonDict,
    automation_config: JsonDict,
    logger: Any,
    skip_fetch: bool = False,
    no_push: bool = False,
    dry_run: bool = False,
    ignore_window: bool = False,
) -> bool:
    try:
        logger.info("=== Large-ticket automation cycle started ===")
        active_cfg = {"scheduler": {"active_hours": (large_config.get("scheduler") or {}).get("active_hours", {})}}
        if not ignore_window and not is_in_active_window(active_cfg):
            active = active_cfg["scheduler"]["active_hours"]
            logger.info("Outside large-ticket active window %s-%s; skipped.", active.get("start"), active.get("end"))
            return True

        if dry_run:
            logger.info("[DRY-RUN] Skipping large-ticket platform requests and analyzer execution.")
        else:
            run_large_pipeline(large_config, logger, skip_fetch=skip_fetch)

        if no_push:
            logger.info("Large-ticket push skipped by --no-push.")
        else:
            stats = send_large_deal_digest(automation_config, logger, dry_run=dry_run)
            logger.info("Large-ticket push stats: %s", stats)
        logger.info("=== Large-ticket automation cycle finished ===")
        return True
    except subprocess.TimeoutExpired as exc:
        logger.error("Large-ticket step timeout after %ss: %s", exc.timeout, exc.cmd)
    except Exception as exc:
        logger.error("Large-ticket automation failed: %s: %s", type(exc).__name__, exc)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CS-APP large-ticket arbitrage automation.")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument("--skip-fetch", action="store_true", help="skip platform fetch and analyze existing large raw JSON")
    parser.add_argument("--no-push", action="store_true", help="do not send Bark push")
    parser.add_argument("--dry-run", action="store_true", help="do not call platform APIs, analyzer, or push provider")
    parser.add_argument("--ignore-window", action="store_true", help="ignore configured active hours")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = setup_logging("large_deal_runner", "large_deal_runner")
    large_config = load_large_config()
    automation_config = load_automation_config()
    success = run_once(
        large_config,
        automation_config,
        logger,
        skip_fetch=args.skip_fetch,
        no_push=args.no_push,
        dry_run=args.dry_run,
        ignore_window=args.ignore_window,
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
