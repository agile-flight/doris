"""The forward-to-check door: anyone forwards a suspicious email to check@ and gets back
safe / suspicious / scam with reasons, plus a Yes/No link that labels the case.

The AP inbox is a separate webhook with its own secret (DORIS_AP_INBOUND_TOKEN), never chosen by
headers the sender writes. AP mail is checked and shown on the dashboard, auto-called when it is a
CALLBACK, and never replied to. The check@ door never places a call.

Fuse: a size cap, per-sender and global limits taken atomically, and a freeze time after which the
door stops checking (at most one short note per sender per hour). Every reply carries
Auto-Submitted: auto-replied so autoresponders don't loop. Email bodies are never stored.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from .client import DetectorError

log = logging.getLogger("doris.door")
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_\-]{16,64}")
STRONG = {"bank_account_changed", "lookalike_domain", "payee_name_mismatch", "new_domain_certificate", "vendor_contact_changed",
          "amount_anomaly", "prompt_injection"}
AP_ONLY_REASONS = ("Doris is calling", "First-time vendor", "Human review required", "No phone number on file")


def _signal(decision: dict, name: str) -> dict:
    return next((s for s in decision.get("signals") or [] if s.get("name") == name), {}) or {}


def verdict(decision: dict) -> str:
    """safe / suspicious / scam for someone who forwarded an email.

    The new-vendor gate is an AP rule (every unknown sender is held), so it doesn't count here. A stranger
    can never trip "account differs from the one on file", so for strangers the claim itself counts: an
    unknown sender asking to pay a new account, or pressing for speed, is at least suspicious."""
    fired = {s["name"] for s in decision.get("signals") or [] if s.get("fired")}
    risk = float(decision.get("risk_score") or 0)
    llm = _signal(decision, "llm_judgment").get("evidence") or {}
    llm_risk = float(llm["risk"]) if llm.get("status") == "ok" and isinstance(llm.get("risk"), (int, float)) else None
    stranger = decision.get("vendor_id") is None
    claimed_change = bool((_signal(decision, "bank_account_changed").get("evidence") or {}).get("claimed_change"))
    pressure = "urgency_language" in fired
    if (decision.get("decision") == "BLOCKED" or risk >= 0.7 or "prompt_injection" in fired
            or {"lookalike_domain", "bank_account_changed"} <= fired or (llm_risk is not None and llm_risk >= 0.7)
            or (stranger and claimed_change and (pressure or fired & {"lookalike_domain", "new_domain_certificate"}))):
        return "scam"
    if (risk >= 0.3 or fired & STRONG or (llm_risk is not None and llm_risk >= 0.4)
            or (stranger and (claimed_change or pressure))):
        return "suspicious"
    return "safe"


HEADLINE = {
    "scam": "SCAM. Don't pay it, don't reply to it, and don't use any phone number or link in it.",
    "suspicious": "SUSPICIOUS. Before paying, call the sender on a number you already had, not one from this email.",
    "safe": "NO RED FLAGS FOUND.",
}


def reply_text(decision: dict, v: str, feedback_base: str) -> str:
    reasons = [r for r in decision.get("reasons") or [] if not r.startswith(AP_ONLY_REASONS)][:4]
    lines = [f"Doris says: {HEADLINE[v]}", ""]
    if reasons:
        lines += ["What she noticed:"] + [f"  - {r}" for r in reasons] + [""]
    bank = _signal(decision, "bank_account_changed")
    if bank.get("fired") or (bank.get("evidence") or {}).get("claimed_change"):
        lines += ["It asks to send money to a new or changed bank account. That's how nearly every one of these scams works.", ""]
    if decision.get("vendor_id") is None:
        lines += ["Doris has no record of this sender, so she judged the message itself, not your vendor's records.", ""]
    llm = _signal(decision, "llm_judgment").get("evidence") or {}
    if decision.get("llm_rationale"):
        lines += [f"Claude: {decision['llm_rationale']}", ""]
    elif v == "safe" and llm.get("status") != "ok":
        lines += ["Her AI read of the message didn't run this time, so this is the rules talking. Stay careful with any payment request.", ""]
    lines += [f"Was Doris right?  Yes: {feedback_base}/up   No: {feedback_base}/down", "",
              "Doris only trusts what's on file. We don't store email bodies after the check.",
              f"Reference: {decision.get('decision_id')}"]
    return "\n".join(lines)


# --- reading what arrived --------------------------------------------------------

def postmark_to_raw(payload: dict) -> bytes:
    """Postmark's inbound JSON as an RFC 822 message (its RawEmail when sent, which keeps every header)."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    if payload.get("RawEmail"):
        return str(payload["RawEmail"]).encode("utf-8", "replace")
    msg = EmailMessage()
    msg["From"] = payload.get("From") or ""
    msg["To"] = payload.get("To") or ""
    msg["Subject"] = payload.get("Subject") or ""
    if payload.get("Date"):
        msg["Date"] = payload["Date"]
    for h in payload.get("Headers") or []:
        if str(h.get("Name", "")).lower() in ("auto-submitted", "precedence", "reply-to"):
            msg[h["Name"]] = h.get("Value", "")
    msg.set_content(payload.get("TextBody") or "")
    if payload.get("HtmlBody"):
        msg.add_alternative(payload["HtmlBody"], subtype="html")
    for a in payload.get("Attachments") or []:
        ctype = (a.get("ContentType") or "application/octet-stream").split("/", 1)
        msg.add_attachment(base64.b64decode(a.get("Content") or ""), maintype=ctype[0], subtype=ctype[1] if len(ctype) > 1 else "octet-stream",
                           filename=a.get("Name") or "attachment")
    return msg.as_bytes()


def _header(msg, name: str) -> str:
    try:
        value = msg.get(name)
        return re.sub(r"[\r\n\t]+\s*", " ", str(value)).strip() if value is not None else ""
    except Exception:  # noqa: BLE001  (malformed header: treat as absent)
        return ""


def envelope(raw: bytes) -> dict:
    msg = message_from_bytes(raw, policy=policy.default)
    sender = ""
    try:
        addrs = msg["From"].addresses if msg["From"] is not None else ()
        sender = addrs[0].addr_spec if addrs else ""
    except Exception:  # noqa: BLE001
        sender = ""
    if not sender or "@" not in sender:
        sender = parseaddr(_header(msg, "From"))[1]
    auto = _header(msg, "Auto-Submitted").lower() not in ("", "no") or _header(msg, "Precedence").lower() in ("bulk", "junk", "list")
    sender = sender.strip().lower()
    local = sender.split("@")[0]
    return {"from": sender, "subject": _header(msg, "Subject")[:150],
            "automated": auto or local in ("mailer-daemon", "postmaster", "noreply", "no-reply", "donotreply")}


# --- the door --------------------------------------------------------------------

class Door:
    def __init__(self, app):
        self.app = app
        self.outbox = app.settings.data_dir / "outbox"

    @staticmethod
    def _hash(sender: str) -> str:
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

    def accept(self, raw: bytes, mode: str = "door") -> str:
        """Handle one inbound email ('door' = check@, 'ap' = the AP inbox). Returns what happened."""
        s = self.app.settings
        env = envelope(raw)
        sender = env["from"]
        if not sender or "@" not in sender:
            return "dropped: no sender"
        if s.door_from and sender == s.door_from.lower():
            return "dropped: our own mail"
        h = self._hash(sender)
        token = secrets.token_urlsafe(16)
        if mode == "door" and self.frozen():
            if env["automated"]:
                return "frozen"
            if self.app.store.reserve_check(token, h, "frozen", per_sender_hour=1, per_minute=s.door_per_minute):
                return "frozen: limited"
            self.send(sender, f"Re: {env['subject']}", "Doris is resting before her demo, so the bounty is closed and she isn't "
                      "checking new emails right now. Thanks for playing.", key=token)
            return "frozen"
        limited = self.app.store.reserve_check(token, h, "pending", per_sender_hour=s.door_per_sender_per_hour, per_minute=s.door_per_minute)
        if limited:
            return f"dropped: {limited}"
        try:
            decision = self.app.client.check_raw(raw)
        except DetectorError as exc:
            log.warning("door check failed: %s", exc)
            self.app.store.set_verdict(token, "error", None)
            if mode == "door" and not env["automated"]:
                self.send(sender, f"Re: {env['subject']}", "Doris couldn't check this one just now. Please try again in a few minutes.", key=token)
            return "error"
        if mode == "ap":
            self.app.store.set_verdict(token, "ap", decision["decision_id"])
            self.app.maybe_auto_call(decision)
            return f"ap: {decision['decision']}"
        v = verdict(decision)
        self.app.store.set_verdict(token, v, decision["decision_id"])
        if env["automated"]:
            return f"checked, no reply to automated mail: {v}"
        tag = {"scam": "SCAM", "suspicious": "SUSPICIOUS", "safe": "no red flags"}[v]
        self.send(sender, f"[Doris: {tag}] {env['subject']}", reply_text(decision, v, f"{s.public_base_url}/feedback/{token}"), key=token)
        return f"replied: {v}"

    def send(self, to: str, subject: str, body: str, *, key: str) -> None:
        s = self.app.settings
        subject = re.sub(r"[\r\n]+", " ", subject)[:200]
        if s.postmark_token and s.door_from:
            req = urllib.request.Request("https://api.postmarkapp.com/email", method="POST",
                                         data=json.dumps({"From": s.door_from, "To": to, "Subject": subject, "TextBody": body,
                                                          "MessageStream": "outbound",
                                                          "Headers": [{"Name": "Auto-Submitted", "Value": "auto-replied"}]}).encode(),
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
        msg["Auto-Submitted"] = "auto-replied"
        msg.set_content(body)
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / f"{key}.eml").write_bytes(msg.as_bytes())


def wire(app) -> Door:
    from . import render
    from .app import content_length
    door = Door(app)
    app.door = door
    for name, value in (("DORIS_INBOUND_TOKEN", app.settings.inbound_token), ("DORIS_AP_INBOUND_TOKEN", app.settings.extra.get("ap_inbound_token", ""))):
        if value and not TOKEN_SHAPE.fullmatch(value):
            log.warning("%s must be 16-64 letters, digits, '-' or '_'; that webhook stays closed", name)

    def receive(h, token, expected, mode):
        if not expected or not TOKEN_SHAPE.fullmatch(expected) or not hmac.compare_digest(token, expected):
            return h.send(404, b"not found", "text/plain")             # before reading any body
        length = content_length(h)
        if length <= 0:
            return h.send(400, b"missing or bad Content-Length", "text/plain")
        if length > app.settings.door_max_bytes:
            return h.send(413, b"too large", "text/plain")
        body = h.rfile.read(length)
        ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        try:
            raw = postmark_to_raw(json.loads(body)) if ctype == "application/json" else body
        except (ValueError, TypeError, KeyError, AttributeError, base64.binascii.Error):
            return h.send(400, b"unreadable payload", "text/plain")

        def run():
            try:
                log.info("door (%s): %s", mode, door.accept(raw, mode))
            except Exception:  # noqa: BLE001  (a bad email must never kill the door quietly)
                log.exception("door failed on one email")
        threading.Thread(target=run, daemon=True).start()
        h.send(202, b"accepted", "text/plain")

    def inbound(h, token):
        receive(h, token, app.settings.inbound_token, "door")

    def inbound_ap(h, token):
        receive(h, token, app.settings.extra.get("ap_inbound_token", ""), "ap")

    def feedback_page(h, token, value):
        word = "right" if value == "up" else "wrong"
        h.send(200, render.page("Was Doris right?", f'<section class="panel"><form method="post"><p style="margin-top:0">Tell Doris she was '
                                f'<strong>{word}</strong> about this email?</p><button class="primary">Yes, record it</button></form></section>'))

    def feedback_record(h, token, value):
        ok = app.store.set_feedback(token, value)
        msg = "Thanks. That label helps Doris get better." if ok else "That link has already been used or has expired."
        h.send(200 if ok else 404, render.page("Thanks", f'<section class="panel"><p style="margin:0">{msg}</p></section>'))

    app.route("POST", r"/inbound/([A-Za-z0-9_\-]{16,64})", inbound)
    app.route("POST", r"/inbound-ap/([A-Za-z0-9_\-]{16,64})", inbound_ap)
    app.route("GET", r"/feedback/([A-Za-z0-9_\-]{16,32})/(up|down)", feedback_page)
    app.route("POST", r"/feedback/([A-Za-z0-9_\-]{16,32})/(up|down)", feedback_record)
    return door
