"""Risk score and decision rules.

Deterministic signals combine by noisy-OR over their tier weights; Claude's judgment
claims part of what is left. The decision rules are in contract/README.md:

  1. prompt_injection fired                         -> BLOCKED (hard; score floored at 0.95)
  2. bank_account_changed fired + trusted number    -> CALLBACK (the call is the verification)
  3. risk >= block_threshold                        -> BLOCKED
  4. new_vendor                                     -> HOLD
  5. bank_account_changed fired, no trusted number  -> HOLD
  6. risk >= hold_threshold ? HOLD : PAY
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .checks import Signal
from .checks.prompt_injection import AI_ADDRESSED
from .config import Settings
from .models import VendorOnFile

HARD_BLOCK_FLOOR = 0.95
TIER_RANK = {"highest": 0, "high": 1, "medium-high": 2, "medium": 3, "low-medium": 4, "residual": 5, "gate": 6}


@dataclass
class Verdict:
    risk_score: float
    decision: str
    reasons: list[str]
    hard_block: bool


def combine(signals: list[Signal], llm_risk: Optional[float], settings: Settings) -> float:
    survive = 1.0
    for s in signals:
        if s.fired and s.weight in settings.weights:
            survive *= 1.0 - settings.weights[s.weight]
    if llm_risk is not None:
        survive *= 1.0 - settings.llm_weight * min(max(float(llm_risk), 0.0), 1.0)
    return round(min(max(1.0 - survive, 0.0), 1.0), 2)


def decide(signals: list[Signal], risk: float, *, callback_number: Optional[str], settings: Settings) -> str:
    fired = {s.name for s in signals if s.fired}
    if "prompt_injection" in fired:
        return "BLOCKED"
    if "bank_account_changed" in fired and callback_number:
        return "CALLBACK"
    if risk >= settings.block_threshold:
        return "BLOCKED"
    if "new_vendor" in fired:
        return "HOLD"
    if "bank_account_changed" in fired:
        return "HOLD"
    return "HOLD" if risk >= settings.hold_threshold else "PAY"


def pretty_phone(number: Optional[str]) -> Optional[str]:
    if not number:
        return number
    m = re.fullmatch(r"\+1(\d{3})(\d{3})(\d{4})", number)
    return f"+1 {m.group(1)} {m.group(2)} {m.group(3)}" if m else number


def _quote(text: Optional[str], n: int = 80) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _reason_for(s: Signal, lookalike_fired: bool) -> Optional[str]:
    ev = s.evidence or {}
    if s.name == "bank_account_changed":
        new, on_file = ev.get("new_last4"), ev.get("on_file_last4")
        return f"Bank account change requested: ****{on_file} to ****{new}" if new and on_file else s.detail
    if s.name == "prompt_injection":
        findings = ev.get("findings") or []
        text = findings[0]["text"] if findings else ""
        suffix = " addressed to an AI" if AI_ADDRESSED.search(text or "") else ""
        kind, line = ev.get("kind"), ev.get("line")
        where = {"white_text": f"Hidden white text on line {line}", "tiny_text": f"Hidden tiny text on line {line}",
                 "off_page": f"Hidden off-page text on line {line}", "css_hidden": "Hidden HTML text",
                 "ai_addressed_text": "Text"}.get(kind, "Hidden text")
        if kind == "ai_addressed_text":
            suffix = " addressed to an AI"
        return f'{where}{suffix}: "{_quote(text)}"'
    if s.name == "lookalike_domain":
        who = "Reply-to domain" if ev.get("field") == "reply_to" else "Sender domain"
        return f"{who} {ev.get('domain')} is a lookalike of {ev.get('on_file_domain')} ({ev.get('method')})"
    if s.name == "new_domain_certificate":
        age = ev.get("age_days")
        return f"That domain got its first certificate {age} days ago" if lookalike_fired else f"Sender domain {ev.get('domain')} got its first certificate {age} days ago"
    if s.name == "payee_name_mismatch":
        return f"Payee {ev.get('payee')} is not the vendor on file"
    if s.name == "amount_anomaly":
        return f"Amount out of line with history: {s.detail}"
    if s.name == "urgency_language":
        return f"Pressure language: {s.detail}"
    if s.name == "new_vendor":
        return "First-time vendor: no record on file, so Doris cannot call a number on file"
    if s.name == "llm_judgment":
        return None
    return s.detail


def build_reasons(signals: list[Signal], decision: str, *, risk: float, callback_number: Optional[str],
                  callback_since: Optional[datetime], vendor: Optional[VendorOnFile], settings: Settings) -> list[str]:
    fired = [s for s in signals if s.fired]
    fired.sort(key=lambda s: (0 if s.name == "prompt_injection" else 1, TIER_RANK.get(s.weight, 9)))
    lookalike_fired = any(s.name == "lookalike_domain" for s in fired)
    names = {s.name for s in fired}
    lines: list[str] = []
    for s in fired:
        line = _reason_for(s, lookalike_fired)
        if line and line not in lines:
            lines.append(line)
    if decision == "CALLBACK":
        who = pretty_phone(callback_number)
        lines.append(f"Doris is calling {who}, on file since {callback_since:%B %Y}" if callback_since else f"Doris is calling {who}")
    elif decision == "HOLD":
        if "new_vendor" in names:
            lines.append("Human review required before the first payment")
        elif "bank_account_changed" in names:
            lines.append(f"No phone number on file for {settings.on_file_days} days to verify this change; a human must verify out of band")
        else:
            lines.append(f"Risk score {risk:.2f}: human review required")
    elif decision == "PAY" and not fired:
        lines.append("Routine invoice from a vendor on file")
        if vendor and vendor.bank_last4:
            lines.append(f"Bank account unchanged (****{vendor.bank_last4})")
        if vendor and len(vendor.payment_history) >= 3:
            lines.append("Amount within normal range")
    return lines


def score(signals: list[Signal], *, llm_risk: Optional[float], callback_number: Optional[str], callback_since: Optional[datetime],
          vendor: Optional[VendorOnFile], settings: Settings) -> Verdict:
    risk = combine(signals, llm_risk, settings)
    hard = any(s.fired and s.name == "prompt_injection" for s in signals)
    if hard:
        risk = max(risk, HARD_BLOCK_FLOOR)
    decision = decide(signals, risk, callback_number=callback_number, settings=settings)
    reasons = build_reasons(signals, decision, risk=risk, callback_number=callback_number, callback_since=callback_since, vendor=vendor, settings=settings)
    return Verdict(risk_score=risk, decision=decision, reasons=reasons, hard_block=hard)
