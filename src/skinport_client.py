"""
Sprint 1 -- Skinport CS2 全量饰品价格抓取
===========================================
Fetches all CS2 items from the Skinport public API in HKD,
filters out cheap/blacklisted junk, and saves a cleaned JSON.
Filters:
  - min_price must be between 10 HKD and 2300 HKD
  - excludes "Sealed Graffiti" and "Patch" categories
"""

import json
import os
import sys
from typing import Any, Dict, List

import requests

from filters import MAX_SKINPORT_PRICE_HKD
from normalization import parse_float, parse_int
from project_paths import DATA_DIR, SKINPORT_RAW_JSON

try:
    import brotli
except ImportError:  # pragma: no cover - optional response-decoding helper.
    brotli = None  # type: ignore[assignment]

JsonDict = Dict[str, Any]

# ---- constants ----
API_URL = "https://api.skinport.com/v1/items"
PARAMS = {"app_id": "730", "currency": "HKD"}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate, br",
}
TIMEOUT = 30  # seconds
OUTPUT_FILE = SKINPORT_RAW_JSON


def fetch_items() -> List[JsonDict]:
    """Return raw parsed JSON list from the Skinport API, or [] on failure."""
    resp = requests.get(API_URL, params=PARAMS, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    if not resp.content:
        raise ValueError("API returned empty response (possible rate-limit)")

    raw_bytes = resp.content
    # Some environments (e.g. Windows + urllib3) fail to auto-decompress
    # brotli responses. Detect and decompress manually when needed.
    if raw_bytes[:1] not in (b"[", b"{"):
        encoding = resp.headers.get("Content-Encoding", "").lower()
        if "br" in encoding:
            if brotli is None:
                raise ValueError("API returned Brotli data, but brotli is not installed")
            raw_bytes = brotli.decompress(raw_bytes)

    try:
        data = json.loads(raw_bytes)
    except json.JSONDecodeError:
        preview = raw_bytes[:500] if raw_bytes else b"(empty)"
        raise ValueError(
            f"API returned non-JSON response (HTTP {resp.status_code}).\n"
            f"Content-Type: {resp.headers.get('content-type', 'N/A')}\n"
            f"Body preview: {preview!r}"
        )
    if not isinstance(data, list):
        raise ValueError(f"Unexpected API response type: {type(data).__name__}")
    return data


def clean_items(raw: List[JsonDict]) -> List[JsonDict]:
    """
    Keep only items that pass all filters:
      1. min_price is not None
      2. min_price >= PRICE_FLOOR_HKD  (cheap junk)
      3. market_hash_name does NOT contain blacklisted keywords
    Output keeps Skinport pricing fields when the API provides them so the
    analyzer can estimate buy-side execution risk.
    """
    PRICE_FLOOR_HKD = 10.0
    PRICE_CEIL_HKD = MAX_SKINPORT_PRICE_HKD
    BLACKLIST = ("Sealed Graffiti", "Patch")

    cleaned: List[JsonDict] = []
    stat_no_price = 0
    stat_cheap = 0
    stat_too_high = 0
    stat_blacklisted = 0
    stat_star = 0

    for item in raw:
        price = parse_float(item.get("min_price"))
        if price is None:
            stat_no_price += 1
            continue
        if price < PRICE_FLOOR_HKD:
            stat_cheap += 1
            continue
        if price > PRICE_CEIL_HKD:
            stat_too_high += 1
            continue
        name = item.get("market_hash_name", "")
        if "★" in name:
            stat_star += 1
            continue
        if any(bad in name for bad in BLACKLIST):
            stat_blacklisted += 1
            continue
        cleaned.append({
            "market_hash_name": name,
            "min_price": price,
            "mean_price": parse_float(item.get("mean_price")),
            "median_price": parse_float(item.get("median_price")),
            "max_price": parse_float(item.get("max_price")),
            "quantity": parse_int(item.get("quantity")),
            "currency": item.get("currency"),
            "updated_at": item.get("updated_at"),
            "item_page": item.get("item_page") or item.get("market_page"),
        })

    print(f"Cleaned: {len(cleaned)} items kept.")
    print(f"  - no min_price : {stat_no_price}")
    print(f"  - < {PRICE_FLOOR_HKD} HKD   : {stat_cheap}")
    print(f"  - > {PRICE_CEIL_HKD} HKD  : {stat_too_high}")
    print(f"  - contains ★  : {stat_star}")
    print(f"  - blacklisted  : {stat_blacklisted}")
    return cleaned


def save_json(items: List[JsonDict], path: str) -> None:
    """Write items to path as UTF-8 JSON with indentation."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} items to {path}")


def main() -> None:
    print("=== Sprint 1: Skinport CS2 Price Fetcher ===\n")
    try:
        print(f"[1/3] Fetching from {API_URL} ...")
        raw = fetch_items()
        print(f"      Received {len(raw)} items.")

        print("[2/3] Cleaning data ...")
        cleaned = clean_items(raw)

        print("[3/3] Writing output ...")
        save_json(cleaned, OUTPUT_FILE)

        print(f"\n[OK] Sprint 1 complete -- {os.path.basename(OUTPUT_FILE)} is ready.\n")
    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out. Check your network or try again later.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.ConnectionError:
        print("[ERROR] Network connection failed. Verify your internet access.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as exc:
        print(f"[ERROR] HTTP error --{exc}", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] Failed to parse API response --{exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Unexpected: {type(exc).__name__} --{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
