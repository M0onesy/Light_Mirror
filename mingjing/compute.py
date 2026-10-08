from __future__ import annotations

from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from .mentions import unit_multiplier


def tolerance(decimals: int) -> Decimal:
    return Decimal("0.5") * (Decimal("10") ** (-decimals))


def within(observed: Decimal, expected: Decimal, decimals: int = 2) -> bool:
    return abs(observed - expected) <= tolerance(decimals)


def yoy(current: Decimal, base: Decimal) -> Decimal | None:
    if base <= 0:
        return None
    return (current - base) / abs(base)


def qoq(current: Decimal, base: Decimal) -> Decimal | None:
    if base <= 0:
        return None
    return (current - base) / abs(base)


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return numerator / denominator


def display_percent(value: Decimal, decimals: int = 1) -> str:
    return _display_scaled(value, Decimal('0.01'), decimals) + '%'


def display_amount(value: Decimal, unit: str = "元", decimals: int = 2) -> str:
    scale = unit_multiplier(unit)
    return _display_scaled(value, scale, decimals) + unit


def _display_scaled(value: Decimal, scale: Decimal, decimals: int) -> str:
    """Own the formatting context; callers cannot round source values early."""
    if type(decimals) is not int or decimals < 0:
        raise ValueError('显示小数位数须为非负整数')
    if not value.is_finite():
        raise ValueError('显示数值须为有限Decimal')
    precision = max(64, len(value.as_tuple().digits) + abs(value.as_tuple().exponent)
                    + abs(scale.as_tuple().exponent) + abs(scale.adjusted()) + decimals + 12)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_UP)):
        rounded = (value / scale).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return format(rounded.copy_abs() if rounded.is_zero() else rounded, f'.{decimals}f')

