"""bank_account_changed: the mechanism in nearly every real case."""
from __future__ import annotations

from . import CheckContext, Signal

NAME, WEIGHT = "bank_account_changed", "highest"


def check_bank_change(ctx: CheckContext) -> Signal:
    new = ctx.invoice.bank_account_last4
    new_routing = ctx.invoice.routing_number_last4
    claimed = bool(ctx.invoice.bank_change_requested)
    if ctx.vendor is None:
        return Signal(NAME, False, WEIGHT, "No account on file to compare",
                      {"new_last4": new, "on_file_last4": None, "new_routing": new_routing, "on_file_routing": None,
                       "claimed_change": claimed})
    on_file = ctx.vendor.bank_last4
    on_file_routing = ctx.vendor.routing_last4
    evidence = {"new_last4": new, "on_file_last4": on_file, "new_routing": new_routing,
                "on_file_routing": on_file_routing, "claimed_change": claimed}
    if not on_file:
        return Signal(NAME, False, WEIGHT, "No bank account on file for this vendor yet", evidence)
    account_changed = new and new != on_file
    routing_changed = new_routing and on_file_routing and new_routing != on_file_routing
    if account_changed or routing_changed:
        parts = []
        if account_changed:
            parts.append(f"Requests payment to ****{new}; account on file is ****{on_file}")
        if routing_changed:
            parts.append(f"Routing ****{new_routing}; routing on file is ****{on_file_routing}")
        return Signal(NAME, True, WEIGHT, "; ".join(parts), evidence)
    if new == on_file:
        detail = f"Account on file ****{on_file} unchanged"
        if claimed:
            detail += " (message claims a change, but the account matches)"
        return Signal(NAME, False, WEIGHT, detail, evidence)
    if claimed:
        return Signal(NAME, True, WEIGHT, f"Message says bank details changed; no account number found to compare with ****{on_file}", evidence)
    return Signal(NAME, False, WEIGHT, f"No account number in the message; account on file ****{on_file}", evidence)
