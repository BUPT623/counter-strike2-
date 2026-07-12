"""
Sprint 2 -- 悠悠有品 (UU) 国内价格抓取
======================================
Fetches CS2 item prices from CSQAQ API, keeps only 悠悠有品 data,
resolves market_hash_name (cache → detail API fallback), saves cleaned data.

Rate limit: randomized 3-7 second delays per project anti-bot rules.
"""
import json
import os
import re
import sys
import time
import random
from typing import Any, Dict, List, Optional, Tuple

import requests

from filters import MAX_UU_PRICE_RMB
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
        print(f"  Warning: could not load previous output cache: {exc}")
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
    print("  CSQAQ IP binding refreshed.")


def fetch_page(page: int) -> JsonDict:
    payload = {
        "page_index": page, "page_size": PAGE_SIZE,
        "filter": {"价格最低价": MIN_PRICE_RMB, "在售最少": 1, "类别": CATEGORY_FILTER},
        "show_recently_price": False,
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
        print(f"  Fetching page {page} ...", end=" ")
        data = fetch_page(page)
        items = data["data"]
        all_items.extend(items)
        print(f"{len(items)} items (total: {len(all_items)})")
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
            print("\n  Authorization failed (401). Refreshing CSQAQ IP binding ...")
            bind_local_ip()
            print("  Retrying after IP binding refresh ...")
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


def parse_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

        uu_price = parse_optional_float(item.get("yyyp_sell_price"))
        if uu_price is None:
            stat_no_price += 1
            continue
        if uu_price < MIN_PRICE_RMB:
            stat_cheap += 1
            continue
        if uu_price > MAX_PRICE_RMB:
            stat_too_high += 1
            continue

        uu_buy_price = parse_optional_float(item.get("yyyp_buy_price"))
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
            "uu_sell_price": uu_price,
            "uu_buy_price": uu_buy_price,
            "uu_sell_num": item.get("yyyp_sell_num", 0),
            "uu_buy_num": item.get("yyyp_buy_num", 0),
        }
        if mhn:
            result.append(entry)
        else:
            need_api.append(entry)

    print(f"  Price-filter kept: {len(result) + len(need_api)}")
    print(f"    - no uu price: {stat_no_price}")
    print(f"    - < {MIN_PRICE_RMB} RMB: {stat_cheap}")
    print(f"    - > {MAX_PRICE_RMB} RMB: {stat_too_high}")
    print(f"    - contains ★: {stat_star}")
    print(f"    - yyyp_buy_price > yyyp_sell_price: {stat_buy_gt_sell}")
    print(f"    - market_hash_name from cache: {from_cache}")
    print(f"    - market_hash_name from previous output: {from_previous_output}")
    print(f"    - need detail API: {len(need_api)}")

    # Phase 2: detail API for items without cache hit
    if need_api:
        print(f"\n  Fetching market_hash_name via detail API for {len(need_api)} items ...")
        api_ok = 0
        for i, entry in enumerate(need_api):
            mhn = fetch_market_hash_name_from_api(entry["csqaq_id"])
            entry["market_hash_name"] = mhn
            if mhn:
                api_ok += 1
            if (i + 1) % 200 == 0:
                print(f"    API progress: {i + 1}/{len(need_api)}  |  resolved: {api_ok}")
            time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        print(f"    API done: {api_ok}/{len(need_api)} resolved")
        result.extend(need_api)

    # Drop items still without market_hash_name (can't join later)
    final = [r for r in result if r["market_hash_name"]]
    print(f"  Final (with market_hash_name): {len(final)}  |  dropped: {len(result) - len(final)}")
    return final


def save_json(items: List[JsonDict], path: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"  Saved {len(items)} items to {path}")


def main() -> None:
    print("=== Sprint 2: UU (悠悠有品) Domestic Price Fetcher ===\n")

    try:
        HEADERS["ApiToken"] = load_token()
    except FileNotFoundError:
        print("[ERROR] .csqaq_token file not found in project root.", file=sys.stderr)
        sys.exit(1)

    try:
        cache = load_cache()
        print(f"  Translation cache: {len(cache)} pairs loaded")

        print("\n[1/3] Fetching all items from CSQAQ ...")
        raw = fetch_all_items_with_auth_recovery()
        print(f"      Total raw items: {len(raw)}")

        print("\n[2/3] Cleaning & resolving market_hash_name ...")
        cleaned = clean_and_resolve(raw, cache)

        print("\n[3/3] Writing output ...")
        save_json(cleaned, OUTPUT_FILE)

        print(f"\n[OK] Sprint 2 complete -- {os.path.basename(OUTPUT_FILE)} is ready.\n")
    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("[ERROR] Network connection failed.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        print(f"[ERROR] HTTP error -- {exc}", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] Parse error -- {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Unexpected: {type(exc).__name__} -- {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
