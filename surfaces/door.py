"""The forward-to-check door: anyone forwards a suspicious email to check@ and gets back
safe / suspicious / scam with reasons, plus a thumbs up/down that labels the case.

Mail to the AP inbox (DORIS_AP_ADDRESS) is checked the same way but gets no reply: it lands on the
dashboard and, for a CALLBACK, Doris phones the vendor. The door itself never places a call, so
bounty traffic can't ring the judges' table.

Fuse: a size cap, a per-sender hourly limit, a global per-minute limit, and a freeze time after which
the door stops checking (it answers with a short note instead) so a flood can't reach the demo.
Bodies are never stored: the reply carries Doris's reasons, and the store keeps a sender hash.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path
from typing import Optional

from .client import DetectorError

log = logging.getLogger("doris.door")
STRONG = {"bank_account_changed", "lookalike_domain", "payee_name_mismatch", "new_domain_certificate", "vendor_contact_changed",
          "amount_anomaly", "prompt_injection"}
AP_ONLY_REASONS = ("Doris is calling", "First-time vendor", "Human review required", "No phone number on file")


def verdict(decision: dict) -> str:
    """safe / suspicious / scam for someone who forwarded an email. The new-vendor gate is an AP rule
    (every unknown sender is held), so it does not count against the message here."""
    fired = {s["name"] for s in decision.get("signals") or [] if s.get("fired")}
    risk = float(decision.get("risk_score") or 0)
    if (decision.get("decision") == "BLOCKED" or risk >= 0.7 or "prompt_injection" in fired
            or {"lookalike_domain", "bank_account_changed"} <= fired):
        return "scam"
    if risk >= 0.3 or fired & STRONG:
        return "suspicious"
    return "safe"


HEADLINE = {
    "scam": "SCAM. Don't pay it, don't reply to it, and don't use any phone number or link in it.",
    "suspicious": "SUSPICIOUS. Before paying, call the sender on a number you already had, not one from this email.",
    "safe": "LOOKS SAFE. Doris found nothing wrong.",
}


def reply_text(decision: dict, v: str, feedback_base: str) -> str:
    reasons = [r for r in decision.get("reasons") or [] if not r.startswith(AP_ONLY_REASONS)][:4]
    lines = [f"Doris says: {HEADLINE[v]}", ""]
    if reasons:
        lines += ["Why:"] + [f"  - {r}" for r in reasons] + [""]
    if any(s.get("name") == "bank_account_changed" and s.get("fired") for s in decision.get("signals") or []):
        lines += ["It asks to send money to a changed bank account. That's how nearly every one of these scams works.", ""]
    if decision.get("vendor_id") is None:
        lines += ["Doris has no record of this sender, so she judged the message itself, not your vendor's records.", ""]
    if decision.get("llm_rationale"):
        lines += [f"Claude: {decision['llm_rationale']}", ""]
    lines += [f"Was Doris right?  Yes: {feedback_base}/up   No: {feedback_base}/down", "",
              "Doris only trusts what's on file. She checked the message and what Doris has on file; "
              "we don't store email bodies after the check.", f"Reference: {decision.get('decision_id')}"]
    return "\n".join(lines)


# --- reading what arrived --------------------------------------------------------

def postmark_to_raw(payload: dict) -> bytes:
    """Postmark's inbound JSON as an RFC 822 message (its RawEmail when sent, which keeps every header)."""
    if payload.get("RawEmail"):
        return str(payload["RawEmail"]).encode("utf-8", "replace")
    msg = EmailMessage()
    msg["From"] = payload.get("From") or ""
    msg["To"] = payload.get("To") or ""
    msg["Subject"] = payload.get("Subject") or ""
    if payload.get("Date"):
        msg["Date"] = payload["Date"]
    for h in payload.get("Headers") or []:
        if h.get("Name", "").lower() in ("auto-submitted", "precedence", "reply-to"):
            msg[h["Name"]] = h.get("Value", "")
    msg.set_content(payload.get("TextBody") or "")
    if payload.get("HtmlBody"):
        msg.add_alternative(payload["HtmlBody"], subtype="html")
    for a in payload.get("Attachments") or []:
        ctype = (a.get("ContentType") or "application/octet-stream").split("/", 1)
        msg.add_attachment(base64.b64decode(a.get("Content") or ""), maintype=ctype[0], subtype=ctype[1] if len(ctype) > 1 else "octet-stream",
                           filename=a.get("Name") or "attachment")
    return msg.as_bytes()


def envelope(raw: bytes) -> dict:
    msg = message_from_bytes(raw, policy=policy.compat32)
    name, addr = parseaddr(msg.get("From") or "")
    to = [a.lower() for _, a in getaddresses(msg.get_all("To", []) + msg.get_all("Cc", []) + msg.get_all("Delivered-To", []))]
    auto = (msg.get("Auto-Submitted") or "no").strip().lower() != "no" or (msg.get("Precedence") or "").lower() in ("bulk", "junk", "list")
    local = addr.lower().split("@")[0]
    return {"from": addr.lower(), "name": name, "to": to, "subject": str(msg.get("Subject") or ""),
            "automated": auto or local in ("mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply")}


# --- the door --------------------------------------------------------------------

class Door:
    def __init__(self, app):
        self.app = app
        self.outbox = app.settings.data_dir / "outbox"

    def _hash(self, sender: str) -> str:
        return hashlib.sha256(sender.strip().lower().encode()).hexdigest()

    def frozen(self) -> bool:
        at = self.app.settings.bounty_freeze_at
        if not at:
            return False
        try:
            t = datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            return True        # a freeze time we can't read fails closed
        return datetime.now(timezone.utc) >= (t if t.tzinfo else t.replace(tzinfo=timezone.utc))

    def accept(self, raw: bytes) -> str:
        """Decide what to do with one inbound email. Returns what happened (for logs and tests)."""
        s = self.app.settings
        env = envelope(raw)
        sender = env["from"]
        if not sender or "@" not in sender:
            return "dropped: no sender"
        if s.door_from and sender == s.door_from.lower():
            return "dropped: our own mail"
        ap = s.extra.get("ap_address") or ""
        to_ap = bool(ap) and ap.lower() in env["to"]
        if self.frozen() and not to_ap:
            if not env["automated"]:
                self.send(sender, f"Re: {env['subject']}", "Doris is resting before her demo, so the bounty is closed and she isn't "
                          "checking new emails right now. Thanks for playing.", key=f"frozen-{secrets.token_hex(4)}")
            return "frozen"
        now = time.time()
        h = self._hash(sender)
        if self.app.store.count_checks(since=now - 3600, sender_hash=h) >= s.door_per_sender_per_hour:
            return "dropped: sender rate limit"
        if self.app.store.count_checks(since=now - 60) >= s.door_per_minute:
            return "dropped: global rate limit"
        token = secrets.token_urlsafe(16)
        self.app.store.add_check(token, None, "pending", h)       # counts against the limits right away
        try:
            decision = self.app.client.check_raw(raw)
        except DetectorError as exc:
            log.warning("door check failed: %s", exc)
            self.app.store.set_verdict(token, "error", None)
            if not env["automated"] and not to_ap:
                self.send(sender, f"Re: {env['subject']}", "Doris couldn't check this one just now. Please try again in a few minutes.", key=token)
            return "error"
        if to_ap:
            self.app.store.set_verdict(token, "ap", decision["decision_id"])
            self.app.maybe_auto_call(decision)
            return f"ap: {decision['decision']}"
        v = verdict(decision)
        self.app.store.set_verdict(token, v, decision["decision_id"])
        if env["automated"]:
            return f"checked, no reply to automated mail: {v}"
        tag = {"scam": "SCAM", "suspicious": "SUSPICIOUS", "safe": "looks safe"}[v]
        self.send(sender, f"[Doris: {tag}] {env['subject']}"[:200], reply_text(decision, v, f"{s.public_base_url}/feedback/{token}"), key=token)
        return f"replied: {v}"

    def send(self, to: str, subject: str, body: str, *, key: str) -> None:
        s = self.app.settings
        if s.postmark_token and s.door_from:
            req = urllib.request.Request("https://api.postmarkapp.com/email", method="POST",
                                         data=json.dumps({"From": s.door_from, "To": to, "Subject": subject, "TextBody": body,
                                                          "MessageStream": "outbound"}).encode(),
                                         headers={"X-Postmark-Server-Token": s.postmark_token, "Accept": "application/json",
                                                  "Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=20).read()
                return
            except (urllib.error.URLError, OSError) as exc:
                log.warning("Postmark send failed, keeping the reply in the outbox: %s", exc)
        msg = EmailMessage()
        msg["From"] = formataddr(("Doris", s.door_from or "check@doris.invalid"))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / f"{key}.eml").write_bytes(msg.as_bytes())


def wire(app) -> Door:
    door = Door(app)
    app.door = door

    def inbound(h, token):
        s = app.settings
        if not s.inbound_token or not hmac.compare_digest(token, s.inbound_token):
            return h.send(404, b"not found", "text/plain")
        length = int(h.headers.get("Content-Length") or 0)
        if length <= 0:
            return h.send(400, b"empty", "text/plain")
        if length > s.door_max_bytes:
            return h.send(413, b"too large", "text/plain")
        body = h.rfile.read(length)
        ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            raw = postmark_to_raw(json.loads(body)) if ctype == "application/json" else body
        except (ValueError, TypeError, KeyError, base64.binascii.Error):
            return h.send(400, b"unreadable payload", "text/plain")
        # Answer the mail service now; Doris replies on her own time (the doc's promise: under a minute).
        threading.Thread(target=lambda: log.info("door: %s", door.accept(raw)), daemon=True).start()
        h.send(202, b"accepted", "text/plain")

    def feedback(h, token, value):
        ok = app.store.set_feedback(token, value)
        from . import render
        msg = "Thanks. That label helps Doris get better." if ok else "That link has expired."
        h.send(200 if ok else 404, render.page("Thanks", f'<section class="panel"><p style="margin:0">{msg}</p></section>'))

    app.route("POST", r"/inbound/([A-Za-z0-9_\-]{16,64})", inbound)
    app.route("GET", r"/feedback/([A-Za-z0-9_\-]{16,32})/(up|down)", feedback)
    return door
