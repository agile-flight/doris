"""amount_anomaly: payments out of line with this vendor's history.

Weight is dynamic: a moderately large amount is "medium" (evidence, not a decision on its
own) but an extreme one is "high", so on its own it reaches the HOLD threshold. An invoice
above the absolute ceiling is held whatever the history says, so a $10^23 invoice cannot pay.
"""
from __future__ import annotations

import statistics

from . import CheckContext, Signal

NAME, WEIGHT = "amount_anomaly", "medium"
EXTREME_WEIGHT = "high"   # 0.30 on its own -> HOLD


def check_amount_anomaly(ctx: CheckContext) -> Signal:
    amount = ctx.invoice.amount
    if amount is None:
        return Signal(NAME, False, WEIGHT, "No amount found", {"n": 0})
    ceiling = ctx.settings.amount_hold_ceiling
    if amount >= ceiling:
        return Signal(NAME, True, EXTREME_WEIGHT,
                      f"${amount:,.2f} is above the ${ceiling:,.0f} review ceiling",
                      {"amount": amount, "ceiling": ceiling, "reason": "absolute_ceiling"})
    if ctx.vendor is None:
        return Signal(NAME, False, WEIGHT, "No payment history (new vendor)", {"n": 0})
    history = [float(x) for x in ctx.vendor.payment_history if x is not None]
    if len(history) < 3:
        return Signal(NAME, False, WEIGHT, f"Not enough payment history ({len(history)} payments)", {"n": len(history)})
    median, mx = statistics.median(history), max(history)
    ratio = round(amount / median, 2) if median else None
    evidence = {"n": len(history), "median": median, "max": mx, "ratio": ratio}
    if mx and amount > ctx.settings.amount_extreme_ratio * mx:
        return Signal(NAME, True, EXTREME_WEIGHT,
                      f"${amount:,.2f} is more than {ctx.settings.amount_extreme_ratio:g}x this vendor's largest invoice (${mx:,.2f}; median ${median:,.2f})",
                      {**evidence, "reason": "extreme"})
    if (median and amount > ctx.settings.amount_anomaly_ratio * median) or amount > 1.5 * mx:
        return Signal(NAME, True, WEIGHT, f"${amount:,.2f} is {ratio}x this vendor's median payment (${median:,.2f}; max ${mx:,.2f})", evidence)
    return Signal(NAME, False, WEIGHT, f"${amount:,.2f} is within this vendor's usual range (median ${median:,.2f})", evidence)
