"""Deterministic checks. Each returns one Signal; run_checks runs them all in a fixed order.

Weights are tiers (the contract's enum); detector.config maps tiers to numbers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..config import Settings
from ..models import DocumentText, ExtractedInvoice, InboundMessage, VendorOnFile


@dataclass
class Signal:
    name: str
    fired: bool
    weight: str
    detail: str
    evidence: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "fired": self.fired, "weight": self.weight, "detail": self.detail}
        if self.evidence is not None:
            d["evidence"] = self.evidence
        return d


@dataclass
class CheckContext:
    message: InboundMessage
    docs: list[DocumentText]
    invoice: ExtractedInvoice
    vendor: Optional[VendorOnFile]
    known_domains: list[str]
    settings: Settings
    ct: Any = None                      # a CTProvider, or None when lookups are unavailable
    now: datetime = field(default_factory=lambda: datetime.now().astimezone())


def check_new_vendor(ctx: CheckContext) -> Signal:
    if ctx.vendor is None:
        who = ctx.invoice.vendor_claimed or ctx.message.sender_domain or "this sender"
        return Signal("new_vendor", True, "gate", f"No record on file for {who}; first payment is always human-reviewed")
    since = ctx.vendor.created_at.strftime("%Y")
    return Signal("new_vendor", False, "gate", f"Vendor on file since {since}")


SIGNAL_ORDER = [
    "bank_account_changed", "prompt_injection", "vendor_contact_changed", "payee_name_mismatch",
    "lookalike_domain", "new_domain_certificate", "amount_anomaly", "urgency_language", "new_vendor",
]


def run_checks(ctx: CheckContext) -> list[Signal]:
    from .amount import check_amount_anomaly
    from .bank_change import check_bank_change
    from .ct_domain import check_new_domain_certificate
    from .lookalike import check_lookalike_domain
    from .payee import check_payee_mismatch
    from .prompt_injection import check_prompt_injection
    from .urgency import check_urgency
    from .vendor_contact import check_vendor_contact_changed

    checks = {
        "bank_account_changed": check_bank_change,
        "prompt_injection": check_prompt_injection,
        "vendor_contact_changed": check_vendor_contact_changed,
        "payee_name_mismatch": check_payee_mismatch,
        "lookalike_domain": check_lookalike_domain,
        "new_domain_certificate": check_new_domain_certificate,
        "amount_anomaly": check_amount_anomaly,
        "urgency_language": check_urgency,
        "new_vendor": check_new_vendor,
    }
    return [checks[name](ctx) for name in SIGNAL_ORDER]
