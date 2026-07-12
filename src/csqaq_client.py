"""
Sprint 2 -- 悠悠有品 (UU) 国内价格抓取
======================================
Fetches CS2 item prices from CSQAQ API, keeps only 悠悠有品 data,
resolves market_hash_name (cache → detail API fallback), saves cleaned data.

Rate limit: randomized 3-7 second delays per project anti-bot rules.
"""
import json
import logging
import os
import re
import sys
import time
import random
from typing import Any, Dict, List, Optional, Tuple

import requests

from filters import MAX_UU_PRICE_RMB
from logging_utils import setup_logging
from normalization import normalize_rate, parse_float, parse_int
from project_paths import (
    CSQAQ_TOKEN_FILE,
    DATA_DIR,
    DOMESTIC_RAW_JSON,
    TRANSLATION_CACHE_FILE,
)

JsonDict = Dict[str, Any]
MarketHashCache = Dict[str, str]

# ---- constants ----
API_BASE = "https://api.csqaq.com/api/v1"
TOKEN_FILE = CSQAQ_TOKEN_FILE
OUTPUT_FILE = DOMESTIC_RAW_JSON
TRANSLATION_CACHE = TRANSLATION_CACHE_FILE
HEADERS = {"ApiToken": "", "Content-Type": "application/json"}
PAGE_SIZE = 100
MIN_PRICE_RMB = 2.0
MAX_PRICE_RMB = MAX_UU_PRICE_RMB
MAX_RETRIES = 3
REQUEST_DELAY_MIN = 3.0
REQUEST_DELAY_MAX = 7.0
CATEGORY_FILTER = ["普通"]

LOGGER = logging.getLogger("csqaq_client")

TRACKED_RAW_FIELDS = [
    "yyyp_buy_price", "yyyp_sell_price",
    "sell_price_1", "sell_price_7", "sell_price_15", "sell_price_30",
    "sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30",
    "yyyp_buy_num", "yyyp_sell_num",
    "buff_price_chg", "buff_buy_price", "buff_sell_price",
    "steam_buy_price", "steam_sell_price",
]
PRICE_FIELDS = {
    "yyyp_buy_price", "yyyp_sell_price",
    "sell_price_1", "sell_price_7", "sell_price_15", "sell_price_30",
    "buff_buy_price", "buff_sell_price", "steam_buy_price", "steam_sell_price",
}
RATE_FIELDS = {
    "sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30",
    "buff_price_chg",
}
COUNT_FIELDS = {"yyyp_buy_num", "yyyp_sell_num"}

WEAR_CN_TO_EN = {
    "崭新出厂": "Factory New", "略有磨损": "Minimal Wear",
    "久经沙场": "Field-Tested", "破损不堪": "Well-Worn",
    "战痕累累": "Battle-Scarred",
}


def load_token() -> str:
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_cache() -> MarketHashCache:
    if not os.path.exists(TRANSLATION_CACHE):
        return {}
    with open(TRANSLATION_CACHE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_previous_market_hash_cache() -> Tuple[Dict[int, str], MarketHashCache]:
    """Reuse the last cleaned output so reruns avoid unnecessary detail API calls."""
    by_id: Dict[int, str] = {}
    by_name: MarketHashCache = {}
    if not os.path.exists(OUTPUT_FILE):
        return by_id, by_name

    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            previous = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not load previous output cache: %s", exc)
        return by_id, by_name

    for item in previous:
        mhn = item.get("market_hash_name")
        if not mhn:
            continue
        csqaq_id = item.get("csqaq_id")
        name = item.get("name")
        if isinstance(csqaq_id, int):
            by_id[csqaq_id] = mhn
        if name:
            by_name[name] = mhn
    return by_id, by_name


def bind_local_ip() -> None:
    """Refresh CSQAQ token IP binding for dynamic-network environments."""
    resp = requests.post(f"{API_BASE}/sys/bind_local_ip", headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise ValueError(f"IP bind failed: code={data.get('code')}, msg={data.get('msg')}")
    LOGGER.info("CSQAQ IP binding refreshed.")


def fetch_page(page: int) -> JsonDict:
    payload = {
        "page_index": page, "page_size": PAGE_SIZE,
        "filter": {"价格最低价": MIN_PRICE_RMB, "在售最少": 1, "类别": CATEGORY_FILTER},
        "show_recently_price": True,
    }
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(f"{API_BASE}/info/get_rank_list", json=payload,
                                 headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                raise ValueError(f"API error: code={data.get('code')}")
            return data["data"]
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise last_err  # type: ignore[misc]


def fetch_all_items() -> List[JsonDict]:
    all_items: List[JsonDict] = []
    page = 1
    while True:
        LOGGER.info("Fetching page %s ...", page)
        data = fetch_page(page)
        items = data["data"]
        all_items.extend(items)
        LOGGER.info("Fetched %s items (total: %s)", len(items), len(all_items))
        if len(items) < PAGE_SIZE:
            break
        page += 1
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
    return all_items


def fetch_all_items_with_auth_recovery() -> List[JsonDict]:
    """Fetch all items, refreshing CSQAQ IP binding once if token auth returns 401."""
    try:
        return fetch_all_items()
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 401:
            LOGGER.warning("Authorization failed (401). Refreshing CSQAQ IP binding ...")
            bind_local_ip()
            LOGGER.info("Retrying after IP binding refresh ...")
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
            return fetch_all_items()
        raise


def resolve_market_hash_name(name_cn: str, cache: MarketHashCache) -> Optional[str]:
    """Try cache first; returns None if detail API needed."""
    wear_cn = None
    base_name = name_cn
    m = re.search(r"\((.+?)\)$", name_cn)
    if m and m.group(1) in WEAR_CN_TO_EN:
        wear_cn = m.group(1)
        base_name = name_cn[:m.start()].strip()
    en_base = cache.get(base_name)
    if en_base is None:
        return None
    return f"{en_base} ({WEAR_CN_TO_EN[wear_cn]})" if wear_cn else en_base


def fetch_market_hash_name_from_api(csqaq_id: int) -> Optional[str]:
    """Detail API fallback — one GET per item."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(f"{API_BASE}/info/good?id={csqaq_id}",
                                headers=HEADERS, timeout=15)
            if resp.status_code == 429:
                time.sleep(5 * attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                return None
            return data["data"]["goods_info"].get("market_hash_name")
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None


def normalize_csqaq_fields(item: JsonDict) -> Tuple[JsonDict, List[str]]:
    """Normalize tracked CSQAQ fields while keeping raw values traceable."""
    entry: JsonDict = {}
    issues: List[str] = []
    max_abs_rate = 3.0

    for field in TRACKED_RAW_FIELDS:
        raw_value = item.get(field)
        entry[f"raw_{field}"] = raw_value
        if field in PRICE_FIELDS:
            entry[field] = parse_float(raw_value, field, issues)
        elif field in RATE_FIELDS:
            entry[field] = normalize_rate(raw_value, max_abs_rate, field, issues)
        elif field in COUNT_FIELDS:
            parsed = parse_int(raw_value, field, issues)
            entry[field] = parsed if parsed is not None else 0
        else:
            entry[field] = raw_value

    # Backward-compatible aliases used by the existing analyzer and reports.
    entry["uu_sell_price"] = entry.get("yyyp_sell_price")
    entry["uu_buy_price"] = entry.get("yyyp_buy_price")
    entry["uu_sell_num"] = entry.get("yyyp_sell_num", 0)
    entry["uu_buy_num"] = entry.get("yyyp_buy_num", 0)
    if issues:
        entry["field_warnings"] = issues
    return entry, issues


def clean_and_resolve(raw: List[JsonDict], cache: MarketHashCache) -> List[JsonDict]:
    """Filter by price + resolve market_hash_name (cache → API fallback)."""
    result: List[JsonDict] = []
    need_api: List[JsonDict] = []  # items needing detail API call
    stat_no_price = stat_cheap = stat_too_high = 0
    from_cache = 0
    from_previous_output = 0
    stat_star = 0
    stat_buy_gt_sell = 0
    previous_by_id, previous_by_name = load_previous_market_hash_cache()

    for item in raw:
        name_cn = item["name"]
        if "★" in name_cn:
            stat_star += 1
            continue

        normalized_fields, field_issues = normalize_csqaq_fields(item)
        if field_issues:
            LOGGER.debug("Field conversion issues for %s: %s", name_cn, field_issues)

        uu_price = normalized_fields.get("yyyp_sell_price")
        if uu_price is None:
            stat_no_price += 1
            continue
        if uu_price < MIN_PRICE_RMB:
            stat_cheap += 1
            continue
        if uu_price > MAX_PRICE_RMB:
            stat_too_high += 1
            continue

        uu_buy_price = normalized_fields.get("yyyp_buy_price")
        if uu_buy_price is not None and uu_buy_price > uu_price:
            stat_buy_gt_sell += 1
            continue

        mhn = resolve_market_hash_name(name_cn, cache)
        if mhn:
            from_cache += 1
        else:
            mhn = previous_by_id.get(item["id"]) or previous_by_name.get(name_cn)
            if mhn:
                from_previous_output += 1
        if mhn and "★" in mhn:
            stat_star += 1
            continue

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

    LOGGER.info("Price-filter kept: %s", len(result) + len(need_api))
    LOGGER.info("  no uu price: %s", stat_no_price)
    LOGGER.info("  < %.2f RMB: %s", MIN_PRICE_RMB, stat_cheap)
    LOGGER.info("  > %.2f RMB: %s", MAX_PRICE_RMB, stat_too_high)
    LOGGER.info("  contains star: %s", stat_star)
    LOGGER.info("  yyyp_buy_price > yyyp_sell_price: %s", stat_buy_gt_sell)
    LOGGER.info("  market_hash_name from cache: %s", from_cache)
    LOGGER.info("  market_hash_name from previous output: %s", from_previous_output)
    LOGGER.info("  need detail API: %s", len(need_api))

    # Phase 2: detail API for items without cache hit
    if need_api:
        LOGGER.info("Fetching market_hash_name via detail API for %s items ...", len(need_api))
        api_ok = 0
        for i, entry in enumerate(need_api):
            mhn = fetch_market_hash_name_from_api(entry["csqaq_id"])
            entry["market_hash_name"] = mhn
            if mhn:
                api_ok += 1
            if (i + 1) % 200 == 0:
                LOGGER.info("API progress: %s/%s | resolved: %s", i + 1, len(need_api), api_ok)
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        LOGGER.info("Detail API done: %s/%s resolved", api_ok, len(need_api))
        result.extend(need_api)

    # Drop items still without market_hash_name (can't join later)
    final = [r for r in result if r["market_hash_name"]]
    LOGGER.info("Final with market_hash_name: %s | dropped: %s", len(final), len(result) - len(final))
    return final


def save_json(items: List[JsonDict], path: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    LOGGER.info("Saved %s items to %s", len(items), path)


def main() -> None:
    global LOGGER
    LOGGER = setup_logging("csqaq_client", "csqaq_client")
    LOGGER.info("=== Sprint 2: UU (悠悠有品) Domestic Price Fetcher ===")

    try:
        HEADERS["ApiToken"] = load_token()
    except FileNotFoundError:
        LOGGER.error(".csqaq_token file not found in project root.")
        sys.exit(1)

    try:
        cache = load_cache()
        LOGGER.info("Translation cache: %s pairs loaded", len(cache))

        LOGGER.info("[1/3] Fetching all items from CSQAQ ...")
        raw = fetch_all_items_with_auth_recovery()
        LOGGER.info("Total raw items: %s", len(raw))

        LOGGER.info("[2/3] Cleaning & resolving market_hash_name ...")
        cleaned = clean_and_resolve(raw, cache)

        LOGGER.info("[3/3] Writing output ...")
        save_json(cleaned, OUTPUT_FILE)

        LOGGER.info("[OK] Sprint 2 complete -- %s is ready.", os.path.basename(OUTPUT_FILE))
    except requests.exceptions.Timeout:
        LOGGER.error("Request timed out.")
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        LOGGER.error("Network connection failed.")
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        LOGGER.error("HTTP error -- %s", exc)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as exc:
        LOGGER.error("Parse error -- %s", exc)
        sys.exit(1)
    except Exception as exc:
        LOGGER.error("Unexpected: %s -- %s", type(exc).__name__, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
