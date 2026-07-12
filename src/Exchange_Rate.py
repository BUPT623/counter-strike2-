"""
Realtime HKD -> RMB exchange rate helper.

Fetches ICBC's latest HKD bank sell rate once and exposes only the
`cny_per_hkd` value needed by the analyzer.
"""
import json
import os
import ssl
import sys
import time
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter

from project_paths import DATA_DIR, ICBC_HKD_RATE_JSON


RateInfo = Dict[str, float]

ICBC_RATE_URL = "https://papi.icbc.com.cn/exchanges/ns/getLatest"
REFERER_URL = "https://www.icbc.com.cn/column/1438058341489590354.html"

CACHE_FILE = ICBC_HKD_RATE_JSON
CACHE_MAX_AGE_SECONDS = 6 * 60 * 60

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class LegacyTLSAdapter(HTTPAdapter):
    """Allow ICBC's legacy TLS renegotiation under Python 3.13/OpenSSL 3."""

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED

        legacy_option = getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0)
        if legacy_option:
            context.options |= legacy_option
        context.set_ciphers("DEFAULT@SECLEVEL=1")

        pool_kwargs["ssl_context"] = context
        super().init_poolmanager(
            connections,
            maxsize,
            block=block,
            **pool_kwargs,
        )


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.mount("https://", LegacyTLSAdapter())
    return session


def parse_float(value: Any, field_name: str) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def fetch_icbc_hkd_rate() -> RateInfo:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Referer": REFERER_URL,
        "Origin": "https://www.icbc.com.cn",
    }

    session = build_session()
    resp = session.post(ICBC_RATE_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    payload = resp.json()
    if payload.get("code") != 0:
        raise ValueError(f"ICBC API error: code={payload.get('code')}")

    rows = payload.get("data") or []
    for row in rows:
        if row.get("currencyENName") == "HKD" or row.get("currencyCHName") == "港币":
            sell_rate_per_100 = parse_float(row.get("foreignSell"), "foreignSell")
            if not 50 <= sell_rate_per_100 <= 150:
                raise ValueError(f"HKD sell rate out of range: {sell_rate_per_100}")
            return {"cny_per_hkd": round(sell_rate_per_100 / 100, 6)}

    raise ValueError("HKD row not found in ICBC exchange-rate response")


def save_rate_cache(rate_info: RateInfo) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_file = CACHE_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(rate_info, f, ensure_ascii=False, indent=2)
    os.replace(temp_file, CACHE_FILE)


def load_rate_cache() -> Optional[RateInfo]:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        age_seconds = time.time() - os.path.getmtime(CACHE_FILE)
        if age_seconds > CACHE_MAX_AGE_SECONDS:
            return None

        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        rate = parse_float(data.get("cny_per_hkd"), "cny_per_hkd")
        return {"cny_per_hkd": rate}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def get_hkd_to_cny_rate() -> RateInfo:
    try:
        rate_info = fetch_icbc_hkd_rate()
        save_rate_cache(rate_info)
        print(f"[FX] 1 HKD = {rate_info['cny_per_hkd']:.6f} CNY")
        return rate_info
    except Exception as exc:
        print(
            f"[FX WARNING] Failed to fetch live ICBC HKD rate: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        cached = load_rate_cache()
        if cached is not None:
            print(f"[FX] Using cached rate: 1 HKD = {cached['cny_per_hkd']:.6f} CNY")
            return cached
        raise RuntimeError("No live or cached HKD -> RMB rate is available") from exc


if __name__ == "__main__":
    print(json.dumps(get_hkd_to_cny_rate(), ensure_ascii=False, indent=2))
