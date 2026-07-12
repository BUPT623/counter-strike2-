"""Shared project paths for scripts run from the CS-APP workspace."""
import os


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")

CSQAQ_TOKEN_FILE = os.path.join(ROOT_DIR, ".csqaq_token")
TRANSLATION_CACHE_FILE = os.path.join(DATA_DIR, "_translation_cache.json")
SKINPORT_RAW_JSON = os.path.join(DATA_DIR, "skinport_raw.json")
SKINPORT_RAW_XLSX = os.path.join(DATA_DIR, "skinport_raw.xlsx")
DOMESTIC_RAW_JSON = os.path.join(DATA_DIR, "domestic_raw.json")
DOMESTIC_RAW_XLSX = os.path.join(DATA_DIR, "domestic_raw.xlsx")
ICBC_HKD_RATE_JSON = os.path.join(DATA_DIR, "icbc_hkd_rate.json")
PROFIT_REPORT_XLSX = os.path.join(OUTPUT_DIR, "profit_report.xlsx")
