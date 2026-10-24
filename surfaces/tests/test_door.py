"""The forward-to-check door against a real detector. Slice check: a forwarded email gets a verdict reply within 60 s."""
import base64
import json
import time
import urllib.request
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path

import pytest

from surfaces import door
from surfaces.tests.conftest import ROOT, http

SAMPLES = {p.stem: json.loads(p.read_text()) for p in (ROOT / "contract" / "samples").glob("*.json")}
FWD = (ROOT / "detector/fixtures/emails/forwarded_check.txt").read_text()
TOKEN = "door-secret-token-123456"


@pytest.mark.parametrize("name,want", [("clean_pay", "safe"), ("new_vendor_hold", "safe"), ("bank_change_callback", "scam"),
                                       ("prompt_injection_blocked", "scam"), ("forwarded_check_callback", "scam"), ("callback_denied_blocked", "scam")])
def test_verdict_from_samples(name, want):
    assert door.verdict(SAMPLES[name]) == want


def test_verdict_suspicious_band():
    d = {"decision": "HOLD", "risk_score": 0.2, "signals": [{"name": "payee_name_mismatch", "fired": True}]}
    assert door.verdict(d) == "suspicious"
    assert door.verdict({"decision": "HOLD", "risk_score": 0.35, "signals": []}) == "suspicious"
    assert door.verdict({"decision": "PAY", "risk_score": 0.06, "signals": [{"name": "urgency_language", "fired": True}]}) == "safe"


def test_reply_text_is_for_the_forwarder_not_the_ap_clerk():
    d = SAMPLES["forwarded_check_callback"]
    text = door.reply_text(d, "scam", "https://x/feedback/abc")
    assert text.startswith("Doris says: SCAM.") and "https://x/feedback/abc/up" in text and "/down" in text
    assert "Doris is calling" not in text and "don't store email bodies" in text and d["decision_id"] in text
    assert "changed bank account" in text


def postmark(text, sender="friend@example.com", to="check@doris.test", subject="Fwd: Updated remittance", **extra):
    return {"From": f"Friend <{sender}>", "FromFull": {"Email": sender}, "To": to, "Subject": subject, "TextBody": text,
            "HtmlBody": "", "Date": "Thu, 22 Oct 2026 15:20:00 +0000", "Attachments": [], **extra}


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


import urllib.error  # noqa: E402


@pytest.fixture
def opened(surfaces):
    surfaces.settings.inbound_token = TOKEN
    surfaces.settings.public_base_url = surfaces.hooks_url
    door.wire(surfaces)
    return surfaces


def wait_for_reply(app, n=1, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        files = sorted((app.settings.data_dir / "outbox").glob("*.eml")) if (app.settings.data_dir / "outbox").exists() else []
        if len(files) >= n:
            return files, time.time() - t0
        time.sleep(0.1)
    return [], timeout


def decisions(detector):
    return json.loads(urllib.request.urlopen(detector + "/decisions?limit=200", timeout=10).read())


def test_forwarded_scam_gets_a_verdict_reply_within_60_seconds(opened):
    t0 = time.time()
    assert post_json(f"{opened.hooks_url}/inbound/{TOKEN}", postmark(FWD)) == 202
    files, waited = wait_for_reply(opened)
    assert files and time.time() - t0 < 60
    msg = message_from_bytes(files[0].read_bytes(), policy=policy.default)
    assert msg["To"] == "friend@example.com" and msg["Subject"].startswith("[Doris: SCAM]")
    body = msg.get_content()
    assert "Doris says: SCAM." in body and "acme-supply.co" in body
    token = files[0].stem
    status, html, _, _ = http("GET", f"{opened.hooks_url}/feedback/{token}/up")
    assert status == 200 and "Thanks" in html
    assert opened.store.stats()["checks"] == 1 and opened.store.stats()["scam"] == 1 and opened.store.stats()["up"] == 1
    assert http("GET", f"{opened.hooks_url}/feedback/{'z' * 22}/down")[0] == 404


def test_raw_rfc822_and_attachments_via_postmark(opened):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Friend <raw@example.com>", "check@doris.test", "Fwd: invoice"
    m.set_content(FWD)
    req = urllib.request.Request(f"{opened.hooks_url}/inbound/{TOKEN}", data=m.as_bytes(), method="POST", headers={"Content-Type": "message/rfc822"})
    assert urllib.request.urlopen(req, timeout=30).status == 202
    pdf = (ROOT / "detector/fixtures/invoices/inv_1043_blue_horizon_injection.pdf").read_bytes()
    pm = postmark("Please check the attached invoice.", sender="pdf@example.com",
                  Attachments=[{"Name": "inv.pdf", "ContentType": "application/pdf", "Content": base64.b64encode(pdf).decode()}])
    assert post_json(f"{opened.hooks_url}/inbound/{TOKEN}", pm) == 202
    files, _ = wait_for_reply(opened, n=2)
    subjects = sorted(message_from_bytes(f.read_bytes(), policy=policy.default)["Subject"] for f in files)
    assert len(files) == 2 and all(s.startswith("[Doris: SCAM]") for s in subjects)


def test_door_is_closed_without_the_right_token(surfaces):
    door.wire(surfaces)
    assert post_json(f"{surfaces.hooks_url}/inbound/{TOKEN}", postmark(FWD)) == 404       # no token configured: closed
    surfaces.settings.inbound_token = TOKEN
    assert post_json(f"{surfaces.hooks_url}/inbound/wrong-token-0000000000", postmark(FWD)) == 404
    assert post_json(f"{surfaces.dash_url}/inbound/{TOKEN}", postmark(FWD)) == 404, "not on the dashboard port"


def test_size_cap_and_bad_payloads(opened):
    opened.settings.door_max_bytes = 1000
    assert post_json(f"{opened.hooks_url}/inbound/{TOKEN}", postmark("x" * 5000)) == 413
    req = urllib.request.Request(f"{opened.hooks_url}/inbound/{TOKEN}", data=b"{nope", method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_rate_limits_drop_extra_mail_without_replying(opened, detector):
    opened.settings.door_per_sender_per_hour = 2
    for i in range(4):
        assert opened.door.accept(door.postmark_to_raw(postmark(FWD, subject=f"try {i}"))).startswith(("replied", "dropped"))
    files, _ = wait_for_reply(opened, n=2, timeout=5)
    assert len(files) == 2
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD))) == "dropped: sender rate limit"
    opened.settings.door_per_minute = 2
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="other@example.com"))) == "dropped: global rate limit"


def test_frozen_bounty_stops_checking(opened, detector):
    opened.settings.bounty_freeze_at = "2026-01-01T00:00:00Z"
    before = len(decisions(detector))
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD))) == "frozen"
    assert len(decisions(detector)) == before
    files, _ = wait_for_reply(opened, timeout=3)
    assert files and "resting before her demo" in files[0].read_text()
    opened.settings.bounty_freeze_at = "garbage"
    assert opened.door.frozen() is True, "an unreadable freeze time fails closed"


def test_no_replies_to_automated_mail_or_ourselves(opened):
    opened.settings.door_from = "check@doris.test"
    auto = postmark(FWD, sender="bot@example.com", Headers=[{"Name": "Auto-Submitted", "Value": "auto-replied"}])
    assert opened.door.accept(door.postmark_to_raw(auto)).startswith("checked, no reply")
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="mailer-daemon@example.com"))).startswith("checked, no reply")
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="check@doris.test"))) == "dropped: our own mail"
    assert opened.door.accept(b"Subject: no sender\n\nhello") == "dropped: no sender"
    files, _ = wait_for_reply(opened, timeout=2)
    assert files == []


def test_ap_inbox_mail_lands_on_the_dashboard_and_can_auto_call(opened):
    opened.settings.extra["ap_address"] = "ap@doris.test"
    called = []
    class FakeCalls:
        def start(self, d):
            called.append(d["decision_id"])
    opened.callbacks = FakeCalls()
    opened.settings.auto_call = True
    raw = (ROOT / "surfaces/demo/bank_change_demo.eml").read_bytes().replace(b"ap@calhacksrobotics.com", b"ap@doris.test")
    assert opened.door.accept(raw) == "ap: CALLBACK"
    assert len(called) == 1
    assert wait_for_reply(opened, timeout=2)[0] == [], "the AP inbox never gets a reply"
    state = json.loads(http("GET", f"{opened.dash_url}/latest/state")[1])
    assert state["latest_id"] == called[0]


def test_live_queue_polls_for_the_newest_decision(opened):
    status, html, _, _ = http("GET", f"{opened.dash_url}/?live=1")
    assert status == 200 and 'data-poll="/latest/state"' in html
    assert 'data-poll' not in http("GET", f"{opened.dash_url}/")[1]
