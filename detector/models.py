"""Data shapes shared across the detector. The Decision itself is defined by the contract."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

E164 = re.compile(r"\+[1-9][0-9]{6,14}")


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class HiddenSpan:
    """Text a human would not see: white-on-white, tiny, off the page, or CSS-hidden."""
    source: str                 # "pdf" | "html"
    reason: str                 # "white_text" | "tiny_text" | "off_page" | "css_hidden"
    text: str
    line: Optional[int] = None  # 1-based line in reading order (pdf only)
    font_size: Optional[float] = None
    color: Optional[list[float]] = None


@dataclass
class DocumentText:
    visible: str
    hidden: list[HiddenSpan] = field(default_factory=list)
    line_count: int = 0
    error: Optional[str] = None     # set when the attachment could not be read or inspected
    name: str = ""
    kind: str = "pdf"               # "pdf" | "html" | "text" | "other"


@dataclass
class InboundMessage:
    sender_email: Optional[str]
    sender_name: Optional[str]
    reply_to: Optional[str]
    subject: Optional[str]
    body_text: str
    body_hidden: list[HiddenSpan]
    attachments: list[Attachment]
    received_at: datetime
    raw_sha256: str
    forwarded: bool = False
    outer_sender_email: Optional[str] = None   # who actually sent us a forwarded message

    @property
    def outer_sender_domain(self) -> Optional[str]:
        if self.outer_sender_email and "@" in self.outer_sender_email:
            return self.outer_sender_email.rsplit("@", 1)[1].lower()
        return None

    @property
    def sender_domain(self) -> Optional[str]:
        if self.sender_email and "@" in self.sender_email:
            return self.sender_email.rsplit("@", 1)[1].lower()
        return None

    @property
    def reply_to_domain(self) -> Optional[str]:
        if self.reply_to and "@" in self.reply_to:
            return self.reply_to.rsplit("@", 1)[1].lower()
        return None


def _all_required(schema: dict, cls=None) -> None:
    props = schema.get("properties", {})
    schema["required"] = list(props)
    for prop in props.values():
        prop.pop("default", None)


class ClaudeExtraction(BaseModel):
    """What Claude fills in for extraction. Flat and fully required, with "" or [] meaning absent:
    the API rejects schemas with many optional or nullable fields ("Schema is too complex")."""
    model_config = ConfigDict(json_schema_extra=_all_required)
    vendor_claimed: str = Field("", description="Company the message claims to be from; empty if unknown.")
    payee_name: str = Field("", description="Name payment should be made out to (remit-to, beneficiary, pay-to); empty if none.")
    amount: str = Field("", description="Total amount due exactly as printed, e.g. '$4,820.00'; empty if none.")
    currency: str = Field("USD", description="ISO 4217 code, e.g. USD.")
    invoice_number: str = Field("", description="Invoice identifier as printed; empty if none.")
    bank_account_last4: str = Field("", description="Last four digits of the bank account it asks to pay to; empty if none.")
    routing_number_last4: str = Field("", description="Last four digits of the routing/ABA number; empty if none.")
    other_account_last4: list[str] = Field(default_factory=list, description="Last four digits of any other payment account numbers given.")
    bank_change_requested: bool = Field(False, description="True if it asks to pay to new or changed bank details, or says they changed.")
    bank_change_justification: str = Field("", description="The reason given for the change; empty if none.")
    contact_change_requested: bool = Field(False, description="True if it asks to update a phone number, email or contact person on file.")
    contact_change_detail: str = Field("", description="What contact detail it asks to change; empty if none.")
    urgency_phrases: list[str] = Field(default_factory=list, description="Short quoted phrases that create time pressure.")
    summary: str = Field("", description="One neutral sentence describing the message.")


class ExtractedInvoice(BaseModel):
    """Fields pulled from the email and the visible invoice text.

    Doubles as the structured-output schema for Claude, so keep it flat and simple.
    Account numbers are last-4 only: the full number never leaves the ingest step.
    """
    vendor_claimed: Optional[str] = Field(None, description="Who the message claims to be from (company name).")
    payee_name: Optional[str] = Field(None, description="Name the payment should be made out to (remit-to, beneficiary, pay-to).")
    amount: Optional[float] = Field(None, description="Total amount due.")
    currency: str = Field("USD", description="ISO 4217 code.")
    invoice_number: Optional[str] = Field(None, description="Invoice identifier as printed.")
    bank_account_last4: Optional[str] = Field(None, description="Last four digits of the bank account the message asks to pay to.")
    routing_number_last4: Optional[str] = Field(None, description="Last four digits of the routing/ABA number, if present.")
    other_account_last4: list[str] = Field(default_factory=list, description="Last four digits of any other bank account numbers the message or invoices give for payment.")
    bank_change_requested: bool = Field(False, description="True if the message or invoice asks to pay to new or changed bank details, or says they changed.")
    bank_change_justification: Optional[str] = Field(None, description="The reason given for the change, quoted or paraphrased, if any.")
    contact_change_requested: bool = Field(False, description="True if it asks to update a phone number, email or contact person on file.")
    contact_change_detail: Optional[str] = Field(None, description="What contact detail it asks to change, if any.")
    urgency_phrases: list[str] = Field(default_factory=list, description="Short quoted phrases that create time pressure.")
    summary: str = Field("", description="One neutral sentence describing the message.")


@dataclass
class VendorOnFile:
    """The vendor record as the checks see it: current values plus when each was last changed.

    Built by detector.vendors from the versioned store. "On file" means unchanged for
    Settings.on_file_days; see trusted_phone().
    """
    vendor_id: str
    name: str
    domain: Optional[str]
    phone: Optional[str]
    phone_since: Optional[datetime]
    email: Optional[str]
    email_since: Optional[datetime]
    contact_name: Optional[str]
    bank_last4: Optional[str]
    bank_since: Optional[datetime]
    payment_history: list[float]
    last_contact_change_at: Optional[datetime]
    created_at: datetime
    last_contact_change_field: Optional[str] = None
    routing_last4: Optional[str] = None
    routing_since: Optional[datetime] = None
    phone_recorded_at: Optional[datetime] = None            # when we actually recorded the phone
    last_contact_change_recorded_at: Optional[datetime] = None  # when the change was actually recorded

    def trusted_phone(self, now: datetime, on_file_days: int) -> tuple[Optional[str], Optional[datetime]]:
        """The callback number, only if it is E.164 and has been on file unchanged for on_file_days.

        Trust is based on phone_recorded_at (when we actually recorded it), not phone_since
        (when the vendor claims it became effective).
        """
        recorded = self.phone_recorded_at or self.phone_since
        if (self.phone and E164.fullmatch(self.phone) and recorded
                and (now - recorded).days >= on_file_days):
            return self.phone, recorded
        return None, None
