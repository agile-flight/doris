"""The pipeline: email in, Decision out. Doris is the public surface for Agent B.

    doris = Doris()
    decision = doris.check(raw_email_bytes)            # a contract Decision (dict)
    doris.resolve_callback(decision_id, vendor_confirmed=False, notes="we never changed our bank")
    doris.resolve_review(decision_id, approve=True, reviewer="...", notes="...")
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .audit import AuditLog
from .checks import CheckContext, Signal, run_checks
from .checks.ct_domain import CrtShProvider, CTProvider, FixtureCTProvider, build_ct_timeline
from .config import PROJECT_ROOT, Settings
from .extract import extract, mask_last4
from .ingest import extract_documents, ingest
from .judge import judge
from .llm import Claude
from .models import Attachment
from .scoring import pretty_phone, score
from .vendors import VendorStore

log = logging.getLogger("doris.pipeline")
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_ct_provider(settings: Settings) -> CTProvider:
    data = json.loads((FIXTURES / "ct_fixture.json").read_text())
    fixture = FixtureCTProvider({k: v for k, v in data.items() if not k.startswith("_")})
    if settings.offline:
        return fixture
    return CrtShProvider(timeout=settings.crtsh_timeout, fallback=fixture)


class Doris:
    def __init__(self, settings: Optional[Settings] = None, *, ct: Optional[CTProvider] = None, llm: Optional[Claude] = None,
                 now: Optional[Callable[[], datetime]] = None, seed: Optional[str | Path] = None):
        self.settings = settings or Settings.from_env()
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.audit = AuditLog(self.settings.db_path)
        self.vendors = VendorStore(self.settings.db_path, audit=self.audit, now=self.now)
        self.ct = ct if ct is not None else default_ct_provider(self.settings)
        self.llm = llm if llm is not None else Claude(self.settings)
        self._lock = threading.RLock()
        if seed:
            self.vendors.seed(seed)

    def close(self) -> None:
        self.vendors.close()
        self.audit.close()

    # --- the main path ------------------------------------------------------------------

    def check(self, raw: bytes | str, *, extra_attachments: Iterable[Attachment] = (), invoice_id: Optional[str] = None) -> dict[str, Any]:
        """Score one incoming message (RFC 822 bytes or forwarded text) and seal the Decision."""
        now = self.now()
        message = ingest(raw, now=now)
        # Warm the Certificate Transparency cache in the background while Claude extracts.
        ct_domains = {d for d in (message.sender_domain, message.outer_sender_domain) if d} | set(self.vendors.known_domains())
        prefetch = [threading.Thread(target=self.ct.certificates, args=(d,), daemon=True) for d in sorted(ct_domains)]
        for t in prefetch:
            t.start()
        message.attachments.extend(extra_attachments)
        docs = extract_documents(message)
        invoice, method = extract(message, docs, self.llm)
        if invoice_id is None or invoice_id == "":
            invoice_id = invoice.invoice_number or "inv_" + message.raw_sha256[:12]
        else:
            invoice_id = str(invoice_id)   # never let a non-string reach the frozen schema

        with self._lock:
            # Intake record: hashes only. Email bodies are never stored after the check.
            self.audit.append("intake", {
                "invoice_id": invoice_id, "input_sha256": message.raw_sha256,
                "attachments": [{"name": a.filename, "sha256": hashlib.sha256(a.data).hexdigest(), "bytes": len(a.data)} for a in message.attachments],
                "extraction": method, "sender_domain": message.sender_domain, "forwarded": message.forwarded,
                "created_at": iso_z(now),
            })
            vendor = self.vendors.match(vendor_claimed=invoice.vendor_claimed, sender_domain=message.sender_domain, payee_name=invoice.payee_name)
            ctx = CheckContext(message=message, docs=docs, invoice=invoice, vendor=vendor,
                               known_domains=self.vendors.known_domains(), settings=self.settings, ct=self.ct, now=now)
            signals = run_checks(ctx)
            judgment = judge(self.llm, ctx, signals)
            signals.append(judgment.signal)
            callback_number, callback_since = vendor.trusted_phone(now, self.settings.on_file_days) if vendor else (None, None)
            verdict = score(signals, llm_risk=judgment.risk, callback_number=callback_number, callback_since=callback_since,
                            vendor=vendor, settings=self.settings)
            # Unreadable attachments force HOLD: never silently drop a PDF the pipeline cannot read.
            doc_errors = [d for d in docs if d.error]
            if doc_errors and verdict.decision == "PAY":
                verdict.decision = "HOLD"
                verdict.risk_score = max(verdict.risk_score, self.settings.hold_threshold)
                names = ", ".join(d.name or "unnamed attachment" for d in doc_errors)
                verdict.reasons.append(f"Attachment {names} could not be read; human review required")
            decision: dict[str, Any] = {
                "invoice_id": invoice_id,
                "created_at": iso_z(now),
                "vendor_claimed": invoice.vendor_claimed,
                "vendor_id": vendor.vendor_id if vendor else None,
                "payee_name": invoice.payee_name,
                "amount": invoice.amount,
                "currency": (invoice.currency or "USD")[:3].upper(),
                "bank_account_new": mask_last4(invoice.bank_account_last4) if (vendor is None or invoice.bank_account_last4 != (vendor.bank_last4 if vendor else None)) else None,
                "bank_account_on_file": mask_last4(vendor.bank_last4) if vendor else None,
                "sender_email": message.sender_email,
                "sender_domain": message.sender_domain,
                "signals": [s.to_dict() for s in signals],
                "risk_score": verdict.risk_score,
                "decision": verdict.decision,
                "reasons": verdict.reasons,
                "callback_number": callback_number,
                "callback_number_on_file_since": iso_z(callback_since) if callback_since else None,
                "ct_timeline": build_ct_timeline(self.ct, message, vendor, invoice_id),
                "llm_rationale": judgment.rationale,
                "supersedes": None,
            }
            return self._seal(decision)

    # --- follow-ups ---------------------------------------------------------------------

    def _already_resolved(self, decision_id: str) -> Optional[str]:
        """Return the decision_id of a follow-up that already supersedes this one, or None."""
        for rec in self.audit.records(kind="decision", newest_first=True):
            if rec.payload.get("supersedes") == decision_id:
                return rec.payload.get("decision_id")
        return None

    def resolve_callback(self, decision_id: str, *, vendor_confirmed: bool, notes: str = "", called: Optional[str] = None) -> dict[str, Any]:
        """Record what the vendor said at the number on file. Denied -> BLOCKED; confirmed -> PAY
        and the new account becomes the account on file (a versioned, audited edit)."""
        with self._lock:
            original = self.get_decision(decision_id)
            if original is None:
                raise KeyError(decision_id)
            follow_up = self._already_resolved(decision_id)
            if follow_up:
                raise ValueError(f"decision {decision_id} already resolved by {follow_up}")
            if original["decision"] != "CALLBACK":
                raise ValueError(f"decision {decision_id} is {original['decision']}, not CALLBACK")
            now = self.now()
            trusted = original["callback_number"]
            if called is not None and called != trusted:
                raise ValueError(f"called {called} does not match the number on file {trusted}")
            called = called or trusted
            since = original.get("callback_number_on_file_since")
            since_text = f" (on file since {since[:10]})" if since else ""
            quote = f': "{notes}"' if notes else ""
            if vendor_confirmed:
                detail = f"Called {pretty_phone(called)}{since_text}. They confirmed the change{quote}"
                headline = f"At the number on file, they confirmed the change{quote}"
                decision, risk = "PAY", 0.1
                if not original["bank_account_new"]:
                    # The vendor confirmed a change but the message carried no account number to pay:
                    # a human enters the account the vendor gave on the call.
                    decision, risk = "HOLD", 0.5
                    headline += "; the message had no new account number, so a human must enter the one given on the call"
                if original["vendor_id"] and original["bank_account_new"]:
                    self.vendors.update_field(original["vendor_id"], "bank_last4", original["bank_account_new"].lstrip("*"),
                                              at=now, source=f"callback confirmed by {pretty_phone(called)}" + (f": {notes}" if notes else ""))
                    # Also update the routing number on file from the original check's evidence.
                    new_routing = None
                    for s in original.get("signals", []):
                        if s.get("name") == "bank_account_changed" and s.get("fired"):
                            new_routing = s.get("evidence", {}).get("new_routing")
                            break
                    if new_routing:
                        self.vendors.update_field(original["vendor_id"], "routing_last4", new_routing,
                                                  at=now, source=f"callback confirmed by {pretty_phone(called)}" + (f": {notes}" if notes else ""))
            else:
                detail = f"Called {pretty_phone(called)}{since_text}. They said{quote}" if notes else f"Called {pretty_phone(called)}{since_text}. They did not confirm the change"
                headline = f"At the number on file, they said{quote}" if notes else "At the number on file, they did not confirm the change"
                decision, risk = "BLOCKED", 0.99
            result = Signal("callback_result", True, "gate", detail, {"vendor_confirmed": vendor_confirmed, "called": called, "notes": notes})
            signals = [s for s in original["signals"] if s["name"] != "callback_result"] + [result.to_dict()]
            reasons = [headline] + [r for r in original["reasons"] if not r.startswith("Doris is calling")]
            return self._seal(self._follow_up(original, now, signals=signals, risk=risk, decision=decision, reasons=reasons))

    def resolve_review(self, decision_id: str, *, approve: bool, reviewer: str, notes: str = "") -> dict[str, Any]:
        """A human's call on an escalation."""
        with self._lock:
            original = self.get_decision(decision_id)
            if original is None:
                raise KeyError(decision_id)
            follow_up = self._already_resolved(decision_id)
            if follow_up:
                raise ValueError(f"decision {decision_id} already resolved by {follow_up}")
            if original["decision"] == "CALLBACK":
                raise ValueError("a CALLBACK is resolved by calling the number on file (callback_result), not by review")
            # A prompt-injection BLOCKED is a hard block: no human review can turn it into PAY.
            fired = {s["name"] for s in original.get("signals", []) if s.get("fired")}
            if approve and "prompt_injection" in fired:
                raise ValueError("prompt_injection BLOCKED is a hard block and cannot be overridden by review")
            now = self.now()
            verb = "approved" if approve else "rejected"
            detail = f"Reviewed by {reviewer or 'a reviewer'}: {verb}" + (f" ({notes})" if notes else "")
            result = Signal("human_review", True, "gate", detail, {"approve": approve, "reviewer": reviewer, "notes": notes})
            signals = [s for s in original["signals"] if s["name"] != "human_review"] + [result.to_dict()]
            decision = "PAY" if approve else "BLOCKED"
            risk = 0.1 if approve else max(float(original["risk_score"]), 0.9)
            drop = ("Human review required", "Risk score ", "No phone number on file")
            reasons = [f"Human review by {reviewer or 'a reviewer'}: {verb}" + (f". {notes}" if notes else "")] + \
                      [r for r in original["reasons"] if not r.startswith(drop)]
            return self._seal(self._follow_up(original, now, signals=signals, risk=round(risk, 2), decision=decision, reasons=reasons))

    # --- reads --------------------------------------------------------------------------

    def get_decision(self, decision_id: str) -> Optional[dict[str, Any]]:
        for rec in self.audit.records(kind="decision", newest_first=True):
            if rec.payload.get("decision_id") == decision_id:
                return {**rec.payload, "audit_hash": rec.hash}
        return None

    def list_decisions(self, limit: int = 50) -> list[dict[str, Any]]:
        return [{**rec.payload, "audit_hash": rec.hash} for rec in self.audit.records(kind="decision", newest_first=True, limit=limit)]

    def verify(self):
        return self.audit.verify()

    # --- internals ----------------------------------------------------------------------

    def _follow_up(self, original: dict[str, Any], now: datetime, *, signals, risk, decision, reasons) -> dict[str, Any]:
        follow = {k: v for k, v in original.items() if k not in ("seq", "prev_hash", "audit_hash", "decision_id")}
        follow.update({"created_at": iso_z(now), "signals": signals, "risk_score": risk, "decision": decision,
                       "reasons": reasons, "supersedes": original["decision_id"]})
        return follow

    def _seal(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Assign the id, append to the chain, write the outbox file."""
        seq = self.audit.head()[0] + 1
        basis = f"{decision['invoice_id']}|{seq}|{decision['created_at']}|{decision.get('supersedes') or ''}"
        decision["decision_id"] = "dec_" + hashlib.sha256(basis.encode()).hexdigest()[:12]
        rec = self.audit.append("decision", decision)
        decision["seq"], decision["prev_hash"], decision["audit_hash"] = rec.seq, rec.prev_hash, rec.hash
        try:
            self.settings.outbox_dir.mkdir(parents=True, exist_ok=True)
            (self.settings.outbox_dir / f"{decision['decision_id']}.json").write_text(json.dumps(decision, indent=2, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("could not write outbox file: %s", exc)
        return decision
