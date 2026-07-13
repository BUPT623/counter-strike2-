"""Large-ticket CSQAQ/UU fetcher.

This reuses the existing CSQAQ parser and rate-limit behavior while writing
to a separate large-ticket raw JSON file.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from typing import Any, Dict, List

import requests

import csqaq_client as csqaq
from logging_utils import setup_logging
from project_paths import LARGE_DOMESTIC_RAW_JSON

JsonDict = Dict[str, Any]

MIN_UU_SELL_PRICE_RMB = 2000.0
MAX_UU_SELL_PRICE_RMB = 10000.0
MIN_UU_SELL_NUM = 51
OUTPUT_FILE = LARGE_DOMESTIC_RAW_JSON


def configure_large_mode() -> None:
    csqaq.OUTPUT_FILE = OUTPUT_FILE
    csqaq.MIN_PRICE_RMB = MIN_UU_SELL_PRICE_RMB
    csqaq.MAX_PRICE_RMB = MAX_UU_SELL_PRICE_RMB


def fetch_large_page(page: int) -> JsonDict:
    payload = {
        "page_index": page,
        "page_size": csqaq.PAGE_SIZE,
        "filter": {"价格最低价": MIN_UU_SELL_PRICE_RMB, "在售最少": MIN_UU_SELL_NUM},
        "show_recently_price": True,
    }
    last_err = None
    for attempt in range(1, csqaq.MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{csqaq.API_BASE}/info/get_rank_list",
                json=payload,
                headers=csqaq.HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                raise ValueError(f"API error: code={data.get('code')}")
            return data["data"]
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_err = exc
            if attempt < csqaq.MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise last_err  # type: ignore[misc]


def fetch_all_large_items() -> List[JsonDict]:
    all_items: List[JsonDict] = []
    page = 1
    while True:
        csqaq.LOGGER.info("Fetching large-ticket page %s ...", page)
        data = fetch_large_page(page)
        items = data["data"]
        all_items.extend(items)
        csqaq.LOGGER.info("Fetched %s items (total: %s)", len(items), len(all_items))
        if len(items) < csqaq.PAGE_SIZE:
            break
        page += 1
        time.sleep(random.uniform(csqaq.REQUEST_DELAY_MIN, csqaq.REQUEST_DELAY_MAX))
    return all_items


def fetch_all_large_items_with_auth_recovery() -> List[JsonDict]:
    try:
        return fetch_all_large_items()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            csqaq.LOGGER.warning("Authorization failed (401). Refreshing CSQAQ IP binding ...")
            csqaq.bind_local_ip()
            csqaq.LOGGER.info("Retrying after IP binding refresh ...")
            time.sleep(random.uniform(csqaq.REQUEST_DELAY_MIN, csqaq.REQUEST_DELAY_MAX))
            return fetch_all_large_items()
        raise


def clean_and_resolve_large(raw: List[JsonDict], cache: csqaq.MarketHashCache) -> List[JsonDict]:
    result: List[JsonDict] = []
    need_api: List[JsonDict] = []
    stat_no_price = stat_below = stat_above = stat_low_sell_num = stat_buy_gt_sell = 0
    from_cache = 0
    from_previous_output = 0
    previous_by_id, previous_by_name = csqaq.load_previous_market_hash_cache()

    for item in raw:
        name_cn = item["name"]
        normalized_fields, field_issues = csqaq.normalize_csqaq_fields(item)
        if field_issues:
            csqaq.LOGGER.debug("Field conversion issues for %s: %s", name_cn, field_issues)

        uu_sell_price = normalized_fields.get("yyyp_sell_price")
        if uu_sell_price is None:
            stat_no_price += 1
            continue
        if uu_sell_price < MIN_UU_SELL_PRICE_RMB:
            stat_below += 1
            continue
        if uu_sell_price > MAX_UU_SELL_PRICE_RMB:
            stat_above += 1
            continue

        sell_num = normalized_fields.get("yyyp_sell_num") or 0
        if sell_num < MIN_UU_SELL_NUM:
            stat_low_sell_num += 1
            continue

        uu_buy_price = normalized_fields.get("yyyp_buy_price")
        if uu_buy_price is not None and uu_buy_price > uu_sell_price:
            stat_buy_gt_sell += 1
            continue

        mhn = csqaq.resolve_market_hash_name(name_cn, cache)
        if mhn:
            from_cache += 1
        else:
            mhn = previous_by_id.get(item["id"]) or previous_by_name.get(name_cn)
            if mhn:
                from_previous_output += 1

        entry = {
            "csqaq_id": item["id"],
            "name": name_cn,
            "market_hash_name": mhn,
            **normalized_fields,
        }
        if mhn:
            result.append(entry)
        else:
            need_api.append(entry)

    csqaq.LOGGER.info("Large price-filter kept: %s", len(result) + len(need_api))
    csqaq.LOGGER.info("  no uu price: %s", stat_no_price)
    csqaq.LOGGER.info("  < %.2f RMB: %s", MIN_UU_SELL_PRICE_RMB, stat_below)
    csqaq.LOGGER.info("  > %.2f RMB: %s", MAX_UU_SELL_PRICE_RMB, stat_above)
    csqaq.LOGGER.info("  yyyp_sell_num <= %s: %s", MIN_UU_SELL_NUM - 1, stat_low_sell_num)
    csqaq.LOGGER.info("  yyyp_buy_price > yyyp_sell_price: %s", stat_buy_gt_sell)
    csqaq.LOGGER.info("  market_hash_name from cache: %s", from_cache)
    csqaq.LOGGER.info("  market_hash_name from previous output: %s", from_previous_output)
    csqaq.LOGGER.info("  need detail API: %s", len(need_api))

    if need_api:
        csqaq.LOGGER.info("Fetching market_hash_name via detail API for %s items ...", len(need_api))
        api_ok = 0
        for i, entry in enumerate(need_api):
            mhn = csqaq.fetch_market_hash_name_from_api(entry["csqaq_id"])
            entry["market_hash_name"] = mhn
            if mhn:
                api_ok += 1
            if (i + 1) % 50 == 0:
                csqaq.LOGGER.info("API progress: %s/%s | resolved: %s", i + 1, len(need_api), api_ok)
            time.sleep(random.uniform(csqaq.REQUEST_DELAY_MIN, csqaq.REQUEST_DELAY_MAX))
        csqaq.LOGGER.info("Detail API done: %s/%s resolved", api_ok, len(need_api))
        result.extend(need_api)

    final = [r for r in result if r["market_hash_name"]]
    csqaq.LOGGER.info("Final with market_hash_name: %s | dropped: %s", len(final), len(result) - len(final))
    return final


def apply_large_filters(items: List[JsonDict]) -> List[JsonDict]:
    kept: List[JsonDict] = []
    stat_low_sell_num = 0
    for item in items:
        sell_num = item.get("yyyp_sell_num") or item.get("uu_sell_num") or 0
        try:
            sell_num_value = int(sell_num)
        except (TypeError, ValueError):
            sell_num_value = 0
        if sell_num_value < MIN_UU_SELL_NUM:
            stat_low_sell_num += 1
            continue
        kept.append(item)

    csqaq.LOGGER.info("Large-ticket sell count kept: %s", len(kept))
    csqaq.LOGGER.info("  yyyp_sell_num <= %s: %s", MIN_UU_SELL_NUM - 1, stat_low_sell_num)
    return kept


def main() -> None:
    configure_large_mode()
    csqaq.LOGGER = setup_logging("large_deal_csqaq_client", "large_deal_csqaq_client")
    csqaq.LOGGER.info("=== Large Ticket: CSQAQ Domestic Price Fetcher ===")
    csqaq.LOGGER.info(
        "Filters: %.0f <= yyyp_sell_price <= %.0f RMB, yyyp_sell_num > %s",
        MIN_UU_SELL_PRICE_RMB,
        MAX_UU_SELL_PRICE_RMB,
        MIN_UU_SELL_NUM - 1,
    )

    try:
        csqaq.HEADERS["ApiToken"] = csqaq.load_token()
    except FileNotFoundError:
        csqaq.LOGGER.error(".csqaq_token file not found in project root.")
        sys.exit(1)

    try:
        cache = csqaq.load_cache()
        csqaq.LOGGER.info("Translation cache: %s pairs loaded", len(cache))

        csqaq.LOGGER.info("[1/4] Fetching all high-value items from CSQAQ ...")
        raw = fetch_all_large_items_with_auth_recovery()
        csqaq.LOGGER.info("Total raw items: %s", len(raw))

        csqaq.LOGGER.info("[2/4] Cleaning & resolving market_hash_name ...")
        cleaned = clean_and_resolve_large(raw, cache)

        csqaq.LOGGER.info("[3/4] Applying large-ticket sell-count filter ...")
        filtered = apply_large_filters(cleaned)

        csqaq.LOGGER.info("[4/4] Writing output ...")
        csqaq.save_json(filtered, OUTPUT_FILE)
        csqaq.LOGGER.info("[OK] Large CSQAQ complete -- %s is ready.", os.path.basename(OUTPUT_FILE))
    except requests.exceptions.Timeout:
        csqaq.LOGGER.error("Request timed out.")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        csqaq.LOGGER.error("Network connection failed.")
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        csqaq.LOGGER.error("HTTP error -- %s", exc)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as exc:
        csqaq.LOGGER.error("Parse error -- %s", exc)
        sys.exit(1)
    except Exception as exc:
        csqaq.LOGGER.error("Unexpected: %s -- %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
