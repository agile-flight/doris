"""urgency_language: manufactured time pressure."""
from __future__ import annotations

from . import CheckContext, Signal

NAME, WEIGHT = "urgency_language", "low-medium"


def check_urgency(ctx: CheckContext) -> Signal:
    phrases = list(ctx.invoice.urgency_phrases or [])
    if phrases:
        quoted = ", ".join(f'"{p}"' for p in phrases[:4])
        return Signal(NAME, True, WEIGHT, quoted, {"phrases": phrases})
    return Signal(NAME, False, WEIGHT, "No pressure language", {"phrases": []})
