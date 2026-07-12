"""Field parsing and numeric normalization utilities."""
from __future__ import annotations

import math
from typing import Any, List, Optional

NULL_MARKERS = {"", "-", "--", "null", "none", "nan", "n/a", "N/A", "None"}


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() in NULL_MARKERS:
        return True
    return False


def parse_float(value: Any, field: str = "", issues: Optional[List[str]] = None) -> Optional[float]:
    if is_missing(value):
        if issues is not None and field:
            issues.append(f"{field}: 缺失")
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", "").replace("%", "")
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            raise ValueError("not finite")
        return parsed
    except (TypeError, ValueError):
        if issues is not None and field:
            issues.append(f"{field}: 无法转换为数字({value!r})")
        return None


def parse_int(value: Any, field: str = "", issues: Optional[List[str]] = None) -> Optional[int]:
    parsed = parse_float(value, field, issues)
    if parsed is None:
        return None
    if parsed < 0 and issues is not None and field:
        issues.append(f"{field}: 数量为负数({value!r})")
    return int(parsed)


def normalize_rate(value: Any, max_abs_rate: float = 3.0, field: str = "", issues: Optional[List[str]] = None) -> Optional[float]:
    """Normalize API rate values to decimal form: 0.05 means 5%."""
    raw = parse_float(value, field, issues)
    if raw is None:
        return None

    normalized = raw / 100.0 if abs(raw) > 1.0 else raw
    if abs(normalized) > max_abs_rate:
        if issues is not None and field:
            issues.append(f"{field}: 涨跌率超过300%({raw!r})")
        return None
    return normalized


def safe_divide(numerator: Any, denominator: Any) -> Optional[float]:
    num = parse_float(numerator)
    den = parse_float(denominator)
    if num is None or den is None or den == 0:
        return None
    return num / den


def clamp(value: Optional[float], lower: float, upper: float) -> Optional[float]:
    if value is None:
        return None
    return max(lower, min(upper, value))


def normalize_return_to_7d(
    rate: Any,
    period_days: int,
    floor: float = -0.50,
    ceiling: float = 0.80,
) -> Optional[float]:
    parsed = parse_float(rate)
    if parsed is None or period_days <= 0:
        return None
    if 1.0 + parsed <= 0:
        return None
    result = (1.0 + parsed) ** (7.0 / float(period_days)) - 1.0
    return clamp(result, floor, ceiling)
