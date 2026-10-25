"""CRS: an independent check on the email a request came from, against the person on file. Fake CRS server."""
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from surfaces import crs, payments
from surfaces.config import ROOT, Settings, load_key_file
from surfaces.tests.test_payments import Client, FakeNessie, dec

P13_ANSWER = {"risk": {"score": 88, "tumbling_risk": 0, "domain": {"domain_risk_score": 7},
                       "phone": {"line_type": "voip", "prepaid": True, "owner": {"name_match": "no_match"}}},
              "eam": {"date_first_seen": "2026-10-22", "longevity": 0},
              "email_validation": {"address": "kb.9921x@example.com", "status": "valid"}}


class FakeCRS:
    def __init__(self, login_ok=True, answer=None, expire_once=False):
        self.calls, self.login_ok, self.answer, self.expire_once = [], login_ok, answer or P13_ANSWER, expire_once
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append((self.path, body, self.headers.get("Authorization")))
                if self.path == "/api/users/login":
                    ok = outer.login_ok and body == {"username": "u", "password": "p@ss"}
                    self.send_response(200 if ok else 401); self.end_headers()
                    self.wfile.write(json.dumps({"token": "tok1"} if ok else {"codes": ["CRS401"]}).encode())
                    return
                if self.headers.get("Authorization") != "Bearer tok1" or outer.expire_once:
                    outer.expire_once = False
                    self.send_response(401); self.end_headers(); return
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(outer.answer).encode())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api"

    def logins(self):
        return sum(1 for c in self.calls if c[0] == "/api/users/login")


def payroll_decision(**kw):
    d = {"decision_id": "dec_9", "seq": 9, "invoice_id": "PAYROLL-1", "vendor_id": "emp_kayla_brennan", "decision": "CALLBACK",
         "sender_email": "kb.9921x@example.com", "bank_account_new": "****9930", "created_at": "2099-01-01T00:00:00Z"}
    d.update(kw)
    return d


def app_for(tmp_path, fake, ds=(), nessie=None):
    from surfaces.app import Surfaces
    s = Settings(data_dir=tmp_path, crs_username="u", crs_password="p@ss", crs_url=fake.url)
    if nessie:
        s.nessie_key, s.nessie_url = "nk", nessie.url
    app = Surfaces(s, client=Client(list(ds)))
    crs.wire(app)
    return app


RAW = (ROOT / "surfaces" / "demo" / "payroll_change_demo.eml").read_bytes()


def test_the_payroll_demo_sends_cris_exactly_the_p13_persona(tmp_path):
    fake = FakeCRS()
    app = app_for(tmp_path, fake)
    s = app.crs.check(payroll_decision(), RAW)
    path, body, auth = fake.calls[-1]
    assert path == "/api/fraud-finder/fraud-finder" and auth == "Bearer tok1"
    assert body == {"firstName": "KAYLA", "lastName": "BRENNAN", "phoneNumber": "5550101013", "email": "kb.9921x@example.com",
                    "ipAddress": "198.51.100.13", "address": {"addressLine1": "9021 KINGSTON PIKE", "city": "KNOXVILLE",
                                                              "state": "TN", "postalCode": "37923"}}
    assert s["high"] and s["score"] == 88
    text = " ".join(e["text"] for e in app.store.events(["dec_9"]))
    assert "risk 88 of 100, high" in text and "no match with the person on file for: name" in text and "release it" in text


def test_a_high_risk_answer_stops_the_payment_even_after_a_phone_yes(tmp_path):
    fake, n = FakeCRS(), FakeNessie()
    paid = dec(10, "PAY")
    paid["invoice_id"] = "PAYROLL-1"
    app = app_for(tmp_path, fake, ds=[paid, dec(11, "PAY")], nessie=n)
    app.crs.check(payroll_decision(), RAW)
    assert payments.Payments(app).once() == 1, "only the unflagged invoice is paid"
    assert not any("PAYROLL-1" in json.dumps(p[1]) for p in n.posts)
    assert "Not paid: CRS rated" in app.store.events(["dec_10"])[-1]["text"]
    again = app_for(tmp_path, fake)                 # the flag survives a restart
    assert again.crs.flagged("PAYROLL-1")["score"] == 88


def test_a_low_risk_answer_is_shown_but_flags_nothing(tmp_path):
    fake = FakeCRS(answer={"risk": {"score": 11}, "eam": {"date_first_seen": "2019-05-01"}, "email_validation": {"status": "valid"}})
    app = app_for(tmp_path, fake)
    s = app.crs.check(payroll_decision(), RAW)
    assert s["level"] == "low" and not s["high"] and app.crs.flagged("PAYROLL-1") is None


def test_only_people_with_identity_on_file_are_sent_to_crs(tmp_path):
    fake = FakeCRS()
    app = app_for(tmp_path, fake)
    assert app.crs.check(payroll_decision(vendor_id="ven_acme_supply"), RAW) is None
    assert app.crs.check(payroll_decision(bank_account_new=None), RAW) is None
    assert app.crs.check(payroll_decision(sender_email=None), RAW) is None
    assert fake.calls == [], "no record, no bank change or no sender: nothing leaves the building"


def test_a_failed_login_is_never_retried_and_never_shows_the_password(tmp_path):
    fake = FakeCRS(login_ok=False)
    app = app_for(tmp_path, fake)
    app.crs.check(payroll_decision(), RAW)
    app.crs.check(payroll_decision(decision_id="dec_10"), RAW)
    assert fake.logins() == 1, "repeated failed logins lock the sandbox account"
    events = json.dumps(app.store.events(["dec_9", "dec_10"]))
    assert "CRS login failed (HTTP 401, CRS401)" in events and "p@ss" not in events
    assert "p@ss" not in repr(app.crs.crs) and "p@ss" not in repr(app.settings)


def test_one_login_serves_many_checks_and_an_expired_token_logs_in_again(tmp_path):
    fake = FakeCRS()
    app = app_for(tmp_path, fake)
    app.crs.check(payroll_decision(), RAW)
    app.crs.check(payroll_decision(decision_id="dec_10"), RAW)
    assert fake.logins() == 1
    fake.expire_once = True
    assert app.crs.check(payroll_decision(decision_id="dec_11"), RAW)["score"] == 88
    assert fake.logins() == 2


def test_off_without_credentials(tmp_path):
    from surfaces.app import Surfaces
    app = Surfaces(Settings(data_dir=tmp_path), client=Client([]))
    assert crs.wire(app) is None and getattr(app, "crs", None) is None
    crs.after_check(app, payroll_decision(), RAW)    # a no-op, no crash


def test_credentials_come_from_the_users_key_file(tmp_path):
    f = tmp_path / "crs.env"
    f.write_text("# my CRS sign-in\nCRS_USERNAME=someone@example.com\nCRS_PASSWORD=Pa$$w0rd!#x\n")
    assert load_key_file(str(f)) == {"CRS_USERNAME": "someone@example.com", "CRS_PASSWORD": "Pa$$w0rd!#x"}
    f.write_text("CRS_USERNAME=\nCRS_PASSWORD=\n")
    assert load_key_file(str(f)) == {}, "the empty template means off"


def test_sender_ip_and_summary_reading():
    assert crs.sender_ip(RAW) == "198.51.100.13"
    assert crs.sender_ip(b"From: a@b.c\n\nhi") is None
    s = crs.summarize(P13_ANSWER, "kb.9921x@example.com", today=date(2026, 10, 25))
    assert s["email_age_days"] == 3 and "3 days ago" in " ".join(s["lines"])
    assert crs.summarize({}, "x@y.z")["score"] is None
    assert crs.summarize({"risk": {"score": 61}}, "x@y.z")["high"] and not crs.summarize({"risk": {"score": 60}}, "x@y.z")["high"]


from surfaces.tests.test_payroll import demo_stack, send  # noqa: E402,F401  (the real detector on a demo database)


def test_the_payroll_button_puts_the_crs_answer_in_what_doris_did(demo_stack):
    import time
    import urllib.request
    fake = FakeCRS()
    demo_stack.settings.crs_username, demo_stack.settings.crs_password, demo_stack.settings.crs_url = "u", "p@ss", fake.url
    crs.wire(demo_stack)
    _, _, first = send(demo_stack, "payroll")
    for _ in range(50):
        if any("CRS Fraud Finder" in e["text"] for e in demo_stack.store.events([first])):
            break
        time.sleep(0.1)
    sent = [c[1] for c in fake.calls if c[0] == "/api/fraud-finder/fraud-finder"]
    assert sent and sent[0]["email"] == "kb.9921x@example.com" and sent[0]["ipAddress"] == "198.51.100.13"
    page = urllib.request.urlopen(f"{demo_stack.dash_url}/d/{first}", timeout=10).read().decode()
    assert "risk 88 of 100, high" in page and "p@ss" not in page


def test_a_misread_key_file_never_reaches_the_login(tmp_path):
    fake = FakeCRS()
    for user, pw in (("CRS_PASSWORD=", "x"), ("u", "CRS_USERNAME=u"), ("", "p"), ("u", "")):
        c = crs.CRS(user, pw, fake.url)
        try:
            c.token()
            raise AssertionError("should refuse")
        except crs.CRSError:
            pass
    assert fake.logins() == 0
    assert crs.CRS("u", "p@ss", fake.url).token() == "tok1", "a real password may contain = or @"
