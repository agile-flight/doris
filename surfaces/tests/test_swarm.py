"""Defences for the bounty, assuming a swarm of capable agents doing their worst."""
import socket
import threading
import time

import pytest

from surfaces import door
from surfaces.app import BoundedServer, DashboardHandler, Surfaces, make_servers
from surfaces.config import Settings
from surfaces.tests.conftest import ROOT, http
from surfaces.tests.test_door import FWD, TOKEN, opened, postmark, wait_for_reply  # noqa: F401  (fixture)

SPF_PASS = "Pass (sender SPF authorized) identity=mailfrom; client-ip=209.85.220.41; envelope-from=friend@example.com; receiver=inbound.postmarkapp.com"


def signed(text=FWD, sender="friend@example.com", spf=SPF_PASS, extra_headers=(), **kw):
    headers = ([{"Name": "Received-SPF", "Value": spf}] if spf else []) + list(extra_headers)
    return door.postmark_to_raw(postmark(text, sender=sender, Headers=headers, **kw))


@pytest.fixture
def guarded(opened):
    opened.settings.door_auth = "spf"
    return opened


def test_unverified_senders_get_nothing_and_cost_nothing(guarded, detector):
    import json, urllib.request
    before = len(json.loads(urllib.request.urlopen(detector + "/decisions?limit=200", timeout=10).read()))
    assert guarded.door.accept(signed(spf=None)) == "dropped: sender not verified"
    assert guarded.door.accept(signed(spf="Fail (not authorized) envelope-from=friend@example.com")) == "dropped: sender not verified"
    assert len(json.loads(urllib.request.urlopen(detector + "/decisions?limit=200", timeout=10).read())) == before
    assert wait_for_reply(guarded, timeout=1)[0] == []


def test_a_forged_from_cannot_make_doris_mail_a_victim(guarded):
    # The attacker's own server passes SPF for attacker.example, but From says victim@bank.example.
    forged = signed(sender="victim@bank.example", spf=SPF_PASS.replace("friend@example.com", "x@attacker.example"))
    assert guarded.door.accept(forged) == "dropped: sender not verified"
    # A Received-SPF the attacker wrote sits below the receiving server's own, which is the one read.
    stacked = signed(sender="victim@bank.example", spf="Fail (not authorized) envelope-from=victim@bank.example",
                     extra_headers=[{"Name": "Received-SPF", "Value": SPF_PASS.replace("friend@example.com", "victim@bank.example")}])
    assert guarded.door.accept(stacked) == "dropped: sender not verified"


def test_a_verified_sender_gets_a_reply(guarded):
    assert guarded.door.accept(signed()).startswith("replied")
    assert len(wait_for_reply(guarded)[0]) == 1


def test_authentication_results_mode(guarded):
    guarded.settings.door_auth = "authres:mx.doris.test"
    ok = [{"Name": "Authentication-Results", "Value": "mx.doris.test; dkim=pass header.d=example.com; spf=pass smtp.mailfrom=example.com"}]
    assert guarded.door.accept(signed(spf=None, extra_headers=ok)).startswith("replied")
    other = [{"Name": "Authentication-Results", "Value": "evil.example; dmarc=pass"}]
    assert guarded.door.accept(signed(spf=None, sender="b@example.com", extra_headers=other)) == "dropped: sender not verified"
    guarded.settings.door_auth = "authres:"
    assert guarded.door.accept(signed(spf=None, sender="c@example.com", extra_headers=ok)) == "dropped: sender not verified"


def test_links_never_travel_in_a_reply():
    d = {"decision": "BLOCKED", "risk_score": 0.95, "vendor_id": None, "decision_id": "dec_x", "signals": [],
         "reasons": ['Payee "Claim your $500 at https://phish.example/win now" is not the vendor on file', "see www.evil.example/x"],
         "llm_rationale": "Visit http://evil.example"}
    text = door.reply_text(d, "scam", "https://doris.test/feedback/t")
    assert "phish.example" not in text and "evil.example" not in text and text.count("[link removed]") == 3
    assert "https://doris.test/feedback/t/up" in text, "Doris's own feedback links stay"


def test_daily_budget_caps_spend(opened):
    opened.settings.door_daily = 2
    for i in range(2):
        assert opened.door.accept(door.postmark_to_raw(postmark(FWD, sender=f"u{i}@example.com"))).startswith("replied")
    assert opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="u9@example.com"))) == "dropped: daily budget"


def test_concurrency_cap_on_detector_calls(opened):
    opened.door = door.Door(opened)                 # pick up a cap of 2
    opened.settings.door_concurrency = 2
    opened.door = door.Door(opened)
    live, peak, lock = [0], [0], threading.Lock()
    real = opened.client.check_raw

    def slow(raw):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.3)
        try:
            return real(raw)
        finally:
            with lock:
                live[0] -= 1
    opened.client.check_raw = slow
    threads = [threading.Thread(target=opened.door.accept, args=(door.postmark_to_raw(postmark(FWD, sender=f"c{i}@example.com")),)) for i in range(6)]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    assert peak[0] <= 2


def test_connection_flood_is_shed_not_queued(tmp_path):
    app = Surfaces(Settings(detector_url="http://127.0.0.1:9", port=0, hooks_port=0, data_dir=tmp_path, max_connections=3))
    dash, hooks = make_servers(app)
    threading.Thread(target=dash.serve_forever, daemon=True).start()
    port = dash.server_address[1]
    idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]      # hold every slot
    time.sleep(0.2)
    extra = socket.create_connection(("127.0.0.1", port), timeout=3)
    try:
        extra.sendall(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        got = extra.recv(10)
    except (ConnectionResetError, BrokenPipeError):
        got = b""
    assert got == b"", "the extra connection is closed or reset, never served"
    for s in idle:
        s.close()
    time.sleep(1.5)
    assert http("GET", f"http://127.0.0.1:{port}/")[0] == 200, "the server recovers once the flood stops"
    dash.shutdown(); dash.server_close(); hooks.server_close()


def test_ap_inbox_takes_only_allowlisted_senders_and_never_calls_in_a_loop(opened):
    raw = (ROOT / "surfaces/demo/bank_change_demo.eml").read_bytes()
    calls = []
    opened.callbacks = type("C", (), {"start": lambda self, d: (calls.append(d), opened.store.add_call(f"tok{len(calls):020d}", d["decision_id"], "+1"))})()
    opened.settings.auto_call = True
    opened.settings.ap_allowed = ("teammate@calhacksrobotics.com",)
    assert opened.door.accept(raw, mode="ap") == "dropped: not on the AP allowlist"
    allowed = raw.replace(b"billing@acmesupply-billing.com", b"teammate@calhacksrobotics.com")
    assert opened.door.accept(allowed, mode="ap") == "ap: CALLBACK"
    assert opened.door.accept(allowed, mode="ap") == "ap: CALLBACK"
    assert len(calls) == 1, "a second CALLBACK inside the cooldown doesn't ring the table again"


def test_roles_keep_the_bounty_away_from_the_demo(tmp_path):
    from surfaces import callback
    for role, door_open, ap_open, phone in (("bounty", True, False, False), ("demo", False, True, True)):
        app = Surfaces(Settings(role=role, detector_url="http://127.0.0.1:9", port=0, hooks_port=0, data_dir=tmp_path / role,
                                inbound_token=TOKEN, extra={"ap_inbound_token": TOKEN}))
        if role == "demo":
            callback.wire(app)
        door.wire(app)
        paths = [p for _, p, _ in app.hook_routes]
        assert any(p.startswith("/inbound/") for p in paths) is door_open
        assert any(p.startswith("/inbound-ap/") for p in paths) is ap_open
        assert any(p.startswith("/twilio/") for p in paths) is phone


def test_stats_count_people_not_just_emails(opened):
    for i in range(3):
        opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="same@example.com", subject=f"s{i}")))
    opened.door.accept(door.postmark_to_raw(postmark(FWD, sender="other@example.com")))
    stats = opened.store.stats()
    assert stats["checks"] == 4 and stats["senders"] == 2
