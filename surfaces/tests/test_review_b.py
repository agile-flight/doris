"""One regression test per finding of the adversarial review of surfaces/ (2026-10-24), numbered as reported."""
import json
import socket
import threading
import time
import urllib.request
from email.message import EmailMessage

import pytest

from surfaces import app as app_module, callback, door, render
from surfaces.client import DetectorError
from surfaces.tests.conftest import ROOT, http
from surfaces.tests.test_callback import FakeVoice, demo_callback, token_of, wired  # noqa: F401  (fixture)
from surfaces.tests.test_door import FWD, TOKEN, opened, postmark, post_json, wait_for_reply  # noqa: F401  (fixture)

SAMPLE = json.loads((ROOT / "contract/samples/bank_change_callback.json").read_text())


def state(app, decision_id):
    return json.loads(http("GET", f"{app.dash_url}/d/{decision_id}/state")[1])


def raw_post(port, path, headers, body=b"", wait=3.0):
    """A hand-written HTTP request, for lies a normal client won't tell."""
    s = socket.create_connection(("127.0.0.1", port), timeout=wait)
    s.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n").encode() + body)
    try:
        data = s.recv(200)
    except socket.timeout:
        data = b""
    s.close()
    return data


# 1 ------------------------------------------------------------------------------
def test_01_headers_cannot_route_mail_into_the_ap_inbox(opened, wired):
    called = []
    opened.callbacks = type("C", (), {"start": lambda self, d: called.append(d)})()
    opened.settings.auto_call = True
    opened.settings.bounty_freeze_at = "2026-01-01T00:00:00Z"
    raw = (ROOT / "surfaces/demo/bank_change_demo.eml").read_bytes().replace(b"To: ap@calhacksrobotics.com", b"To: check@doris.test\nCc: ap@calhacksrobotics.com")
    assert opened.door.accept(raw) == "frozen" and called == []
    pm = postmark(FWD, to="check@doris.test, ap@calhacksrobotics.com")
    assert opened.door.accept(door.postmark_to_raw(pm)).startswith("frozen") and called == []
    assert post_json(f"{opened.hooks_url}/inbound-ap/{TOKEN}", pm) == 404, "the door's token does not open the AP inbox"
    opened.settings.extra["ap_inbound_token"] = "ap-inbox-secret-0000001"
    assert post_json(f"{opened.hooks_url}/inbound-ap/ap-inbox-secret-0000001", pm) == 202


# 2 ------------------------------------------------------------------------------
@pytest.mark.parametrize("speech", ["I can't confirm that", "That's not correct", "Yeah, I don't think so", "Yeah I'm not sure, let me check",
                                    "I can neither confirm nor deny", "Yes? Who is this?", "yes but hold on"])
def test_02_hedges_are_not_a_yes(speech):
    assert callback.classify(speech) is None


def test_02_a_hedge_never_pays_and_greetings_cannot_barge_in(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    assert 'bargeIn="false"' in twiml
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "I can't confirm that, who is this?"})
    assert "<Gather" in twiml and state(wired, first)["decision"] == "CALLBACK"
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "Yes we did"})
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "Yeah, wait, what account?"})
    assert "<Hangup/>" in twiml and state(wired, first)["decision"] == "CALLBACK", "a first yes then a hedge stays frozen"


# 3 ------------------------------------------------------------------------------
def test_03_a_strangers_bank_change_is_never_called_safe(opened):
    text = "Hello,\nOur bank account has changed. Please remit $12,400.00 to our new account today.\nAccount Number: 885500129930\nRouting Number: 322271627\n"
    assert opened.door.accept(door.postmark_to_raw(postmark(text, sender="stranger@example.com", subject="New bank details"))).startswith("replied")
    files, _ = wait_for_reply(opened)
    body = files[0].read_text()
    assert "[Doris: SCAM]" in body or "[Doris: SUSPICIOUS]" in body
    assert "NO RED FLAGS" not in body


# 4 ------------------------------------------------------------------------------
def test_04_lying_content_length_cannot_eat_memory_or_threads(opened, wired, monkeypatch):
    port = int(opened.hooks_url.rsplit(":", 1)[1])
    t0 = time.time()
    resp = raw_post(port, f"/twilio/{'A' * 20}/gather", {"Content-Length": "-1"}, b"x" * 100_000)
    assert resp.startswith(b"HTTP/1.0 404") or resp.startswith(b"HTTP/1.1 404")
    resp = raw_post(port, f"/inbound/{TOKEN}", {"Content-Length": str(10 ** 9), "Content-Type": "application/json"})
    assert b" 413 " in resp
    resp = raw_post(port, f"/inbound/{TOKEN}", {"Content-Length": "-5"})
    assert b" 400 " in resp
    assert time.time() - t0 < 10
    monkeypatch.setattr(app_module._Base, "timeout", 1)
    t0 = time.time()
    resp = raw_post(port, f"/inbound/{TOKEN}", {"Content-Length": "50000", "Content-Type": "message/rfc822"}, b"0123456789", wait=6)
    assert time.time() - t0 < 6, "a stalled body releases its thread"


# 5 ------------------------------------------------------------------------------
def test_05_dashboard_refuses_other_sites_and_rebinding(wired):
    first = demo_callback(wired)
    port = int(wired.dash_url.rsplit(":", 1)[1])
    resp = raw_post(port, f"/d/{first}/manual_callback", {"Origin": "https://evil.example", "Content-Type": "application/x-www-form-urlencoded",
                                                           "Content-Length": "12"}, b"confirmed=no")
    assert b" 403 " in resp and state(wired, first)["decision"] == "CALLBACK"
    resp = raw_post(port, f"/d/{first}/manual_callback", {"Referer": "https://evil.example/x", "Content-Type": "application/x-www-form-urlencoded",
                                                           "Content-Length": "12"}, b"confirmed=no")
    assert b" 403 " in resp
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.sendall(f"GET / HTTP/1.1\r\nHost: rebind.evil.example:{port}\r\n\r\n".encode())
    assert b" 403 " in s.recv(100)
    s.close()
    _, _, headers, _ = http("GET", wired.dash_url + "/")
    assert headers["X-Frame-Options"] == "DENY"
    ok = raw_post(port, f"/d/{first}/manual_callback", {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/x-www-form-urlencoded",
                                                        "Content-Length": "12"}, b"confirmed=no")
    assert b" 303 " in ok, "the dashboard's own forms still work"


# 6 ------------------------------------------------------------------------------
def test_06_an_older_call_cannot_answer(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    old = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{old}/status", {"CallStatus": "no-answer"})
    http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(wired.dialed) == 2
    for _ in range(2):
        _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{old}/gather", {"Digits": "1"})
        assert twiml == "<Response><Hangup/></Response>"
    assert state(wired, first)["decision"] == "CALLBACK"


# 7 ------------------------------------------------------------------------------
def test_07_resolved_callbacks_are_not_redialled(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.dash_url}/d/{first}/manual_callback", {"confirmed": "no"})           # the clerk got there first
    _, twiml, _, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    assert "<Hangup/>" in twiml and "<Gather" not in twiml
    status, html, _, _ = http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(wired.dialed) == 1 and "blocked-screen" in html


def test_07_an_answer_after_the_clerk_resolved_it_is_spoken_to_politely(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    http("POST", f"{wired.dash_url}/d/{first}/manual_callback", {"confirmed": "no"})
    _, twiml, headers, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "no we did not"})
    assert headers["Content-Type"].startswith("text/xml") and "<Hangup/>" in twiml
    assert wired.store.call(token)["status"] == "resolved-elsewhere"


# 8 ------------------------------------------------------------------------------
def test_08_a_burst_cannot_slip_past_the_limits(opened):
    opened.settings.door_per_sender_per_hour = 5
    raws = [door.postmark_to_raw(postmark(FWD, subject=f"burst {i}")) for i in range(40)]
    gate = threading.Barrier(40)
    results = []

    def go(r):
        gate.wait()
        results.append(opened.door.accept(r))
    threads = [threading.Thread(target=go, args=(r,)) for r in raws]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert sum(r.startswith("replied") for r in results) <= 5
    assert opened.store.count_checks(since=0) <= 5


# 9 ------------------------------------------------------------------------------
def test_09_frozen_replies_are_limited_and_marked_automatic(opened):
    opened.settings.bounty_freeze_at = "2026-01-01T00:00:00Z"
    for i in range(20):
        opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="victim@example.org", subject=f"spam {i}")))
    files, _ = wait_for_reply(opened, timeout=2)
    assert len(files) == 1
    assert "Auto-Submitted: auto-replied" in files[0].read_text()


# 10 -----------------------------------------------------------------------------
def test_10_a_long_folded_subject_still_gets_a_reply(opened):
    m = EmailMessage()
    m["From"], m["To"] = "Friend <long@example.com>", "check@doris.test"
    m["Subject"] = "Fwd: " + "URGENT updated remittance instructions for invoice INV-1046 " * 3
    m.set_content(FWD)
    assert opened.door.accept(m.as_bytes()).startswith("replied")
    files, _ = wait_for_reply(opened)
    subject = next(l for l in files[0].read_text().splitlines() if l.startswith("Subject:"))
    assert "URGENT updated" in subject


# 11 -----------------------------------------------------------------------------
def test_11_a_far_future_date_does_not_break_the_screen(wired, detector):
    raw = (ROOT / "surfaces/demo/bank_change_demo.eml").read_bytes()
    raw = b"\n".join(b"Date: Fri, 31 Dec 9999 23:00:00 +0000" if l.startswith(b"Date:") else l for l in raw.split(b"\n"))
    req = urllib.request.Request(detector + "/check", data=raw, method="POST", headers={"Content-Type": "message/rfc822"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    status, html, _, _ = http("GET", f"{wired.dash_url}/d/{d['decision_id']}")
    assert status == 200 and ("Call the vendor now" in html or "manual_callback" in html)
    far = dict(SAMPLE, ct_timeline=SAMPLE["ct_timeline"] + [{"domain": "x.com", "kind": "cert_issued", "at": "9999-12-31T23:00:00Z", "label": "x"}])
    assert "<svg" in render.ct_strip(far) or "could not be drawn" in render.review_screen(far)


# 12 -----------------------------------------------------------------------------
def test_12_the_vendors_answer_survives_a_detector_outage(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    real = wired.client.callback_result
    fails = {"n": 2}

    def flaky(*a, **k):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise DetectorError(503, "detector not reachable")
        return real(*a, **k)
    wired.client.callback_result = flaky
    wired.callbacks.retry_delay = 0.2
    _, twiml, headers, _ = http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "we never changed our bank"})
    assert headers["Content-Type"].startswith("text/xml") and "<Hangup/>" in twiml
    assert wired.store.call(token)["result"] == "we never changed our bank"
    for _ in range(50):
        if state(wired, first)["decision"] == "BLOCKED":
            break
        time.sleep(0.1)
    assert state(wired, first)["decision"] == "BLOCKED" and wired.store.call(token)["status"] == "completed"


def test_12_an_answer_that_cannot_be_saved_says_so(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    wired.client.callback_result = lambda *a, **k: (_ for _ in ()).throw(DetectorError(503, "down"))
    wired.callbacks.retry_delay, wired.callbacks.retry_attempts = 0.05, 2
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"Digits": "2"})
    time.sleep(0.5)
    assert wired.store.call(token)["status"] == "unsaved"
    assert "couldn" in wired.call_text(first) and "pressed 2" in wired.call_text(first)


# 13 -----------------------------------------------------------------------------
def test_13_a_failed_or_silent_call_does_not_stick(wired):
    first = demo_callback(wired)
    wired.callbacks._twilio_create = lambda **kw: (_ for _ in ()).throw(ValueError("not JSON"))
    status, html, _, _ = http("POST", f"{wired.dash_url}/d/{first}/call")
    assert "could not be placed" in html
    assert wired.store.latest_call(first)["status"] == "failed"
    dialed = []
    wired.callbacks._twilio_create = lambda **kw: dialed.append(kw) or {"sid": "CA9", "status": "queued"}
    http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(dialed) == 1
    tok = wired.store.latest_call(first)["token"]
    wired.store._con.execute("UPDATE calls SET updated_at = updated_at - 600 WHERE token = ?", (tok,))
    wired.store._con.commit()
    http("POST", f"{wired.dash_url}/d/{first}/call")
    assert len(dialed) == 2 and wired.store.call(tok)["status"] == "stale"


# 14 -----------------------------------------------------------------------------
def test_14_certificate_age_is_measured_on_doris_clock():
    d = json.loads(json.dumps(SAMPLE))
    for e in d["ct_timeline"]:
        if e["kind"] == "invoice_received":
            e["at"] = "2027-06-01T00:00:00Z"                    # the sender's Date header lies
    svg = render.ct_strip(d)
    assert "acmesupply-billing.com got its first certificate" in svg and "has had certificates since Oct 2026" not in svg


# 15 -----------------------------------------------------------------------------
def test_15_odd_headers_and_tokens(opened):
    raw = "From: Jürgen Müller <jm@example.com>\nTo: check@doris.test\nSubject: Rechnung\n\n".encode() + FWD.encode()
    assert opened.door.accept(raw).startswith("replied")
    raw8 = b"From: x@example.com\nAuto-Submitted: \xe9t\xe9\nSubject: s\n\n" + FWD.encode()
    assert opened.door.accept(raw8).startswith("checked, no reply")
    opened.settings.inbound_token = "abc+def/ghi=jkl123456"
    assert post_json(f"{opened.hooks_url}/inbound/abc+def/ghi=jkl123456", postmark(FWD)) == 404
