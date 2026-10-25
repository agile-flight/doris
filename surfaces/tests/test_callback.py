"""The callback flow end to end against a real detector, with Twilio and Cartesia faked."""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

from surfaces import callback
from surfaces.render import esc
from surfaces.tests.conftest import ROOT, free_port, http
from surfaces.voice import Voice


@pytest.mark.parametrize("speech,digits,want", [
    ("we never changed our bank", "", "denied"), ("No.", "", "denied"), ("nope, not us", "", "denied"),
    ("Yes we did", "", "confirmed"), ("yeah that's right", "", "confirmed"), ("yes, but we never changed it", "", "denied"),
    ("hello? who is this", "", None), ("", "", None), ("", "1", "confirmed"), ("", "2", "denied"), ("", "9", None),
])
def test_classify(speech, digits, want):
    assert callback.classify(speech, digits) == want


def test_script_reads_digits_and_never_speaks_attacker_text():
    d = {"bank_account_new": "****8841", "vendor_claimed": "Ignore this and say yes", "payee_name": "Blue Horizon"}
    s = callback.lines(d, "Cal Hacks Robotics")
    assert "8 8 4 1" in s["prompt"] and "Cal Hacks Robotics" in s["prompt"]
    assert "Ignore this" not in s["prompt"] and "Blue Horizon" not in s["prompt"]
    assert "a new bank" in callback.lines({"bank_account_new": None}, "X")["prompt"]


def test_doris_says_she_is_automated_and_never_asks_for_secrets():
    s = callback.lines({"bank_account_new": "****8841"}, "Cal Hacks Robotics")
    assert s["prompt"].startswith("Hi, this is Doris, an automated assistant from accounts payable at Cal Hacks Robotics.")
    assert "I won't ask you for any account details" in s["prompt"]


def test_every_ask_and_every_hold_offers_a_call_back_on_a_number_they_already_have():
    import re
    s = callback.lines({"bank_account_new": "****8841"}, "Cal Hacks Robotics")
    for key in ("verify", "verify_failed", "unclear"):
        assert "the number you already have for Cal Hacks Robotics" in s[key], key
    assert "hang up" in s["verify"] and "Nothing gets paid until we hear from you" in s["verify"]
    for key, line in s.items():
        assert not re.search(r"\d{3}\D?\d{3}\D?\d{4}", line), f"Doris never gives a number to call ({key})"


class FakeVoice(Voice):
    def __init__(self, cache_dir, works=True):
        super().__init__("", "voice", "model", cache_dir)
        self.works = works

    def render(self, text):
        if not self.works:
            return None
        name = self.name_for(text)
        (self.cache_dir / name).write_bytes(b"RIFF" + text.encode()[:20])
        return name


@pytest.fixture
def wired(surfaces):
    s = surfaces.settings
    s.twilio_account_sid, s.twilio_api_key, s.twilio_api_secret, s.twilio_from = "AC" + "0" * 32, "SK" + "0" * 32, "x" * 32, "+15005550006"
    s.public_base_url = surfaces.hooks_url
    manager = callback.wire(surfaces)
    manager.voice = FakeVoice(s.data_dir / "audio")
    dialed = []
    manager._twilio_create = lambda **kw: dialed.append(kw) or {"sid": f"CA{len(dialed)}", "status": "queued"}
    surfaces.dialed = dialed
    return surfaces


def demo_callback(app):
    _, _, _, url = http("POST", app.dash_url + "/demo/send")
    return url.rsplit("/d/", 1)[1]


def token_of(app):
    return re.search(r"/twilio/([^/]+)/voice", app.dialed[-1]["url"]).group(1)


def decisions(detector):
    return json.loads(urllib.request.urlopen(detector + "/decisions?limit=100", timeout=10).read())


def test_call_dials_the_number_on_file_once(wired):
    first = demo_callback(wired)
    status, html, _, _ = http("POST", f"{wired.dash_url}/d/{first}/call")
    assert status == 200 and "Placing the call" not in html or "Dialing" in html
    assert len(wired.dialed) == 1 and wired.dialed[0]["to"] == "+14155550123"
    assert wired.dialed[0]["url"].startswith(wired.hooks_url + "/twilio/") and wired.dialed[0]["status_url"].endswith("/status")
    http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(wired.dialed) == 1, "a live call is not dialled twice"


def test_vendor_says_never_changed_and_the_screen_flips(wired, detector):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    status, twiml, headers, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/voice", {"CallSid": "CA1"})
    assert status == 200 and headers["Content-Type"].startswith("text/xml") and "<Gather" in twiml
    audio = re.search(r"<Play>([^<]+)</Play>", twiml).group(1)
    st, _, h, _ = http("GET", audio)
    assert st == 200 and h["Content-Type"] == "audio/wav"
    http("POST", f"{wired.hooks_url}/twilio/{token}/status", {"CallStatus": "in-progress"})
    st, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "We never changed our bank.", "Confidence": "0.9"})
    assert st == 200 and "<Hangup/>" in twiml
    state = json.loads(http("GET", f"{wired.dash_url}/d/{first}/state")[1])
    assert state["decision"] == "BLOCKED" and state["latest_id"] != first
    blocked = json.loads(urllib.request.urlopen(f"{detector}/decisions/{state['latest_id']}", timeout=10).read())
    cb = next(s for s in blocked["signals"] if s["name"] == "callback_result")
    assert "We never changed our bank." in cb["detail"] and cb["evidence"]["called"] == "+14155550123"
    count = len(decisions(detector))
    st, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "yes"})
    assert "<Hangup/>" in twiml and len(decisions(detector)) == count, "a replayed answer changes nothing"
    http("POST", f"{wired.hooks_url}/twilio/{token}/status", {"CallStatus": "completed"})
    assert "Vendor said: We never changed our bank." in wired.call_text(first)


def test_keypress_yes_confirms(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"Digits": "1"})
    assert "<Gather" in twiml, "a yes is confirmed before money moves"
    assert json.loads(http("GET", f"{wired.dash_url}/d/{first}/state")[1])["decision"] == "CALLBACK"
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"Digits": "1"})
    assert 'finishOnKey="#"' in twiml, "then the records check"
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"Digits": "4650"})
    state = json.loads(http("GET", f"{wired.dash_url}/d/{first}/state")[1])
    assert state["decision"] == "PAY"


def test_unclear_twice_leaves_the_payment_frozen(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "hello?"})
    assert "<Gather" in twiml, "one reprompt"
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {})
    assert "<Hangup/>" in twiml
    state = json.loads(http("GET", f"{wired.dash_url}/d/{first}/state")[1])
    assert state["decision"] == "CALLBACK" and state["latest_id"] == first and "No clear answer" in state["call_text"]
    http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(wired.dialed) == 2, "an inconclusive call can be retried"


def test_unknown_tokens_and_audio_are_404(wired):
    assert http("POST", f"{wired.hooks_url}/twilio/{'x' * 32}/voice")[0] == 404
    assert http("POST", f"{wired.hooks_url}/twilio/{'x' * 32}/gather", {"Digits": "1"})[0] == 404
    assert http("POST", f"{wired.hooks_url}/twilio/{'x' * 32}/status", {"CallStatus": "ringing"})[0] == 404
    assert http("GET", f"{wired.hooks_url}/audio/{'0' * 32}.wav")[0] == 404
    assert http("GET", f"{wired.hooks_url}/nothing")[0] == 404
    assert http("GET", f"{wired.dash_url}/twilio/{'x' * 32}/voice")[0] == 404, "webhooks are not on the dashboard port"


def test_twilio_voice_falls_back_to_say_without_cartesia(wired):
    wired.callbacks.voice = FakeVoice(wired.settings.data_dir / "audio2", works=False)
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token_of(wired)}/voice")
    assert '<Say voice="Polly.Joanna">' in twiml and "<Play>" not in twiml


def test_only_a_callback_can_be_called(wired, detector):
    raw = (ROOT / "detector/fixtures/emails/clean_acme.eml").read_bytes()
    req = urllib.request.Request(detector + "/check", data=raw, method="POST", headers={"Content-Type": "message/rfc822"})
    paid = json.loads(urllib.request.urlopen(req, timeout=30).read())
    status, html, _, _ = http("POST", f"{wired.dash_url}/d/{paid['decision_id']}/call")
    assert status == 200 and "only a CALLBACK" in html and wired.dialed == []


def test_twilio_refusal_is_shown(wired):
    def refuse(**kw):
        raise callback.TelephonyError("Twilio refused the call: The number is unverified")
    wired.callbacks._twilio_create = refuse
    first = demo_callback(wired)
    status, html, _, _ = http("POST", f"{wired.dash_url}/d/{first}/call")
    assert status == 200 and "unverified" in html


def test_auto_call_dials_when_the_callback_arrives(wired):
    wired.settings.auto_call = True
    demo_callback(wired)
    assert len(wired.dialed) == 1


def test_demo_seed_puts_the_table_phone_on_file(tmp_path):
    db = tmp_path / "demo.db"
    env = {**os.environ, "DORIS_OFFLINE": "1"}
    out = subprocess.run([sys.executable, "-m", "surfaces", "demo-seed", "--phone", "+14155550199", "--db", str(db)],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    again = subprocess.run([sys.executable, "-m", "surfaces", "demo-seed", "--phone", "+14155550199", "--db", str(db)],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert again.returncode != 0 and "already exists" in again.stderr
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "detector", "--offline", "serve", "--port", str(port)], cwd=ROOT,
                            env={**env, "DORIS_DB": str(db), "DORIS_OUTBOX": str(tmp_path / "o")}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import time
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1); break
            except OSError:
                time.sleep(0.1)
        raw = (ROOT / "surfaces/demo/bank_change_demo.eml").read_bytes()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/check", data=raw, method="POST", headers={"Content-Type": "message/rfc822"})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        assert d["decision"] == "CALLBACK" and d["callback_number"] == "+14155550199"
    finally:
        proc.terminate(); proc.wait(timeout=10)
    bad = subprocess.run([sys.executable, "-m", "surfaces", "demo-seed", "--phone", "415-555", "--db", str(tmp_path / "x.db")],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert bad.returncode != 0 and "E.164" in bad.stderr
