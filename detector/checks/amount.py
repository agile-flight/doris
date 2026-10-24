"""amount_anomaly: payments out of line with this vendor's history."""
from __future__ import annotations

import statistics

from . import CheckContext, Signal

NAME, WEIGHT = "amount_anomaly", "medium"


def check_amount_anomaly(ctx: CheckContext) -> Signal:
    amount = ctx.invoice.amount
    if ctx.vendor is None:
        return Signal(NAME, False, WEIGHT, "No payment history (new vendor)", {"n": 0})
    history = [float(x) for x in ctx.vendor.payment_history if x is not None]
    if amount is None:
        return Signal(NAME, False, WEIGHT, "No amount found", {"n": len(history)})
    if len(history) < 3:
        return Signal(NAME, False, WEIGHT, f"Not enough payment history ({len(history)} payments)", {"n": len(history)})
    median, mx = statistics.median(history), max(history)
    ratio = round(amount / median, 2) if median else None
    evidence = {"n": len(history), "median": median, "max": mx, "ratio": ratio}
    if (median and amount > ctx.settings.amount_anomaly_ratio * median) or amount > 1.5 * mx:
        return Signal(NAME, True, WEIGHT, f"${amount:,.2f} is {ratio}x this vendor's median payment (${median:,.2f}; max ${mx:,.2f})", evidence)
    return Signal(NAME, False, WEIGHT, f"${amount:,.2f} is within this vendor's usual range (median ${median:,.2f})", evidence)
