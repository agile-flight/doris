"""The callback: Doris phones the vendor at the number on file and asks one question.

The number is the Decision's callback_number, which the detector only sets when the number has
been on file, unchanged, for 90+ days. Nothing here ever takes a number from the email. Every
webhook URL carries a random single-use token, so nobody can post a forged "yes".
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from .voice import Voice

log = logging.getLogger("doris.callback")
TOKEN = r"([A-Za-z0-9_\-]{20,64})"
LIVE = ("starting", "queued", "initiated", "ringing", "in-progress")


class TelephonyError(Exception):
    pass


# --- what Doris says ---------------------------------------------------------------

def spaced(digits: Optional[str]) -> str:
    d = re.sub(r"\D", "", digits or "")
    return " ".join(d) if d else "a new account"


def lines(decision: dict, company: str) -> dict[str, str]:
    last4 = spaced((decision.get("bank_account_new") or "")[-4:])
    ask = f"ending in {last4}" if last4 != "a new account" else "at a new bank"
    return {
        "prompt": (f"Hi, this is Doris from accounts payable at {company}. We got an email asking us to send your payments "
                   f"to a different bank account, {ask}. Before we pay anything: did your company change its bank account? "
                   "Please say yes or no. Or press 1 for yes, 2 for no."),
        "reprompt": "Sorry, hon, I didn't catch that. Did your company change its bank account? Say yes or no, or press 1 for yes, 2 for no.",
        "denied": "Thank you. Then that email wasn't from you. I'm stopping the payment right now and flagging it as fraud. Bye now.",
        "confirmed": "Thanks for confirming. I'll update our records and release the payment. Bye now.",
        "unclear": "No problem. I'll hold the payment and someone from our team will call you back. Goodbye.",
    }


_NO = re.compile(r"\b(no|nope|never|didn'?t|did not|haven'?t|have not|hasn'?t|not us|wasn'?t us|not (?:from )?us|fraud|scam|fake)\b")
_YES = re.compile(r"\b(yes|yeah|yep|yup|correct|we did|that'?s right|that is right|confirm(?:ed)?|affirmative)\b")


def classify(speech: str = "", digits: str = "") -> Optional[str]:
    """'denied', 'confirmed' or None. When both appear, denial wins: money only moves on a clear yes."""
    if digits:
        return {"1": "confirmed", "2": "denied"}.get(digits.strip()[:1])
    text = (speech or "").lower()
    if _NO.search(text):
        return "denied"
    if _YES.search(text):
        return "confirmed"
    return None


# --- the call ----------------------------------------------------------------------

class CallManager:
    def __init__(self, app, voice: Optional[Voice] = None):
        self.app = app
        s = app.settings
        self.voice = voice or Voice(s.cartesia_api_key, s.cartesia_voice, s.cartesia_model, s.data_dir / "audio")
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return self.app.settings.public_base_url.rstrip("/")

    def start(self, decision: dict) -> str:
        """Place the call. Returns the call token. Refuses anything but an unresolved CALLBACK."""
        s = self.app.settings
        if decision.get("decision") != "CALLBACK" or not decision.get("callback_number"):
            raise TelephonyError("only a CALLBACK decision with a number on file can be called")
        if not s.twilio_ready:
            raise TelephonyError("telephony is not configured (Twilio credentials, TWILIO_FROM and DORIS_PUBLIC_URL)")
        with self._lock:
            current = self.app.store.latest_call(decision["decision_id"])
            if current and current["status"] in LIVE:
                return current["token"]
            token = secrets.token_urlsafe(24)
            self.app.store.add_call(token, decision["decision_id"], decision["callback_number"])
        script = lines(decision, s.company)
        self.voice.render(script["prompt"])                       # ready before the phone rings
        threading.Thread(target=lambda: [self.voice.render(script[k]) for k in ("reprompt", "denied", "confirmed", "unclear")],
                         daemon=True).start()
        try:
            result = self._twilio_create(to=decision["callback_number"], url=f"{self.base}/twilio/{token}/voice",
                                         status_url=f"{self.base}/twilio/{token}/status")
        except TelephonyError as exc:
            self.app.store.update_call(token, status="failed", notes=str(exc)[:300])
            raise
        self.app.store.update_call(token, status=result.get("status") or "queued", twilio_sid=result.get("sid"))
        return token

    def _twilio_create(self, *, to: str, url: str, status_url: str) -> dict:
        s = self.app.settings
        form = urllib.parse.urlencode([("To", to), ("From", s.twilio_from), ("Url", url), ("Method", "POST"),
                                       ("StatusCallback", status_url), ("StatusCallbackMethod", "POST"), ("Timeout", "25"),
                                       *[("StatusCallbackEvent", e) for e in ("initiated", "ringing", "answered", "completed")]]).encode()
        auth = base64.b64encode(f"{s.twilio_api_key}:{s.twilio_api_secret}".encode()).decode()
        req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Calls.json", data=form,
                                     method="POST", headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("message") or e.reason
            except ValueError:
                msg = e.reason
            raise TelephonyError(f"Twilio refused the call: {msg}") from None
        except (urllib.error.URLError, OSError) as e:
            raise TelephonyError(f"Twilio not reachable: {e}") from None

    # --- TwiML ----------------------------------------------------------------------

    def _speak(self, text: str) -> str:
        name = self.voice.render(text)
        if name:
            return f"<Play>{xml_escape(f'{self.base}/audio/{name}')}</Play>"
        return f'<Say voice="Polly.Joanna">{xml_escape(text)}</Say>'

    def _gather(self, token: str, text: str) -> str:
        action = xml_escape(f"{self.base}/twilio/{token}/gather")
        return (f'<Gather input="speech dtmf" numDigits="1" timeout="6" speechTimeout="auto" language="en-US" '
                f'hints="yes, no, never, we never changed our bank" action="{action}" method="POST">{self._speak(text)}</Gather>'
                f'<Redirect method="POST">{action}</Redirect>')

    def voice_twiml(self, token: str) -> Optional[str]:
        call = self.app.store.call(token)
        if not call:
            return None
        if call.get("result"):
            return "<Response><Hangup/></Response>"
        decision = self.app.client.get(call["decision_id"])
        self.app.store.update_call(token, status="in-progress")
        return f"<Response>{self._gather(token, lines(decision, self.app.settings.company)['prompt'])}</Response>"

    def gather_twiml(self, token: str, form: dict) -> Optional[str]:
        call = self.app.store.call(token)
        if not call:
            return None
        if call.get("result"):                                      # single use: a replay changes nothing
            return "<Response><Hangup/></Response>"
        decision = self.app.client.get(call["decision_id"])
        script = lines(decision, self.app.settings.company)
        speech = (form.get("SpeechResult") or "").strip()[:200]
        digits = (form.get("Digits") or "").strip()[:2]
        outcome = classify(speech, digits)
        if outcome is None:
            if call["attempts"] < 1:
                self.app.store.update_call(token, attempts=call["attempts"] + 1)
                return f"<Response>{self._gather(token, script['reprompt'])}</Response>"
            self.app.store.update_call(token, status="inconclusive", notes=speech or "no answer")
            return f"<Response>{self._speak(script['unclear'])}<Hangup/></Response>"
        said = speech or (f"pressed {digits}" if digits else "")
        self.app.client.callback_result(call["decision_id"], confirmed=outcome == "confirmed",
                                        notes=said or outcome, called=decision.get("callback_number"))
        self.app.store.update_call(token, status="completed", result=said or outcome)
        return f"<Response>{self._speak(script[outcome])}<Hangup/></Response>"

    def status(self, token: str, form: dict) -> bool:
        call = self.app.store.call(token)
        if not call:
            return False
        status = (form.get("CallStatus") or "").strip()[:20]
        if status and not (call.get("result") or call["status"] == "inconclusive"):
            self.app.store.update_call(token, status=status)
        return True


def _form(handler) -> dict:
    length = min(int(handler.headers.get("Content-Length") or 0), 50_000)
    raw = handler.rfile.read(length).decode("utf-8", "replace") if length else ""
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def wire(app) -> CallManager:
    manager = CallManager(app)
    app.callbacks = manager
    xml = "text/xml; charset=utf-8"

    def voice(h, token):
        twiml = manager.voice_twiml(token)
        h.send(200, twiml, xml) if twiml else h.send(404, b"unknown call", "text/plain")

    def gather(h, token):
        twiml = manager.gather_twiml(token, _form(h))
        h.send(200, twiml, xml) if twiml else h.send(404, b"unknown call", "text/plain")

    def status(h, token):
        ok = manager.status(token, _form(h))
        h.send(204 if ok else 404, b"", "text/plain")

    def audio(h, name):
        p = manager.voice.path(name)
        h.send(200, p.read_bytes(), "audio/wav") if p else h.send(404, b"no such audio", "text/plain")

    app.route("POST", rf"/twilio/{TOKEN}/voice", voice)
    app.route("GET", rf"/twilio/{TOKEN}/voice", voice)
    app.route("POST", rf"/twilio/{TOKEN}/gather", gather)
    app.route("POST", rf"/twilio/{TOKEN}/status", status)
    app.route("GET", r"/audio/([0-9a-f]{32}\.wav)", audio)
    return manager
