"""Doris moves the money: PAY is paid once through the Nessie sandbox; BLOCKED sends nothing."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from surfaces import payments
from surfaces.config import Settings


class FakeNessie:
    def __init__(self, fail=0):
        self.posts, self.fail = [], fail
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if outer.fail > 0:
                    outer.fail -= 1
                    self.send_response(503); self.end_headers(); return
                outer.posts.append((self.path.split("?")[0], body, "key=nk" in self.path))
                oid = f"id{len(outer.posts):04d}" + "x" * 20
                self.send_response(201); self.end_headers()
                self.wfile.write(json.dumps({"code": 201, "objectCreated": {"_id": oid}}).encode())
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"


class Client:
    def __init__(self, ds):
        self.ds = ds

    def list(self, limit=30):
        return self.ds


def app_with(tmp_path, nessie, ds):
    from surfaces.app import Surfaces
    return Surfaces(Settings(data_dir=tmp_path, nessie_key="nk", nessie_url=nessie.url), client=Client(ds))


def dec(i, decision, amount=4820.0, created="2099-01-01T00:00:00Z"):
    return {"decision_id": f"dec_{i}", "seq": i, "decision": decision, "amount": amount, "currency": "USD", "invoice_id": f"INV-{i}",
            "payee_name": "Acme Supply Co.", "bank_account_new": "****8841", "created_at": created}


def test_pay_is_paid_once_and_lands_in_the_trace(tmp_path):
    n = FakeNessie()
    app = app_with(tmp_path, n, [dec(1, "PAY")])
    p = payments.Payments(app)
    assert p.once() == 1 and p.once() == 0, "paid once"
    paths = [x[0] for x in n.posts]
    assert paths[0] == "/customers" and paths[1].endswith("/accounts") and paths[2].endswith("/transfers")
    body = n.posts[2][1]
    assert body["amount"] == 4820.0 and body["status"] == "pending" and "INV-1" in body["description"] and "8841" in body["description"]
    assert all(x[2] for x in n.posts), "the key goes with every call"
    ev = app.store.events(["dec_1"])
    assert ev and "Paid through Capital One" in ev[-1]["text"] and "4,820.00" in ev[-1]["text"]
    p2 = payments.Payments(app_with(tmp_path, n, [dec(2, "PAY")]))
    p2.once()
    assert sum(1 for x in n.posts if x[0] == "/customers") == 1, "the payer account is created once and remembered"


def test_blocked_sends_nothing_and_says_so(tmp_path):
    n = FakeNessie()
    app = app_with(tmp_path, n, [dec(1, "BLOCKED"), dec(2, "HOLD"), dec(3, "CALLBACK")])
    payments.Payments(app).once()
    assert n.posts == []
    assert "Nothing sent" in app.store.events(["dec_1"])[-1]["text"] and app.store.events(["dec_2", "dec_3"]) == []


def test_history_from_before_the_server_started_is_never_paid(tmp_path):
    n = FakeNessie()
    app = app_with(tmp_path, n, [dec(1, "PAY", created="2020-01-01T00:00:00Z")])
    assert payments.Payments(app).once() == 0 and n.posts == []


def test_no_amount_means_nothing_sent(tmp_path):
    n = FakeNessie()
    app = app_with(tmp_path, n, [dec(1, "PAY", amount=None)])
    payments.Payments(app).once()
    assert n.posts == [] and "no amount" in app.store.events(["dec_1"])[-1]["text"]


def test_a_failed_payment_is_retried_then_handed_to_a_person(tmp_path):
    n = FakeNessie(fail=1)
    app = app_with(tmp_path, n, [dec(1, "PAY")])
    p = payments.Payments(app)
    assert p.once() == 0 and p.once() == 1
    n2 = FakeNessie(fail=99)
    app2 = app_with(tmp_path / "b", n2, [dec(1, "PAY")])
    p2 = payments.Payments(app2, retries=2)
    p2.once(); p2.once(); p2.once()
    assert "pay it by hand" in app2.store.events(["dec_1"])[-1]["text"]


def test_off_without_a_key(tmp_path):
    from surfaces.app import Surfaces
    assert payments.wire(Surfaces(Settings(data_dir=tmp_path))) is None



def test_the_same_invoice_is_never_paid_twice_even_after_a_restart(tmp_path):
    n = FakeNessie()
    a, b = dec(1, "PAY"), dec(2, "PAY")
    b["invoice_id"] = a["invoice_id"]
    app = app_with(tmp_path, n, [a, b])
    assert payments.Payments(app).once() == 1
    assert sum(1 for x in n.posts if x[0].endswith("/transfers")) == 1
    assert "Duplicate stopped" in app.store.events(["dec_2"])[-1]["text"]
    c = dec(3, "PAY")
    c["invoice_id"] = a["invoice_id"]
    payments.Payments(app_with(tmp_path, n, [c])).once()
    assert sum(1 for x in n.posts if x[0].endswith("/transfers")) == 1, "the paid list survives a restart"
