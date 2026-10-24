"""The two AI-fraud gaps: a hijacked number answered by a cloned voice, and a new vendor with forged documents."""
import json
import urllib.request

import pytest

from surfaces import callback, render
from surfaces.tests.conftest import ROOT, http
from surfaces.tests.test_callback import demo_callback, token_of, wired  # noqa: F401  (fixture)


@pytest.mark.parametrize("speech,digits,want", [
    ("$4,650.", "", 4650.0), ("4650", "", 4650.0), ("It was 4,650 dollars", "", 4650.0), ("4 thousand 650", "", 4650.0),
    ("four thousand six hundred fifty dollars", "", 4650.0), ("forty six fifty", "", 4650.0), ("forty six hundred and fifty", "", 4650.0),
    ("twenty two hundred", "", 2200.0), ("", "4650", 4650.0), ("no idea", "", None), ("", "", None),
])
def test_spoken_amounts(speech, digits, want):
    assert callback.spoken_amount(speech, digits) == want


def answer_yes_twice(app, first):
    http("POST", f"{app.dash_url}/d/{first}/call")
    token = token_of(app)
    http("POST", f"{app.hooks_url}/twilio/{token}/voice")
    http("POST", f"{app.hooks_url}/twilio/{token}/gather", {"SpeechResult": "Yes we did"})
    _, twiml, _, _ = http("POST", f"{app.hooks_url}/twilio/{token}/gather", {"SpeechResult": "yes"})
    return token, twiml


def decision_of(app, first):
    return json.loads(http("GET", f"{app.dash_url}/d/{first}/state")[1])["decision"]


@pytest.mark.parametrize("answer", ["$4,650.", "four thousand six hundred fifty dollars", "forty six fifty"])
def test_the_real_vendor_knows_the_last_payment_and_gets_paid(wired, answer):
    first = demo_callback(wired)
    token, twiml = answer_yes_twice(wired, first)
    assert "last payment" in callback.lines({}, "X")["verify"] and "4650" not in twiml and "4,650" not in twiml
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": answer})
    assert decision_of(wired, first) == "PAY"
    assert "matches the record on file" in wired.store.call(token)["result"]


def test_a_hijacked_line_with_a_cloned_voice_guesses_wrong_and_nothing_moves(wired):
    first = demo_callback(wired)
    token, _ = answer_yes_twice(wired, first)              # the attacker controls the number and says all the right things
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "about five thousand"})
    assert "<Hangup/>" in twiml
    assert decision_of(wired, first) == "CALLBACK", "frozen for a person, never paid"
    assert "does not match the record on file" in wired.call_text(first)


def test_not_knowing_the_amount_never_blocks_a_real_vendor(wired):
    first = demo_callback(wired)
    token, _ = answer_yes_twice(wired, first)
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "no idea, sorry"})
    assert decision_of(wired, first) == "CALLBACK", "'no idea' is not a denial"


def test_no_payment_history_means_a_person_decides(wired):
    wired.callbacks._last_payment = lambda d: None
    first = demo_callback(wired)
    token, twiml = answer_yes_twice(wired, first)
    assert "<Hangup/>" in twiml and decision_of(wired, first) == "CALLBACK"
    assert "no payment history" in wired.store.call(token)["result"]


def test_a_denial_still_blocks_at_once(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "no, we never changed our bank"})
    assert decision_of(wired, first) == "BLOCKED"


# --- a new vendor with forged documents ----------------------------------------------

def check(detector, raw: bytes) -> dict:
    req = urllib.request.Request(detector + "/check", data=raw, method="POST", headers={"Content-Type": "message/rfc822"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


FORGED = (b"From: Brightline Tools <ar@acme-supply-group.com>\nTo: ap@calhacksrobotics.com\nSubject: Invoice BT-1001\n"
          b"Date: Fri, 23 Oct 2026 10:00:00 +0000\n\nHello, welcome aboard! Our first invoice BT-1001 is below.\n"
          b"Remit to: Brightline Tools LLC\nRouting Number: 026009593\nAccount Number: 551200338871\nTotal Due: $3,900.00\n")


def test_approving_a_new_vendor_needs_independent_verification(wired, detector):
    held = check(detector, FORGED)
    assert held["decision"] == "HOLD" and held["vendor_id"] is None
    page = http("GET", f"{wired.dash_url}/d/{held['decision_id']}")[1]
    assert "First-time vendor" in page and "untrusted" in page and "How did you verify it?" in page
    age = render.domain_age_days(held)
    assert age is not None and age < render.YOUNG_DOMAIN_DAYS and f"only {age} days old" in page
    url = f"{wired.dash_url}/d/{held['decision_id']}/review"
    _, html, _, _ = http("POST", url, {"approve": "yes", "reviewer": "Sam"})
    assert "say how you verified it" in html
    _, html, _, _ = http("POST", url, {"approve": "yes", "reviewer": "Sam", "method": "called", "source": "x"})
    assert "say how you verified it" in html, "a source of a character or two is not a source"
    _, html, _, _ = http("POST", url, {"approve": "yes", "reviewer": "Sam", "method": "called", "source": "the number on their state filing"})
    assert f"only {age} days old" in html and "Tick the box" in html and "On hold" in html
    _, html, _, _ = http("POST", url, {"approve": "yes", "reviewer": "Sam", "method": "called", "source": "the number on their state filing", "young_ok": "yes"})
    assert "Paid" in html and "the number on their state filing" in html and f"domain is {age} days old" in html


def test_rejecting_needs_only_a_name(wired, detector):
    held = check(detector, FORGED)
    _, html, _, _ = http("POST", f"{wired.dash_url}/d/{held['decision_id']}/review", {"approve": "no", "reviewer": "Sam"})
    assert "blocked-screen" in html


def test_review_panel_renders_for_every_hold_sample():
    sample = json.loads((ROOT / "contract/samples/new_vendor_hold.json").read_text())
    html = render.review_panel(sample)
    assert "First-time vendor" in html and all(v in html for v in render.VERIFY_METHODS.values())
