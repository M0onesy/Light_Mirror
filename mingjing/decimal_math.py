"""Decimal disclosure intervals, without transport or model dependencies.

The proposal defines compatibility by intersection of two disclosure intervals.
The half-open rounding policy is explicit; it is not a claim that a rounded
source establishes every digit of a more precise research-report statement.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, getcontext, localcontext


_SCALES = {
    "元": "1", "元/股": "1", "万元": "10000", "万": "10000",
    "百万元": "1000000", "亿元": "100000000", "亿": "100000000",
    "%": "0.01", "％": "0.01", "百分点": "0.01",
    "比例": "1", "股": "1", "万股": "10000", "亿股": "100000000",
    "倍": "1", "": "1",
}


def unit_scale(unit: str) -> Decimal:
    if unit not in _SCALES:
        raise ValueError("来源单位未登记，无法建立可靠披露区间")
    return Decimal(_SCALES[unit])


def _precision(*values: Decimal) -> int:
    finite = [value for value in values if value.is_finite()]
    span = max((value.adjusted() for value in finite), default=0) - min(
        (value.as_tuple().exponent for value in finite), default=0)
    return max(getcontext().prec, 64, span + 12)


def resolution(unit: str, decimals: int) -> Decimal:
    if type(decimals) is not int or decimals < 0:
        raise ValueError("披露小数位数须为非负整数")
    scale = unit_scale(unit)
    with localcontext() as context:
        context.prec = _precision(scale) + decimals
        return scale * Decimal(10) ** -decimals


@dataclass(frozen=True)
class DecimalInterval:
    lower: Decimal
    upper: Decimal
    lower_closed: bool = True
    upper_closed: bool = False

    def __post_init__(self):
        if not all(isinstance(value, Decimal) and value.is_finite()
                   for value in (self.lower, self.upper)):
            raise ValueError("区间端点必须为有限Decimal")
        if self.lower > self.upper or (self.lower == self.upper and not (
                self.lower_closed and self.upper_closed)):
            raise ValueError("披露区间为空或上下界无效")

    def to_dict(self) -> dict:
        return {"lower": str(self.lower), "upper": str(self.upper),
                "lower_closed": self.lower_closed, "upper_closed": self.upper_closed}

    def intersection(self, other: "DecimalInterval") -> "DecimalInterval | None":
        lower, upper = max(self.lower, other.lower), min(self.upper, other.upper)
        lower_closed = ((self.lower < lower or self.lower_closed)
                        and (other.lower < lower or other.lower_closed))
        upper_closed = ((self.upper > upper or self.upper_closed)
                        and (other.upper > upper or other.upper_closed))
        if lower > upper or (lower == upper and not (lower_closed and upper_closed)):
            return None
        return DecimalInterval(lower, upper, lower_closed, upper_closed)

    def intersects(self, other: "DecimalInterval") -> bool:
        return self.intersection(other) is not None

    def expanded(self, amount: Decimal) -> "DecimalInterval":
        if not amount.is_finite() or amount < 0:
            raise ValueError("区间放宽量必须为非负有限Decimal")
        with localcontext() as context:
            context.prec = _precision(self.lower, self.upper, amount)
            return DecimalInterval(self.lower-amount, self.upper+amount,
                                   self.lower_closed, self.upper_closed)

    def scaled(self, multiplier: Decimal) -> "DecimalInterval":
        if not multiplier.is_finite():
            raise ValueError("区间倍率必须为有限Decimal")
        with localcontext() as context:
            context.prec = _precision(self.lower, self.upper, multiplier)
            if multiplier == 0:
                return DecimalInterval(Decimal(0), Decimal(0), True, True)
            if multiplier > 0:
                return DecimalInterval(self.lower*multiplier, self.upper*multiplier,
                                       self.lower_closed, self.upper_closed)
            return DecimalInterval(self.upper*multiplier, self.lower*multiplier,
                                   self.upper_closed, self.lower_closed)

    def subtract(self, other: "DecimalInterval") -> "DecimalInterval":
        with localcontext() as context:
            context.prec = _precision(self.lower, self.upper, other.lower, other.upper)
            return DecimalInterval(self.lower-other.upper, self.upper-other.lower,
                                   self.lower_closed and other.upper_closed,
                                   self.upper_closed and other.lower_closed)

    def add(self, other: "DecimalInterval") -> "DecimalInterval":
        with localcontext() as context:
            context.prec = _precision(self.lower, self.upper, other.lower, other.upper)
            return DecimalInterval(self.lower+other.lower, self.upper+other.upper,
                                   self.lower_closed and other.lower_closed,
                                   self.upper_closed and other.upper_closed)

    def multiply(self, other: "DecimalInterval") -> "DecimalInterval":
        corners = []
        with localcontext() as context:
            context.prec = max(_precision(self.lower, self.upper, other.lower, other.upper),
                               sum(max(len(v.as_tuple().digits) for v in endpoints)
                                   for endpoints in ((self.lower, self.upper), (other.lower, other.upper))) + 8)
            for value, closed in ((self.lower, self.lower_closed), (self.upper, self.upper_closed)):
                for multiplier, multiplier_closed in ((other.lower, other.lower_closed),
                                                      (other.upper, other.upper_closed)):
                    corners.append((value*multiplier, closed and multiplier_closed))
            lower, upper = min(v for v, _ in corners), max(v for v, _ in corners)
            # Zero is attained whenever one factor contains it, even if the
            # other factor's extreme is open (e.g. [0,1) * [0,1)).
            zero = DecimalInterval(Decimal(0), Decimal(0), True, True)
            includes_zero = self.intersects(zero) or other.intersects(zero)
            return DecimalInterval(lower, upper,
                any(v == lower and closed for v, closed in corners) or lower == 0 and includes_zero,
                any(v == upper and closed for v, closed in corners) or upper == 0 and includes_zero)

    def absolute(self) -> "DecimalInterval":
        if self.lower >= 0:
            return self
        if self.upper <= 0:
            return self.scaled(Decimal(-1))
        upper = max(-self.lower, self.upper)
        upper_closed = ((upper == -self.lower and self.lower_closed)
                        or (upper == self.upper and self.upper_closed))
        return DecimalInterval(Decimal(0), upper, True, upper_closed)

    def divide_positive(self, other: "DecimalInterval") -> "DecimalInterval":
        # Even an open zero endpoint allows an unbounded quotient. The
        # registered financial formula requires a strictly positive lower bound.
        if other.lower <= 0:
            raise ValueError("授权公式分母区间为非正值、接触零或跨零")
        corners = []
        precision = _precision(self.lower, self.upper, other.lower, other.upper)
        for value, value_closed in ((self.lower, self.lower_closed),
                                    (self.upper, self.upper_closed)):
            for divisor, divisor_closed in ((other.lower, other.lower_closed),
                                           (other.upper, other.upper_closed)):
                with localcontext() as context:
                    context.prec, context.rounding = precision, ROUND_FLOOR
                    lower = value/divisor
                with localcontext() as context:
                    context.prec, context.rounding = precision, ROUND_CEILING
                    upper = value/divisor
                corners.append((lower, upper, value_closed and divisor_closed))
        lower = min(item[0] for item in corners)
        upper = max(item[1] for item in corners)
        # Directed rounding that enlarges an endpoint is conservative. Open
        # exact extrema remain open, including adjacent disclosure boundaries.
        lower_closed = any(item[0] == lower and (item[2] or item[0] != item[1]) for item in corners)
        upper_closed = any(item[1] == upper and (item[2] or item[0] != item[1]) for item in corners)
        return DecimalInterval(lower, upper, lower_closed, upper_closed)


def rounding_interval(value: Decimal, unit: str, decimals: int) -> DecimalInterval:
    quantum = resolution(unit, decimals)
    with localcontext() as context:
        context.prec = _precision(value, quantum)
        half = quantum/2
        return DecimalInterval(value-half, value+half)


def fact_interval(fact) -> DecimalInterval:
    return rounding_interval(fact.value, fact.display_unit, fact.decimals)


def growth_interval(current: DecimalInterval, base: DecimalInterval) -> DecimalInterval:
    one = DecimalInterval(Decimal(1), Decimal(1), True, True)
    return current.divide_positive(base).subtract(one)


def ratio_interval(account: str, first: DecimalInterval, second: DecimalInterval) -> DecimalInterval:
    if account == "GROSS_MARGIN":
        one = DecimalInterval(Decimal(1), Decimal(1), True, True)
        return one.subtract(second.divide_positive(first))
    return first.divide_positive(second)


def round_display_interval(bounds: DecimalInterval, decimals: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Rounded one-sided extrema and an outward enclosure in display units."""
    if type(decimals) is not int or decimals < 0:
        raise ValueError('显示小数位数须为非负整数')
    with localcontext() as context:
        context.prec = _precision(bounds.lower, bounds.upper) + decimals
        quantum = Decimal(1).scaleb(-decimals)
        lower = bounds.lower.quantize(quantum, rounding=ROUND_HALF_UP)
        upper = bounds.upper.quantize(quantum, rounding=ROUND_HALF_UP)
        if not bounds.lower_closed and bounds.lower < 0 and abs(bounds.lower-lower) == quantum/2:
            lower += quantum
        if not bounds.upper_closed and bounds.upper > 0 and abs(bounds.upper-upper) == quantum/2:
            upper -= quantum
        return (lower, upper,
                bounds.lower.quantize(quantum, rounding=ROUND_FLOOR),
                bounds.upper.quantize(quantum, rounding=ROUND_CEILING))
