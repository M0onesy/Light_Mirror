"""Integer-proof Decimal root bounds for the registered CAGR algorithm."""
from decimal import Decimal, localcontext
from .decimal_math import DecimalInterval

def integer_root_bounds(value: Decimal, degree: int, places: int=48) -> DecimalInterval:
    if not isinstance(value,Decimal) or not value.is_finite() or value<=0:
        raise ValueError('CAGR操作数必须为有限正数')
    if type(degree) is not int or not 1<=degree<=100:
        raise ValueError('CAGR期数须为一至一百的明确整数')
    if type(places) is not int or not 1<=places<=64 or abs(value.adjusted())>1000:
        raise ValueError('CAGR精度或金额尺度超出登记算法上限')
    numerator,denominator=value.as_integer_ratio()
    scaled=numerator*10**(places*degree)
    left,right=0,1
    while right**degree*denominator<scaled:right*=2
    while left+1<right:
        mid=(left+right)//2
        if mid**degree*denominator<=scaled:left=mid
        else:right=mid
    if right**degree*denominator==scaled:left=right
    exact=left**degree*denominator==scaled
    with localcontext() as ctx:
        ctx.prec=max(80,len(str(left))+10)
        scale=Decimal(10)**(-places)
        lower=Decimal(left)*scale
        upper=lower if exact else Decimal(left+1)*scale
    return DecimalInterval(lower,upper,True,True)

def cagr_interval(current,base,years):
    if years.lower!=years.upper or not years.lower_closed or not years.upper_closed:
        return None
    count=years.lower
    if count!=count.to_integral_value() or not 1<=count<=100 or current.lower<=0 or base.lower<=0:
        return None
    try:
        ratio=current.divide_positive(base)
        lower=integer_root_bounds(ratio.lower,int(count)).lower
        upper=integer_root_bounds(ratio.upper,int(count)).upper
    except ValueError:
        return None
    with localcontext() as context:
        context.prec=max(80,len(lower.as_tuple().digits)+8,len(upper.as_tuple().digits)+8)
        return DecimalInterval(lower-1,upper-1,True,True)
