"""vendor_contact_changed: the setup move (swap the phone on file first, then ask for the bank change)."""
from __future__ import annotations

from . import CheckContext, Signal

NAME, WEIGHT = "vendor_contact_changed", "high"


def check_vendor_contact_changed(ctx: CheckContext) -> Signal:
    v = ctx.vendor
    if v is None:
        return Signal(NAME, False, WEIGHT, "No vendor record", {"days_ago": None, "requested": bool(ctx.invoice.contact_change_requested)})
    on_file_days = ctx.settings.on_file_days
    parts: list[str] = []
    evidence: dict = {"days_ago": None, "field": None, "requested": bool(ctx.invoice.contact_change_requested)}
    if v.last_contact_change_at is not None:
        days = (ctx.now - v.last_contact_change_at).days
        evidence["days_ago"] = days
        evidence["field"] = v.last_contact_change_field
        if days < on_file_days:
            what = v.last_contact_change_field or "contact details"
            parts.append(f"Vendor {what} changed {days} days ago (on {v.last_contact_change_at:%Y-%m-%d}); not yet on file for {on_file_days} days")
    if ctx.invoice.contact_change_requested:
        detail = ctx.invoice.contact_change_detail
        parts.append("Message asks to change contact details on file" + (f": {detail}" if detail else ""))
    if parts:
        return Signal(NAME, True, WEIGHT, "; ".join(parts), evidence)
    return Signal(NAME, False, WEIGHT, f"No vendor record edits in the last {on_file_days} days", evidence)
