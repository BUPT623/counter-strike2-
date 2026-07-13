"""Shared project paths for scripts run from the CS-APP workspace."""
import os


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")
CONFIG_DIR = os.path.join(ROOT_DIR, "config")
LOG_DIR = os.path.join(ROOT_DIR, "logs")

CSQAQ_TOKEN_FILE = os.path.join(ROOT_DIR, ".csqaq_token")
TRANSLATION_CACHE_FILE = os.path.join(DATA_DIR, "_translation_cache.json")
RISK_CONFIG_JSON = os.path.join(CONFIG_DIR, "risk_config.json")
AUTOMATION_CONFIG_JSON = os.path.join(CONFIG_DIR, "automation_config.json")
LARGE_DEAL_CONFIG_JSON = os.path.join(CONFIG_DIR, "large_deal_config.json")
SKINPORT_RAW_JSON = os.path.join(DATA_DIR, "skinport_raw.json")
SKINPORT_RAW_XLSX = os.path.join(DATA_DIR, "skinport_raw.xlsx")
DOMESTIC_RAW_JSON = os.path.join(DATA_DIR, "domestic_raw.json")
DOMESTIC_RAW_XLSX = os.path.join(DATA_DIR, "domestic_raw.xlsx")
LARGE_SKINPORT_RAW_JSON = os.path.join(DATA_DIR, "large_skinport_raw.json")
LARGE_DOMESTIC_RAW_JSON = os.path.join(DATA_DIR, "large_domestic_raw.json")
ICBC_HKD_RATE_JSON = os.path.join(DATA_DIR, "icbc_hkd_rate.json")
PROFIT_REPORT_XLSX = os.path.join(OUTPUT_DIR, "profit_report.xlsx")
PROFIT_REPORT_JSON = os.path.join(OUTPUT_DIR, "profit_report.json")
LARGE_DEAL_REPORT_JSON = os.path.join(OUTPUT_DIR, "large_deal_report.json")
MARKET_SNAPSHOT_DB = os.path.join(DATA_DIR, "market_snapshots.sqlite3")
RECOMMENDATION_BACKTEST_DB = os.path.join(DATA_DIR, "recommendation_backtest.sqlite3")
NOTIFICATION_STATE_DB = os.path.join(DATA_DIR, "notification_state.sqlite3")
LARGE_NOTIFICATION_STATE_DB = os.path.join(DATA_DIR, "large_notification_state.sqlite3")
