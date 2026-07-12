"""Seven-day risk model and candidate classification."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from normalization import normalize_return_to_7d, parse_float, parse_int, safe_divide

PERIODS: Tuple[Tuple[int, str], ...] = ((1, "day_1"), (7, "day_7"), (15, "day_15"), (30, "day_30"))


def weighted_std(values: Iterable[float], weights: Iterable[float]) -> Optional[float]:
    pairs = [(float(v), float(w)) for v, w in zip(values, weights) if v is not None and w > 0]
    if not pairs:
        return None
    total_weight = sum(w for _, w in pairs)
    if total_weight <= 0:
        return None
    mean = sum(v * w for v, w in pairs) / total_weight
    variance = sum(w * (v - mean) ** 2 for v, w in pairs) / total_weight
    return math.sqrt(max(variance, 0.0))


def yyyp_spread_rate(yyyp_buy_price: Any, yyyp_sell_price: Any) -> Optional[float]:
    buy = parse_float(yyyp_buy_price)
    sell = parse_float(yyyp_sell_price)
    if buy is None or sell is None or sell <= 0:
        return None
    return (sell - buy) / sell


def order_imbalance(yyyp_buy_num: Any, yyyp_sell_num: Any) -> Optional[float]:
    buy_num = parse_int(yyyp_buy_num)
    sell_num = parse_int(yyyp_sell_num)
    if buy_num is None or sell_num is None or buy_num < 0 or sell_num < 0:
        return None
    return (buy_num - sell_num) / (buy_num + sell_num + 1.0)


def buy_sell_num_ratio(yyyp_buy_num: Any, yyyp_sell_num: Any) -> Optional[float]:
    buy_num = parse_int(yyyp_buy_num)
    sell_num = parse_int(yyyp_sell_num)
    if buy_num is None or sell_num is None or buy_num < 0 or sell_num < 0:
        return None
    return buy_num / max(sell_num, 1)


def normalize_period_returns(row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    risk_cfg = config["risk_model"]
    floor = float(risk_cfg["normalized_return_floor"])
    ceiling = float(risk_cfg["normalized_return_ceiling"])
    weights = config["trend_weights"]
    returns: Dict[str, Optional[float]] = {}
    valid_values: List[float] = []
    valid_weights: List[float] = []
    used_periods: List[str] = []

    for days, weight_key in PERIODS:
        rate = parse_float(row.get(f"sell_price_rate_{days}"))
        normalized = normalize_return_to_7d(rate, days, floor, ceiling)
        col = f"normalized_rate_{days}_to_7" if days != 7 else "normalized_rate_7_to_7"
        returns[col] = normalized
        if normalized is not None:
            valid_values.append(normalized)
            valid_weights.append(float(weights[weight_key]))
            used_periods.append(str(days))

    if valid_values:
        total_weight = sum(valid_weights)
        expected = sum(v * w for v, w in zip(valid_values, valid_weights)) / total_weight
        dispersion = weighted_std(valid_values, valid_weights)
        trend_range = max(valid_values) - min(valid_values)
        worst = min([0.0] + valid_values)
    else:
        expected = 0.0
        dispersion = None
        trend_range = None
        worst = 0.0

    r1 = returns.get("normalized_rate_1_to_7")
    r7 = returns.get("normalized_rate_7_to_7")
    acceleration = r1 - r7 if r1 is not None and r7 is not None else None

    returns.update({
        "expected_change_7d": expected,
        "trend_dispersion": dispersion,
        "volatility_proxy": dispersion,
        "trend_range": trend_range,
        "worst_historical_change": worst,
        "short_term_acceleration": acceleration,
        "trend_periods_used": ",".join(used_periods),
    })
    return returns


def calculate_cross_market_metrics(row: Dict[str, Any]) -> Dict[str, Optional[float]]:
    yyyp_buy = parse_float(row.get("yyyp_buy_price"))
    yyyp_sell = parse_float(row.get("yyyp_sell_price"))
    buff_buy = parse_float(row.get("buff_buy_price"))
    buff_sell = parse_float(row.get("buff_sell_price"))
    steam_buy = parse_float(row.get("steam_buy_price"))
    steam_sell = parse_float(row.get("steam_sell_price"))

    return {
        "buff_spread_rate": yyyp_spread_rate(buff_buy, buff_sell),
        "yyyp_buff_buy_premium": safe_divide(yyyp_buy - buff_buy, buff_buy) if yyyp_buy is not None and buff_buy is not None else None,
        "yyyp_buff_sell_premium": safe_divide(yyyp_sell - buff_sell, buff_sell) if yyyp_sell is not None and buff_sell is not None else None,
        "steam_buy_premium_reference": safe_divide(yyyp_buy - steam_buy, steam_buy) if yyyp_buy is not None and steam_buy is not None else None,
        "steam_sell_premium_reference": safe_divide(yyyp_sell - steam_sell, steam_sell) if yyyp_sell is not None and steam_sell is not None else None,
    }


def calculate_cross_market_penalty(row: Dict[str, Any], config: Dict[str, Any]) -> float:
    weight = float(config["stress_model"]["cross_market_penalty_weight"])
    buy_premium = parse_float(row.get("yyyp_buff_buy_premium"))
    sell_premium = parse_float(row.get("yyyp_buff_sell_premium"))
    buff_change = parse_float(row.get("buff_price_chg"))
    r7 = parse_float(row.get("normalized_rate_7_to_7"))
    penalty = 0.0

    for premium in (buy_premium, sell_premium):
        if premium is not None and premium > 0.12:
            penalty += premium - 0.12

    # 悠悠上涨但 BUFF 不涨，说明局部溢价回归风险更高。
    if r7 is not None and r7 > 0.08 and buff_change is not None and buff_change <= 0:
        penalty += 0.05
    return max(0.0, penalty) * weight


def stress_change(row: Dict[str, Any], config: Dict[str, Any]) -> float:
    stress_cfg = config["stress_model"]
    max_stress_drop = float(config["risk_model"]["max_stress_drop"])
    worst = parse_float(row.get("worst_historical_change")) or 0.0
    spread = max(parse_float(row.get("yyyp_spread_rate")) or 0.0, 0.0)
    imbalance = parse_float(row.get("order_imbalance"))
    dispersion = max(parse_float(row.get("trend_dispersion")) or 0.0, 0.0)
    imbalance_penalty = float(stress_cfg["imbalance_penalty_weight"]) * max(-(imbalance or 0.0), 0.0)
    change = (
        worst
        - float(stress_cfg["spread_penalty_weight"]) * spread
        - imbalance_penalty
        - float(stress_cfg["dispersion_penalty_weight"]) * dispersion
        - calculate_cross_market_penalty(row, config)
    )
    return max(change, -max_stress_drop)


def _bounded(value: Optional[float], lower: float = 0.0, upper: float = 1.0) -> float:
    if value is None or math.isnan(value):
        return 0.0
    return max(lower, min(upper, value))


def _score(value: Optional[float], threshold: float, weight: float) -> float:
    if value is None or threshold <= 0:
        return 0.0
    return _bounded(value / threshold) * weight


def risk_level(score: float, config: Dict[str, Any]) -> str:
    levels = config["risk_levels"]
    if score <= float(levels["low_max"]):
        return "LOW"
    if score <= float(levels["medium_max"]):
        return "MEDIUM"
    if score <= float(levels["high_max"]):
        return "HIGH"
    return "VERY_HIGH"


def calculate_skinport_metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    min_price = parse_float(row.get("min_price"))
    median_price = parse_float(row.get("median_price"))
    max_price = parse_float(row.get("max_price"))
    quantity = parse_int(row.get("quantity"))

    discount = safe_divide(median_price - min_price, median_price) if min_price is not None and median_price is not None else None
    dispersion = safe_divide(max_price - min_price, median_price) if min_price is not None and median_price is not None and max_price is not None else None

    execution_risk = 0.0
    if quantity is None:
        execution_risk += 0.35
    elif quantity <= 1:
        execution_risk += 0.50
    elif quantity < 5:
        execution_risk += 0.20
    if discount is not None and discount > 0.25:
        execution_risk += 0.25

    return {
        "skinport_cost_cny": parse_float(row.get("skinport_cost_cny")) or parse_float(row.get("cost_rmb")),
        "skinport_discount_rate": discount,
        "skinport_price_dispersion": dispersion,
        "skinport_quantity": quantity,
        "skinport_data_age": None,
        "skinport_execution_risk": _bounded(execution_risk),
    }


def _joined_reasons(reasons: List[str]) -> str:
    return " | ".join(dict.fromkeys([r for r in reasons if r]))


def classify_row(row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, str]:
    stable_cfg = config["stable_arbitrage"]
    vol_cfg = config["high_volatility"]
    risk_codes: List[str] = []
    risk_reasons: List[str] = []
    rec_reasons: List[str] = []
    subcategories: List[str] = []

    cost = parse_float(row.get("skinport_cost_cny"))
    yyyp_buy = parse_float(row.get("yyyp_buy_price"))
    flat_roi = parse_float(row.get("flat_roi"))
    expected_roi = parse_float(row.get("expected_roi"))
    stress_roi = parse_float(row.get("stress_roi"))
    r7 = parse_float(row.get("normalized_rate_7_to_7"))
    r1 = parse_float(row.get("normalized_rate_1_to_7"))
    r15 = parse_float(row.get("normalized_rate_15_to_7"))
    r30 = parse_float(row.get("normalized_rate_30_to_7"))
    acceleration = parse_float(row.get("short_term_acceleration"))
    dispersion = parse_float(row.get("trend_dispersion"))
    spread = parse_float(row.get("yyyp_spread_rate"))
    buy_num = parse_int(row.get("yyyp_buy_num")) or 0
    imbalance = parse_float(row.get("order_imbalance"))
    buff_change = parse_float(row.get("buff_price_chg"))
    buy_premium = parse_float(row.get("yyyp_buff_buy_premium"))
    sell_premium = parse_float(row.get("yyyp_buff_sell_premium"))
    risk_score_value = parse_float(row.get("risk_score")) or 100.0

    if cost is None or cost <= 0 or yyyp_buy is None or yyyp_buy <= 0:
        risk_codes.append("INSUFFICIENT_PRICE_DATA")
        risk_reasons.append("成本或悠悠求购价缺失，无法可靠计算收益")
        return {
            "candidate_category": "INSUFFICIENT_DATA",
            "candidate_subcategory": "INSUFFICIENT_DATA",
            "risk_codes": _joined_reasons(risk_codes),
            "risk_reasons": _joined_reasons(risk_reasons),
            "recommendation_reasons": "",
        }

    if r7 is not None and r7 < -float(vol_cfg["abnormal_rate_7"]):
        risk_codes.append("HIGH_7D_DECLINE")
        risk_reasons.append("近7日价格明显下降")
    if spread is not None and spread > float(stable_cfg["max_spread_rate"]):
        risk_codes.append("WIDE_YYYP_SPREAD")
        risk_reasons.append("悠悠买卖价差偏大")
    if imbalance is not None and imbalance < -0.35:
        risk_codes.append("SELL_PRESSURE")
        risk_reasons.append("出售数量明显高于求购数量")
    if dispersion is not None and dispersion > float(stable_cfg["max_trend_dispersion"]):
        risk_codes.append("HIGH_TREND_DISPERSION")
        risk_reasons.append("多周期趋势分歧较大")

    if acceleration is not None and acceleration > float(vol_cfg["acceleration_threshold"]):
        subcategories.append("SPIKE_RISK")
        risk_codes.append("SPIKE_RISK")
        risk_reasons.append("短期涨幅显著高于7日趋势，存在追高回落风险")
    if r1 is not None and r1 > float(vol_cfg["abnormal_rate_1_to_7"]):
        subcategories.append("SPIKE_RISK")
        risk_codes.append("FAST_1D_RISE")
        risk_reasons.append("近1日等效7日涨幅异常")
    if r7 is not None and r7 > float(vol_cfg["abnormal_rate_7"]) and (buff_change is None or buff_change <= 0):
        subcategories.append("SPIKE_RISK")
        risk_codes.append("BUFF_NOT_CONFIRM_RISE")
        risk_reasons.append("悠悠上涨未得到BUFF同步确认")

    long_term_ok = all(v is None or v > -0.06 for v in (r15, r30))
    if r7 is not None and r7 < float(vol_cfg["dip_rate_threshold"]) and long_term_ok and flat_roi is not None and flat_roi > 0:
        subcategories.append("DIP_OPPORTUNITY")
        risk_codes.append("DIP_OPPORTUNITY")
        risk_reasons.append("近7日下跌但长期趋势未明显恶化，属于高风险抄底观察")

    valid_returns = [v for v in (r1, r7, r15, r30) if v is not None]
    if len(valid_returns) >= 2:
        has_up = any(v > 0.03 for v in valid_returns)
        has_down = any(v < -0.03 for v in valid_returns)
        trend_range = max(valid_returns) - min(valid_returns)
        if has_up and has_down and trend_range > float(vol_cfg["trend_conflict_threshold"]):
            subcategories.append("TREND_CONFLICT")
            risk_codes.append("TREND_CONFLICT")
            risk_reasons.append("不同周期趋势方向冲突")

    premium_limit = float(stable_cfg["max_cross_market_premium"])
    if (buy_premium is not None and abs(buy_premium) > premium_limit) or (sell_premium is not None and abs(sell_premium) > premium_limit):
        subcategories.append("CROSS_MARKET_DIVERGENCE")
        risk_codes.append("CROSS_MARKET_DIVERGENCE")
        risk_reasons.append("悠悠与BUFF价格偏离过大")

    premium_ok = True
    for premium in (buy_premium, sell_premium):
        if premium is not None and abs(premium) > premium_limit:
            premium_ok = False

    stable = (
        flat_roi is not None and flat_roi >= float(stable_cfg["min_flat_roi"])
        and expected_roi is not None and expected_roi >= float(stable_cfg["min_expected_roi"])
        and stress_roi is not None and stress_roi >= float(stable_cfg["min_stress_roi"])
        and (r7 is None or abs(r7) <= float(stable_cfg["max_abs_rate_7"]))
        and (dispersion is None or dispersion <= float(stable_cfg["max_trend_dispersion"]))
        and (spread is None or spread <= float(stable_cfg["max_spread_rate"]))
        and buy_num >= int(stable_cfg["min_yyyp_buy_num"])
        and premium_ok
        and risk_score_value <= float(stable_cfg["max_risk_score"])
    )

    if stable:
        rec_reasons.extend([
            "当前静态收益满足阈值",
            "压力情景下仍保持盈利",
            "悠悠求购数量达到最低要求",
        ])
        if dispersion is not None:
            rec_reasons.append("多周期趋势相对稳定")
        if spread is not None:
            rec_reasons.append("悠悠买卖价差处于可控范围")
        category = "STABLE_ARBITRAGE"
    elif subcategories:
        category = "HIGH_VOLATILITY"
    elif flat_roi is not None and flat_roi > 0:
        category = "NOT_RECOMMENDED"
        risk_reasons.append("账面有利润但风险或压力收益不满足稳定搬砖条件")
    else:
        category = "NOT_RECOMMENDED"
        risk_reasons.append("当前静态收益不满足推荐条件")

    return {
        "candidate_category": category,
        "candidate_subcategory": _joined_reasons(subcategories),
        "risk_codes": _joined_reasons(risk_codes),
        "risk_reasons": _joined_reasons(risk_reasons),
        "recommendation_reasons": _joined_reasons(rec_reasons),
    }


def analyze_row(row: Dict[str, Any], config: Dict[str, Any], semantic_confidence: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    result.update(normalize_period_returns(row, config))

    yyyp_buy = parse_float(row.get("yyyp_buy_price"))
    yyyp_sell = parse_float(row.get("yyyp_sell_price"))
    result["yyyp_spread_rate"] = yyyp_spread_rate(yyyp_buy, yyyp_sell)
    result["order_imbalance"] = order_imbalance(row.get("yyyp_buy_num"), row.get("yyyp_sell_num"))
    result["buy_sell_num_ratio"] = buy_sell_num_ratio(row.get("yyyp_buy_num"), row.get("yyyp_sell_num"))
    result.update(calculate_cross_market_metrics({**row, **result}))
    result.update(calculate_skinport_metrics(row))

    result["stress_change_7d"] = stress_change({**row, **result}, config)
    exit_fee_rate = float(config["risk_model"].get("exit_fee_rate", 0.0))
    cost = parse_float(result.get("skinport_cost_cny"))
    flat_price = yyyp_buy
    expected_price = yyyp_buy * (1.0 + result["expected_change_7d"]) if yyyp_buy is not None else None
    stress_price = yyyp_buy * (1.0 + result["stress_change_7d"]) if yyyp_buy is not None else None

    result["flat_price_7d"] = flat_price
    result["expected_price_7d"] = expected_price
    result["stress_price_7d"] = stress_price

    for prefix, price in (("flat", flat_price), ("expected", expected_price), ("stress", stress_price)):
        income = price * (1.0 - exit_fee_rate) if price is not None else None
        profit = income - cost if income is not None and cost is not None else None
        roi = profit / cost if profit is not None and cost and cost > 0 else None
        result[f"{prefix}_net_income"] = income
        result[f"{prefix}_profit"] = profit
        result[f"{prefix}_roi"] = roi

    weights = config["risk_score_weights"]
    trend_risk = _score(max(-(parse_float(result.get("expected_change_7d")) or 0.0), -(parse_float(result.get("normalized_rate_7_to_7")) or 0.0), 0.0), 0.20, float(weights["trend"]))
    dispersion_risk = _score(parse_float(result.get("trend_dispersion")), 0.20, float(weights["dispersion"]))
    spread_risk = _score(parse_float(result.get("yyyp_spread_rate")), 0.25, float(weights["spread"]))
    liquidity_base = (1.0 - min((parse_int(row.get("yyyp_buy_num")) or 0) / 20.0, 1.0)) * 0.6
    liquidity_base += max(-(parse_float(result.get("order_imbalance")) or 0.0), 0.0) * 0.4
    liquidity_risk = _bounded(liquidity_base) * float(weights["liquidity"])
    premium_values = [abs(v) for v in (parse_float(result.get("yyyp_buff_buy_premium")), parse_float(result.get("yyyp_buff_sell_premium"))) if v is not None]
    cross_market_risk = _score(max(premium_values) if premium_values else None, 0.25, float(weights["cross_market"]))
    execution_risk = _bounded(parse_float(result.get("skinport_execution_risk"))) * float(weights["execution"])
    stale_data_risk = 0.0
    missing_core = sum(1 for key in ("sell_price_rate_1", "sell_price_rate_7", "sell_price_rate_15", "sell_price_rate_30", "buff_buy_price", "buff_sell_price") if row.get(key) is None or pd.isna(row.get(key)))
    semantic_risk = 0.0 if semantic_confidence == "HIGH" else 0.35 if semantic_confidence == "MEDIUM" else 0.65
    data_quality_risk = _bounded((missing_core / 6.0) * 0.55 + semantic_risk * 0.45) * float(weights["data_quality"])

    result.update({
        "trend_risk_score": trend_risk,
        "dispersion_risk_score": dispersion_risk,
        "spread_risk_score": spread_risk,
        "liquidity_risk_score": liquidity_risk,
        "cross_market_risk_score": cross_market_risk,
        "execution_risk_score": execution_risk,
        "stale_data_risk_score": stale_data_risk,
        "data_quality_risk_score": data_quality_risk,
    })
    total_risk = min(100.0, sum(float(result[key]) for key in (
        "trend_risk_score", "dispersion_risk_score", "spread_risk_score",
        "liquidity_risk_score", "cross_market_risk_score", "execution_risk_score",
        "stale_data_risk_score", "data_quality_risk_score",
    )))
    result["risk_score"] = total_risk
    result["risk_level"] = risk_level(total_risk, config)

    score_cfg = config["risk_adjusted_score"]
    normalized_profit = min(max((parse_float(result.get("expected_profit")) or 0.0) / float(score_cfg["profit_normalizer"]), 0.0), 1.0)
    liquidity_score = min((parse_int(row.get("yyyp_buy_num")) or 0) / 50.0, 1.0)
    result["risk_adjusted_score"] = (
        float(score_cfg["expected_roi_weight"]) * (parse_float(result.get("expected_roi")) or 0.0)
        + float(score_cfg["stress_roi_weight"]) * (parse_float(result.get("stress_roi")) or 0.0)
        + float(score_cfg["profit_weight"]) * normalized_profit
        + float(score_cfg["liquidity_weight"]) * liquidity_score
        - float(score_cfg["risk_score_weight"]) * (total_risk / 100.0)
    )

    result["field_semantic_confidence"] = semantic_confidence
    result["data_quality_status"] = "OK" if data_quality_risk < 5 else "DEGRADED" if data_quality_risk < 10 else "INSUFFICIENT"
    result["calculated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    result.update(classify_row({**row, **result}, config))
    return result


def add_risk_columns(df: pd.DataFrame, config: Dict[str, Any], semantic_confidence: str) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            rows.append(analyze_row(row.to_dict(), config, semantic_confidence))
        except Exception as exc:  # keep one corrupt item from killing the whole run.
            rows.append({
                "candidate_category": "INSUFFICIENT_DATA",
                "candidate_subcategory": "INSUFFICIENT_DATA",
                "risk_codes": "ROW_ANALYSIS_FAILED",
                "risk_reasons": f"单条数据风险计算失败: {type(exc).__name__}",
                "recommendation_reasons": "",
                "risk_score": 100.0,
                "risk_level": "VERY_HIGH",
                "risk_adjusted_score": -100.0,
                "field_semantic_confidence": semantic_confidence,
                "data_quality_status": "INSUFFICIENT",
                "calculated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            })
    risk_df = pd.DataFrame(rows, index=df.index)
    return pd.concat([df.copy(), risk_df], axis=1)
