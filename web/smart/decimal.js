'use strict';
/* Exact display of Decimal strings. Financial values never pass through Number. */
const MingJingDecimal = (() => {
  const powers = { '元': 0, '千元': 3, '万元': 4, '百万元': 6, '千万元': 7, '亿元': 8,
    '%': -2, '％': -2, '百分点': -2, 'pct': -2, 'pp': -2, 'bp': -4,
    '股': 0, '万股': 4, '百万股': 6, '亿股': 8, '倍': 0, '次': 0, 'x': 0, '元/股': 0, '': 0 };
  function format(value, unit = '', decimals = 2) {
    const raw = String(value), match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
    if (!match || powers[unit] === undefined) return raw + (unit ? ' ' + unit : '');
    const d = Math.max(0, Math.min(30, Number(decimals) || 0)), exponent = Number(match[4] || 0);
    if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 500) return raw + ' ' + unit;
    let amount = BigInt(match[2] + (match[3] || ''));
    const shift = d - (match[3] || '').length + exponent - powers[unit];
    if (shift >= 0) amount *= 10n ** BigInt(shift);
    else {
      const divisor = 10n ** BigInt(-shift), remainder = amount % divisor;
      amount = amount / divisor + (remainder * 2n >= divisor ? 1n : 0n);
    }
    let digits = amount.toString().padStart(d + 1, '0');
    let whole = d ? digits.slice(0, -d) : digits;
    whole = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const shown = (match[1] === '-' && amount !== 0n ? '-' : '') + whole + (d ? '.' + digits.slice(-d) : '');
    return shown + (unit === '%' || unit === '％' ? '%' : unit ? ' ' + unit : '');
  }
  return { format };
})();
if (typeof module !== 'undefined') module.exports = MingJingDecimal;
