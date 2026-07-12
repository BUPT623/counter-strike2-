"""Configuration helpers for the CS2 arbitrage risk model."""
from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any, Dict

from project_paths import RISK_CONFIG_JSON

ConfigDict = Dict[str, Any]


DEFAULT_CONFIG: ConfigDict = {
    "risk_model": {
        "normalized_return_floor": -0.50,
        "normalized_return_ceiling": 0.80,
        "max_stress_drop": 0.30,
        "stale_data_seconds": 600,
        "exit_fee_rate": 0.0,
        "max_abs_rate": 3.0,
    },
    "trend_weights": {"day_1": 0.10, "day_7": 0.45, "day_15": 0.30, "day_30": 0.15},
    "stress_model": {
        "spread_penalty_weight": 0.50,
        "imbalance_penalty_weight": 0.03,
        "dispersion_penalty_weight": 0.50,
        "cross_market_penalty_weight": 0.30,
        "short_term_anomaly_penalty_weight": 0.18,
        "short_term_anomaly_threshold": 0.12,
        "max_short_term_anomaly_penalty": 0.08,
    },
    "recommendation_gate": {
        "max_data_quality_risk": 12,
        "min_valid_trend_periods": 2,
        "min_flat_roi": 0.03,
        "min_expected_roi": -0.02,
        "min_stress_roi": -0.08,
        "min_flat_profit": 1.0,
        "min_yyyp_buy_num": 3,
        "max_spread_rate": 0.22,
    },
    "recommendation_scoring": {
        "flat_roi_weight": 20,
        "expected_roi_weight": 20,
        "stress_roi_weight": 20,
        "liquidity_weight": 10,
        "stability_weight": 10,
        "spread_weight": 10,
        "cross_market_weight": 5,
        "profit_weight": 5,
        "risk_penalty_weight": 20,
        "roi_cap": 0.25,
        "profit_cap": 50,
        "buy_num_cap": 50,
        "dispersion_cap": 0.18,
        "spread_cap": 0.22,
        "premium_cap": 0.25,
        "grade_a_min": 75,
        "grade_b_min": 60,
        "grade_c_min": 35,
    },
    "stable_arbitrage": {
        "min_flat_roi": 0.05,
        "min_expected_roi": 0.05,
        "min_stress_roi": 0.0,
        "max_abs_rate_7": 0.12,
        "max_trend_dispersion": 0.10,
        "max_spread_rate": 0.14,
        "min_yyyp_buy_num": 5,
        "max_cross_market_premium": 0.12,
        "max_risk_score": 49,
        "min_recommendation_score": 35,
    },
    "high_volatility": {
        "abnormal_rate_1_to_7": 0.15,
        "abnormal_rate_7": 0.15,
        "acceleration_threshold": 0.10,
        "trend_conflict_threshold": 0.12,
        "max_spread_rate": 0.18,
        "dip_rate_threshold": -0.10,
    },
    "field_semantic_check": {
        "rate_match_tolerance": 0.01,
        "high_confidence_ratio": 0.90,
        "medium_confidence_ratio": 0.70,
    },
    "risk_levels": {"low_max": 29, "medium_max": 49, "high_max": 69},
    "risk_score_weights": {
        "trend": 18,
        "dispersion": 14,
        "spread": 14,
        "liquidity": 12,
        "cross_market": 12,
        "execution": 10,
        "stale_data": 5,
        "data_quality": 15,
    },
    "risk_adjusted_score": {
        "expected_roi_weight": 35,
        "stress_roi_weight": 35,
        "profit_weight": 10,
        "liquidity_weight": 10,
        "risk_score_weight": 20,
        "profit_normalizer": 100,
    },
}


def _deep_merge(base: ConfigDict, override: ConfigDict) -> ConfigDict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def validate_config(config: ConfigDict, logger: logging.Logger | None = None) -> ConfigDict:
    logger = logger or logging.getLogger(__name__)
    weights = config.get("trend_weights", {})
    weight_sum = sum(float(v) for v in weights.values())
    if weight_sum <= 0:
        raise ValueError("trend_weights sum must be positive")
    if abs(weight_sum - 1.0) > 0.001:
        logger.warning("trend_weights sum is %.4f; valid periods will be normalized at runtime", weight_sum)

    risk_weights = config.get("risk_score_weights", {})
    total_risk = sum(float(v) for v in risk_weights.values())
    if total_risk <= 0:
        raise ValueError("risk_score_weights sum must be positive")
    return config


def load_config(path: str = RISK_CONFIG_JSON, logger: logging.Logger | None = None) -> ConfigDict:
    logger = logger or logging.getLogger(__name__)
    config = deepcopy(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            config = _deep_merge(config, json.load(f))
        logger.info("Loaded risk config: %s", path)
    else:
        logger.info("Risk config not found, using defaults: %s", path)
    return validate_config(config, logger)
