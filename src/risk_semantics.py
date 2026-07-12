"""Diagnostics for CSQAQ periodic price fields."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pandas as pd

from normalization import parse_float

PERIODS: Tuple[int, ...] = (1, 7, 15, 30)


def _confidence(match_ratio: float, config: Dict[str, Any]) -> str:
    check = config["field_semantic_check"]
    if match_ratio >= float(check["high_confidence_ratio"]):
        return "HIGH"
    if match_ratio >= float(check["medium_confidence_ratio"]):
        return "MEDIUM"
    return "LOW"


def _stats(errors: List[float], total_samples: int, tolerance: float, config: Dict[str, Any]) -> Dict[str, Any]:
    if not errors:
        return {
            "total_sample_count": total_samples,
            "valid_sample_count": 0,
            "median_abs_error": None,
            "mean_abs_error": None,
            "p90_abs_error": None,
            "match_ratio": 0.0,
            "confidence_level": "LOW",
        }
    series = pd.Series(errors, dtype="float64")
    match_ratio = float((series <= tolerance).mean())
    return {
        "total_sample_count": total_samples,
        "valid_sample_count": int(len(errors)),
        "median_abs_error": float(series.median()),
        "mean_abs_error": float(series.mean()),
        "p90_abs_error": float(series.quantile(0.90)),
        "match_ratio": match_ratio,
        "confidence_level": _confidence(match_ratio, config),
    }


def diagnose_period(df: pd.DataFrame, days: int, config: Dict[str, Any]) -> Dict[str, Any]:
    price_col = "yyyp_sell_price" if "yyyp_sell_price" in df.columns else "uu_sell_price"
    value_col = f"sell_price_{days}"
    rate_col = f"sell_price_rate_{days}"
    total_samples = len(df)
    tolerance = float(config["field_semantic_check"]["rate_match_tolerance"])

    reference_errors: List[float] = []
    delta_errors: List[float] = []

    if price_col not in df.columns or value_col not in df.columns or rate_col not in df.columns:
        result = _stats([], total_samples, tolerance, config)
        result.update({
            "field": value_col,
            "rate_field": rate_col,
            "inferred_meaning": "unknown",
            "tested_hypothesis": "missing_columns",
        })
        return result

    for _, row in df.iterrows():
        current_price = parse_float(row.get(price_col))
        period_value = parse_float(row.get(value_col))
        api_rate = parse_float(row.get(rate_col))
        if current_price is None or period_value is None or api_rate is None or current_price <= 0:
            continue

        # Hypothesis 1: sell_price_N is the historical/reference price.
        if period_value > 0:
            reference_rate = current_price / period_value - 1.0
            reference_errors.append(abs(reference_rate - api_rate))

        # Hypothesis 2: sell_price_N is the absolute price change over the period.
        reference_price_from_delta = current_price - period_value
        if reference_price_from_delta > 0:
            delta_rate = current_price / reference_price_from_delta - 1.0
            delta_errors.append(abs(delta_rate - api_rate))

    reference_stats = _stats(reference_errors, total_samples, tolerance, config)
    delta_stats = _stats(delta_errors, total_samples, tolerance, config)
    ref_score = reference_stats["median_abs_error"] if reference_stats["median_abs_error"] is not None else float("inf")
    delta_score = delta_stats["median_abs_error"] if delta_stats["median_abs_error"] is not None else float("inf")

    if ref_score == float("inf") and delta_score == float("inf"):
        chosen = reference_stats
        inferred = "unknown"
        hypothesis = "no_valid_samples"
    elif delta_score < ref_score:
        chosen = delta_stats
        inferred = "absolute_price_change"
        hypothesis = "current_price / (current_price - sell_price_n) - 1"
    else:
        chosen = reference_stats
        inferred = "historical_or_reference_price"
        hypothesis = "current_price / sell_price_n - 1"

    chosen.update({
        "field": value_col,
        "rate_field": rate_col,
        "inferred_meaning": inferred,
        "tested_hypothesis": hypothesis,
    })
    return chosen


def diagnose_rate_fields(df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    rows = [diagnose_period(df, days, config) for days in PERIODS]
    return pd.DataFrame(rows)
