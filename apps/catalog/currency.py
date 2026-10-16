import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_inr(value, symbol=True):
    """
    Standard Indian Rupee (INR) currency formatter.
    Formats numbers with Indian grouping (e.g., 1,00,000.00) and EXACTLY two decimal places.

    Examples:
      0 -> ₹0.00
      25 -> ₹25.00
      25.5 -> ₹25.50
      25.567 -> ₹25.57
      999.999 -> ₹1,000.00
      100000 -> ₹1,00,000.00
      -50.25 -> -₹50.25
    """
    if value is None or value == '':
        return '₹0.00' if symbol else '0.00'

    # If already a string containing currency symbols or commas, clean it safely
    if isinstance(value, str):
        cleaned = re.sub(r'[^\d.-]', '', value.strip())
        if not cleaned or cleaned in ['-', '.', '-.']:
            return '₹0.00' if symbol else '0.00'
        try:
            d = Decimal(cleaned)
        except InvalidOperation:
            return '₹0.00' if symbol else '0.00'
    elif isinstance(value, Decimal):
        d = value
    elif isinstance(value, (int, float)):
        # Avoid float representation errors by converting via str
        d = Decimal(str(value))
    else:
        try:
            d = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return '₹0.00' if symbol else '0.00'

    # Quantize to exactly 2 decimal places using half-up rounding
    d = d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    is_neg = d < 0
    d_abs = abs(d)

    parts = f"{d_abs:.2f}".split('.')
    int_part = parts[0]
    frac_part = parts[1]

    # Indian grouping: last 3 digits, then groups of 2 digits
    if len(int_part) > 3:
        last3 = int_part[-3:]
        remaining = int_part[:-3]
        groups = []
        while len(remaining) > 2:
            groups.insert(0, remaining[-2:])
            remaining = remaining[:-2]
        if remaining:
            groups.insert(0, remaining)
        int_formatted = ','.join(groups) + ',' + last3
    else:
        int_formatted = int_part

    num_str = f"{int_formatted}.{frac_part}"
    if not symbol:
        return f"-{num_str}" if is_neg else num_str

    formatted = f"₹{num_str}"
    return f"-{formatted}" if is_neg else formatted
