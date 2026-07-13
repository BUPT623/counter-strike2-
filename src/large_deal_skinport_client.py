"""Large-ticket Skinport fetcher for high-value arbitrage hunting."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

import requests

import skinport_client
from normalization import parse_float, parse_int
from project_paths import DATA_DIR, LARGE_SKINPORT_RAW_JSON

JsonDict = Dict[str, Any]

MIN_PRICE_HKD = 2300.0
MAX_PRICE_HKD = 12000.0
OUTPUT_FILE = LARGE_SKINPORT_RAW_JSON
BLACKLIST = ("Sealed Graffiti", "Patch")


def clean_large_items(raw: List[JsonDict]) -> List[JsonDict]:
    cleaned: List[JsonDict] = []
    stat_no_price = 0
    stat_below = 0
    stat_above = 0
    stat_blacklisted = 0

    for item in raw:
        price = parse_float(item.get("min_price"))
        if price is None:
            stat_no_price += 1
            continue
        if price < MIN_PRICE_HKD:
            stat_below += 1
            continue
        if price > MAX_PRICE_HKD:
            stat_above += 1
            continue

        name = item.get("market_hash_name", "")
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

    print(f"Large Skinport kept: {len(cleaned)} items.")
    print(f"  - no min_price: {stat_no_price}")
    print(f"  - < {MIN_PRICE_HKD:.0f} HKD: {stat_below}")
    print(f"  - > {MAX_PRICE_HKD:.0f} HKD: {stat_above}")
    print(f"  - blacklisted: {stat_blacklisted}")
    return cleaned


def save_json(items: List[JsonDict], path: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(items)} items to {path}")


def main() -> None:
    print("=== Large Ticket: Skinport Price Fetcher ===\n")
    try:
        print(f"[1/3] Fetching from {skinport_client.API_URL} ...")
        raw = skinport_client.fetch_items()
        print(f"      Received {len(raw)} items.")

        print("[2/3] Cleaning high-value range ...")
        cleaned = clean_large_items(raw)

        print("[3/3] Writing output ...")
        save_json(cleaned, OUTPUT_FILE)
        print(f"\n[OK] Large Skinport complete -- {os.path.basename(OUTPUT_FILE)} is ready.\n")
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
        print(f"[ERROR] Failed to parse API response -- {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Unexpected: {type(exc).__name__} -- {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
