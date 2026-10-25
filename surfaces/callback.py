"""The callback: Doris phones the vendor at the number on file and asks one question.

The number is the Decision's callback_number, which the detector only sets when the number has been
on file, unchanged, for 90+ days. Nothing here ever takes a number from the email.

Money moves only on a clear yes, said twice (or 1 pressed twice): a hedge ("I can't confirm that"),
a question, or silence is unclear, and an unclear call leaves the payment frozen. Any denial wins.
Speech is only heard after Doris finishes asking, so a pickup greeting can't answer for the vendor.

Every webhook URL carries a random token, and only the newest live call for a decision is accepted:
an old call, a finished call, or a replay changes nothing.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

from .client import DetectorError
from .voice import Voice

log = logging.getLogger("doris.callback")
TOKEN = r"([A-Za-z0-9_\-]{20,64})"
LIVE = ("starting", "queued", "initiated", "ringing", "answered", "in-progress")
TERMINAL = ("completed", "busy", "no-answer", "failed", "canceled")
STALE_SECONDS = 120
SORRY = '<Response><Say voice="Polly.Joanna">Sorry, something went wrong on our end. We will call you back.</Say><Hangup/></Response>'


class TelephonyError(Exception):
    pass


# --- what Doris says and hears -------------------------------------------------------

def spaced(digits: Optional[str]) -> str:
    d = re.sub(r"\D", "", digits or "")
    return " ".join(d) if d else ""


def lines(decision: dict, company: str) -> dict[str, str]:
    last4 = spaced((decision.get("bank_account_new") or "")[-4:])
    ask = f"ending in {last4}" if last4 else "at a new bank"
    return {
        "prompt": (f"Hi, this is Doris from accounts payable at {company}. We got an email asking us to send your payments "
                   f"to a different bank account, {ask}. Before we pay anything: did your company change its bank account? "
                   "Please say yes or no. Or press 1 for yes, 2 for no."),
        "reprompt": "Sorry, hon, I didn't catch that. Did your company change its bank account? Say yes or no, or press 1 for yes, 2 for no.",
        "confirm": (f"Just to be sure, hon: your company did change its bank account, to the one {ask}? "
                    "Say yes again, or press 1. If not, say no, or press 2."),
        "denied": "Thank you. Then that email wasn't from you. I'm stopping the payment right now and flagging it as fraud. Bye now.",
        "confirmed": "Thanks for confirming. I'll update our records and release the payment. Bye now.",
        "unclear": "No problem. I'll hold the payment and someone from our team will call you back. Goodbye.",
        "handled": "Thanks for picking up. This one has already been taken care of, so there's nothing you need to do. Bye now.",
        "verify": ("One last check, hon, so I know it's really you: what was the amount of the last payment we sent you? "
                   "You can say it, or type it in whole dollars and press pound."),
        "verify_failed": "Thanks. I couldn't match that to our records, so I'll hold the payment and someone from our team will follow up. Goodbye.",
    }


_SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
                                      "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


def words_to_number(text: str) -> Optional[int]:
    """'four thousand six hundred fifty' -> 4650; also the spoken 'forty six fifty' -> 4650."""
    total, current, seen = 0, 0, False
    for tok in re.findall(r"[a-z]+", text.lower()):
        if tok in _SMALL:
            current += _SMALL[tok]
        elif tok in _TENS:
            if 0 < current < 100 and current % 10 and not total:     # "forty six | fifty": hundreds then tens
                current = current * 100
            current += _TENS[tok]
        elif tok == "hundred":
            current = (current or 1) * 100
        elif tok == "thousand":
            total += (current or 1) * 1000
            current = 0
        elif tok in ("and", "a", "dollars", "dollar", "bucks", "uh", "um", "it", "was", "about", "like"):
            continue
        elif tok in ("point", "cents"):
            break
        else:
            if seen:
                break
            continue
        seen = True
    return total + current if seen else None


def spoken_amount(speech: str = "", digits: str = "") -> Optional[float]:
    """The dollar amount someone said or typed, or None."""
    if digits:
        d = re.sub(r"\D", "", digits)
        return float(d) if d else None
    text = (speech or "").lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d{1,2})?)\s*thousand(?:\s*(?:and\s*)?(\d+))?", text)
    if m:
        return float(m.group(1)) * 1000 + float(m.group(2) or 0)
    m = re.search(r"\d+(?:\.\d{1,2})?", text)
    if m:
        return float(m.group(0))
    n = words_to_number(text)
    return float(n) if n is not None else None


_NO = re.compile(r"\b(no|nope|never|didn'?t|did not|haven'?t|have not|hasn'?t|has not|not us|wasn'?t us|was not us|not from us|fraud|scam|fake)\b")
_YES = re.compile(r"^\W*(yes|yeah|yep|yup|correct|affirmative|we did|that'?s right|that is right|sure did|we have)\b")
_HEDGE = re.compile(r"\bnot\b|n'?t\b|\bunsure\b|\bmaybe\b|\bperhaps\b|\bneither\b|\bnor\b|\bcheck\b|\bwho\b|\bwhat\b|\bwhy\b"
                    r"|\bwait\b|\bhold on\b|\bthink\b|\bguess\b|\bprobably\b|\bbut\b|\?")


def classify(speech: str = "", digits: str = "") -> Optional[str]:
    """'denied', 'confirmed' or None. Any denial wins; a yes counts only when it leads and nothing hedges it."""
    if digits:
        return {"1": "confirmed", "2": "denied"}.get(digits.strip()[:1])
    text = (speech or "").lower().strip()
    if not text:
        return None
    if _NO.search(text):
        return "denied"
    if _YES.search(text) and not _HEDGE.search(text):
        return "confirmed"
    return None


# --- the call --------------------------------------------------------------------

class CallManager:
    def __init__(self, app, voice: Optional[Voice] = None):
        self.app = app
        s = app.settings
        self.voice = voice or Voice(s.cartesia_api_key, s.cartesia_voice, s.cartesia_model, s.data_dir / "audio")
        self._lock = threading.Lock()
        self.retry_delay = 5.0
        self.retry_attempts = 24

    @property
    def base(self) -> str:
        return self.app.settings.public_base_url.rstrip("/")

    def _unresolved(self, decision_id: str) -> bool:
        _, latest = self.app.client.chain(decision_id)
        return latest["decision_id"] == decision_id

    def start(self, decision: dict) -> str:
        """Place the call. Returns the call token. Refuses anything but an unresolved CALLBACK."""
        s = self.app.settings
        if decision.get("decision") != "CALLBACK" or not decision.get("callback_number"):
            raise TelephonyError("only a CALLBACK decision with a number on file can be called")
        if not s.twilio_ready:
            raise TelephonyError("telephony is not configured (Twilio credentials, TWILIO_FROM and DORIS_PUBLIC_URL)")
        if not self._unresolved(decision["decision_id"]):
            raise TelephonyError("this callback has already been resolved")
        with self._lock:
            current = self.app.store.latest_call(decision["decision_id"])
            if current and current["status"] in LIVE:
                if time.time() - current["updated_at"] < STALE_SECONDS:
                    return current["token"]
                self.app.store.update_call(current["token"], status="stale")
            elif current and current["status"] not in TERMINAL:
                self.app.store.update_call(current["token"], status="superseded") if not current.get("result") else None
            token = secrets.token_urlsafe(24)
            self.app.store.add_call(token, decision["decision_id"], decision["callback_number"])
        try:
            script = lines(decision, s.company)
            self.voice.render(script["prompt"])                       # ready before the phone rings
            threading.Thread(target=self._prerender, args=(script,), daemon=True).start()
            result = self._twilio_create(to=decision["callback_number"], url=f"{self.base}/twilio/{token}/voice",
                                         status_url=f"{self.base}/twilio/{token}/status")
        except Exception as exc:  # any failure after the row exists must not leave it "starting" forever
            self.app.store.update_call(token, status="failed", notes=str(exc)[:300])
            raise exc if isinstance(exc, TelephonyError) else TelephonyError(f"the call could not be placed: {exc}") from None
        self.app.store.update_call(token, status=result.get("status") or "queued", twilio_sid=result.get("sid"))
        self._event(decision["decision_id"], "system", f"Dialing {decision['callback_number']}, the number on file, never one from the email", token)
        return token

    def _event(self, decision_id: str, who: str, text: str, token: Optional[str] = None) -> None:
        try:
            self.app.store.add_event(decision_id, who, text, token)
        except Exception:  # noqa: BLE001  (the transcript is a view; it must never break the call)
            log.exception("could not record a call event")

    def _say(self, call: dict, token: str, line: str) -> None:
        self._event(call["decision_id"], "doris", line, token)

    def _prerender(self, script: dict) -> None:
        for key in ("reprompt", "confirm", "verify", "verify_failed", "denied", "confirmed", "unclear", "handled"):
            try:
                self.voice.render(script[key])
            except Exception:  # noqa: BLE001  (a missing line falls back to Twilio's voice)
                pass

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
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TelephonyError(f"Twilio not reachable: {e}") from None

    # --- TwiML ----------------------------------------------------------------------

    def _speak(self, text: str) -> str:
        try:
            name = self.voice.render(text)
        except Exception:  # noqa: BLE001
            name = None
        if name:
            return f"<Play>{xml_escape(f'{self.base}/audio/{name}')}</Play>"
        return f'<Say voice="Polly.Joanna">{xml_escape(text)}</Say>'

    def _gather(self, token: str, text: str, amount: bool = False) -> str:
        action = xml_escape(f"{self.base}/twilio/{token}/gather")
        keys = 'finishOnKey="#" timeout="8"' if amount else 'numDigits="1" timeout="6"'
        hints = "dollars, thousand, hundred" if amount else "yes, no, never, we never changed our bank"
        # bargeIn="false": nothing said while Doris is still asking counts as the answer.
        return (f'<Gather input="speech dtmf" {keys} speechTimeout="auto" bargeIn="false" language="en-US" '
                f'hints="{hints}" action="{action}" method="POST">{self._speak(text)}</Gather>'
                f'<Redirect method="POST">{action}</Redirect>')

    def _last_payment(self, decision: dict) -> Optional[float]:
        """The most recent payment on file for this vendor: something only the real vendor would know."""
        try:
            record = self.app.client.vendor(decision.get("vendor_id"))
        except DetectorError:
            return None
        history = (record or {}).get("payment_history") or []
        return float(history[-1]) if history else None

    def _accepts(self, token: str) -> Optional[dict]:
        """The call, if this token is the newest live call for its decision and has no answer yet."""
        call = self.app.store.call(token)
        if not call or call.get("result") or call["status"] not in LIVE:
            return None
        latest = self.app.store.latest_call(call["decision_id"])
        return call if latest and latest["token"] == token else None

    def voice_twiml(self, token: str) -> Optional[str]:
        call = self.app.store.call(token)
        if not call:
            return None
        if not self._accepts(token):
            return "<Response><Hangup/></Response>"
        decision = self.app.client.get(call["decision_id"])
        script = lines(decision, self.app.settings.company)
        if not self._unresolved(call["decision_id"]):
            self.app.store.update_call(token, status="resolved-elsewhere")
            return f"<Response>{self._speak(script['handled'])}<Hangup/></Response>"
        self.app.store.update_call(token, status="in-progress")
        self._event(call["decision_id"], "system", "The vendor picked up", token)
        self._say(call, token, script["prompt"])
        return f"<Response>{self._gather(token, script['prompt'])}</Response>"

    def gather_twiml(self, token: str, form: dict) -> Optional[str]:
        call = self.app.store.call(token)
        if not call:
            return None
        if not self._accepts(token):                              # old call, finished call or replay: nothing changes
            return "<Response><Hangup/></Response>"
        decision = self.app.client.get(call["decision_id"])
        script = lines(decision, self.app.settings.company)
        speech = (form.get("SpeechResult") or "").strip()[:200]
        digits = (form.get("Digits") or "").strip()[:12]
        heard = speech or (f"(pressed {digits})" if digits else "(silence)")
        self._event(call["decision_id"], "vendor", heard, token)
        if call.get("stage") == "verifying":
            # The records check. A hijacked line or a cloned voice still has to know what only the vendor knows.
            # A miss never blocks a real vendor: it leaves the payment frozen for a person.
            amount, expected = spoken_amount(speech, digits), self._last_payment(decision)
            answer = speech or (f"typed {digits}" if digits else "no answer")
            if amount is not None and expected is not None and abs(amount - expected) <= 1.0:
                said = f"{call.get('notes')}; last payment we sent: {answer} (matches the record on file)"
                self.app.store.update_call(token, status="saving", result=said, stage="confirmed")
                self._event(call["decision_id"], "system", "That matches the last payment on file", token)
                self._say(call, token, script["confirmed"])
                self._post(token, call["decision_id"], "confirmed", said, decision.get("callback_number"))
                return f"<Response>{self._speak(script['confirmed'])}<Hangup/></Response>"
            said = f"{call.get('notes')}; last payment we sent: {answer} (does not match the record on file)"
            self.app.store.update_call(token, status="inconclusive", result=said, stage="failed-check")
            self._event(call["decision_id"], "system", "That doesn't match the last payment on file: the payment stays frozen for a person", token)
            self._say(call, token, script["verify_failed"])
            return f"<Response>{self._speak(script['verify_failed'])}<Hangup/></Response>"
        outcome = classify(speech, digits[:1] if digits else "")
        said = speech or (f"pressed {digits}" if digits else "")
        if outcome == "confirmed" and call.get("stage") != "confirming":
            self.app.store.update_call(token, stage="confirming", notes=said)
            self._say(call, token, script["confirm"])
            return f"<Response>{self._gather(token, script['confirm'])}</Response>"
        if outcome is None:
            if call.get("stage") != "confirming" and call["attempts"] < 1:
                self.app.store.update_call(token, attempts=call["attempts"] + 1)
                self._say(call, token, script["reprompt"])
                return f"<Response>{self._gather(token, script['reprompt'])}</Response>"
            self.app.store.update_call(token, status="inconclusive", notes=said or "no answer")
            self._event(call["decision_id"], "system", "No clear answer: the payment stays frozen", token)
            self._say(call, token, script["unclear"])
            return f"<Response>{self._speak(script['unclear'])}<Hangup/></Response>"
        if outcome == "confirmed":
            said = f"{call.get('notes') or 'yes'}; then, asked again: {said}"
            if self._last_payment(decision) is None:
                self.app.store.update_call(token, status="inconclusive", stage="failed-check",
                                           result=f"{said}; no payment history on file to check against")
                self._event(call["decision_id"], "system", "No payment history on file to check against: a person decides", token)
                self._say(call, token, script["verify_failed"])
                return f"<Response>{self._speak(script['verify_failed'])}<Hangup/></Response>"
            self.app.store.update_call(token, stage="verifying", notes=said)
            self._say(call, token, script["verify"])
            return f"<Response>{self._gather(token, script['verify'], amount=True)}</Response>"
        self.app.store.update_call(token, status="saving", result=said or outcome, stage=outcome)
        self._say(call, token, script[outcome])
        self._post(token, call["decision_id"], outcome, said or outcome, decision.get("callback_number"))
        return f"<Response>{self._speak(script[outcome])}<Hangup/></Response>"

    def _post(self, token: str, decision_id: str, outcome: str, said: str, called: Optional[str]) -> None:
        """Hand the vendor's answer to the detector; if it is down, keep the answer and keep trying."""
        def attempt() -> bool:
            try:
                follow = self.app.client.callback_result(decision_id, confirmed=outcome == "confirmed", notes=said, called=called)
                self.app.store.update_call(token, status="completed")
                self._event(decision_id, "system", f"Doris recorded the answer: {(follow or {}).get('decision', 'done')}", token)
                return True
            except DetectorError as exc:
                if exc.status in (400, 404, 409):       # resolved by the clerk meanwhile, or refused: stop
                    self.app.store.update_call(token, status="resolved-elsewhere", notes=exc.message[:300])
                    return True
                return False
        if attempt():
            return

        def retry():
            for _ in range(self.retry_attempts):
                time.sleep(self.retry_delay)
                if attempt():
                    return
            self.app.store.update_call(token, status="unsaved")
        threading.Thread(target=retry, daemon=True).start()

    def status(self, token: str, form: dict) -> bool:
        call = self.app.store.call(token)
        if not call:
            return False
        status = (form.get("CallStatus") or "").strip()[:20]
        if status and call["status"] in LIVE and not call.get("result"):
            self.app.store.update_call(token, status=status)
            shown = {"ringing": "Ringing", "busy": "Line busy", "no-answer": "No answer", "failed": "The call failed", "canceled": "Call canceled"}
            if status in shown:
                self._event(call["decision_id"], "system", shown[status], token)
        return True


def _form(handler) -> dict:
    from .app import content_length
    length = content_length(handler)
    if length <= 0 or length > 50_000:
        return {}
    raw = handler.rfile.read(length).decode("utf-8", "replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def wire(app) -> CallManager:
    manager = CallManager(app)
    app.callbacks = manager
    xml = "text/xml; charset=utf-8"

    def known(token: str) -> bool:
        return app.store.call(token) is not None

    def twiml(h, token, make):
        if not known(token):                                    # check the token before reading any body
            return h.send(404, b"unknown call", "text/plain")
        try:
            out = make()
        except Exception:  # noqa: BLE001  (Twilio must always get TwiML back)
            log.exception("callback webhook failed")
            out = SORRY
        h.send(200, out or SORRY, xml)

    def voice(h, token):
        twiml(h, token, lambda: manager.voice_twiml(token))

    def gather(h, token):
        twiml(h, token, lambda: manager.gather_twiml(token, _form(h)))

    def status(h, token):
        if not known(token):
            return h.send(404, b"", "text/plain")
        manager.status(token, _form(h))
        h.send(204, b"", "text/plain")

    def audio(h, name):
        p = manager.voice.path(name)
        h.send(200, p.read_bytes(), "audio/wav") if p else h.send(404, b"no such audio", "text/plain")

    app.route("POST", rf"/twilio/{TOKEN}/voice", voice)
    app.route("GET", rf"/twilio/{TOKEN}/voice", voice)
    app.route("POST", rf"/twilio/{TOKEN}/gather", gather)
    app.route("POST", rf"/twilio/{TOKEN}/status", status)
    app.route("GET", r"/audio/([0-9a-f]{32}\.wav)", audio)
    return manager
