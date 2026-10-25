"""The shared desk on SpacetimeDB: what surfaces writes, with what identity, and what pages subscribe to."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from surfaces import desk, render
from surfaces.config import Settings
from surfaces.tests.conftest import ROOT, http

SAMPLES = [json.loads(p.read_text()) for p in sorted((ROOT / "contract" / "samples").glob("*.json"))]


class FakeSpacetime:
    """Records reducer calls; refuses any write without the right token, like the real module."""

    def __init__(self, token="doris-token", fail_first=0):
        self.calls, self.token, self.fail_first = [], token, fail_first
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if outer.fail_first > 0:
                    outer.fail_first -= 1
                    self.send_response(503); self.end_headers(); return
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_response(530); self.end_headers(); self.wfile.write(b"only Doris writes to the desk"); return
                outer.calls.append((self.path, body))
                self.send_response(200); self.end_headers()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def wait(self, n, timeout=5):
        t0 = time.time()
        while len(self.calls) < n and time.time() - t0 < timeout:
            time.sleep(0.05)
        return self.calls


class FakeClient:
    def __init__(self, decisions):
        self.decisions = decisions

    def list(self, limit=50):
        return list(self.decisions)


def make_app(tmp_path, st, decisions=SAMPLES, token="doris-token"):
    from surfaces.app import Surfaces
    app = Surfaces(Settings(data_dir=tmp_path, spacetime_url=st.url, spacetime_token=token, spacetime_db="doris"), client=FakeClient(decisions))
    return app


def test_decision_row_carries_only_what_screens_need():
    row = desk.decision_row(next(d for d in SAMPLES if d["decision"] == "BLOCKED" and d.get("supersedes")))
    assert set(row) == {"decisionId", "invoiceId", "decision", "supersedes", "riskScore", "headline", "vendorClaimed", "amount", "createdAt", "seq"}
    assert row["decision"] == "BLOCKED" and row["supersedes"] and isinstance(row["seq"], int) and isinstance(row["riskScore"], float)
    assert desk.decision_row({"decision_id": "x"})["supersedes"] == "" and desk.decision_row({"decision_id": "x"})["amount"] == ""


def test_every_decision_is_mirrored_once_with_dorises_token(tmp_path):
    st = FakeSpacetime()
    app = make_app(tmp_path, st)
    d = desk.wire(app)
    calls = st.wait(len(SAMPLES))
    assert len(calls) == len(SAMPLES)
    assert all(path == "/v1/database/doris/call/upsert_decision" for path, _ in calls)
    assert {b["decisionId"] for _, b in calls} == {x["decision_id"] for x in SAMPLES}
    time.sleep(1.5)
    assert len(st.calls) == len(SAMPLES), "nothing is written twice"
    seqs = [b["seq"] for _, b in calls]
    assert seqs == sorted(seqs), "oldest first, so a follow-up never lands before what it supersedes"
    d.stop()


def test_a_failed_write_is_retried(tmp_path):
    st = FakeSpacetime(fail_first=2)
    app = make_app(tmp_path, st, decisions=SAMPLES[:1])
    d = desk.wire(app)
    assert len(st.wait(1, timeout=6)) == 1
    d.stop()


def test_call_lines_reach_the_desk(tmp_path):
    st = FakeSpacetime()
    app = make_app(tmp_path, st, decisions=[])
    d = desk.wire(app)
    app.store.add_event("dec_1", "vendor", "We never changed our bank.")
    path, body = st.wait(1)[0]
    assert path.endswith("/add_call_event") and body["decisionId"] == "dec_1" and body["who"] == "vendor"
    assert body["text"] == "We never changed our bank." and body["at"] > 0
    d.stop()


def test_the_wrong_identity_cannot_write(tmp_path):
    st = FakeSpacetime(token="the-real-one")
    app = make_app(tmp_path, st, decisions=SAMPLES[:1], token="a-guess")
    d = desk.wire(app)
    time.sleep(1.5)
    assert st.calls == [] and d.failures >= 1
    d.stop()


def test_desk_is_off_without_a_url_or_token(tmp_path):
    from surfaces.app import Surfaces
    assert desk.wire(Surfaces(Settings(data_dir=tmp_path / "a"))) is None
    assert desk.wire(Surfaces(Settings(data_dir=tmp_path / "b", spacetime_url="http://127.0.0.1:3000"))) is None


def test_pages_subscribe_only_when_the_desk_is_on(monkeypatch):
    d = next(x for x in SAMPLES if x["decision"] == "CALLBACK")
    monkeypatch.setattr(render, "DESK_CONFIG", None)
    html = render.review_screen(d, chain=[d])
    assert "doris-desk" not in html and "/static/desk.js" not in html and f'data-chain="{d["decision_id"]}"' in html
    monkeypatch.setattr(render, "DESK_CONFIG", {"uri": "ws://127.0.0.1:3000", "db": "doris"})
    html = render.review_screen(d, chain=[d])
    assert 'name="doris-desk"' in html and '<script type="module" src="/static/desk.js"></script>' in html
    assert "doris:changed" in html, "the page refreshes the moment the desk pushes"
    assert "/static/desk.js" not in render.samples_screen(["x"]), "pages that don't refresh don't subscribe"


def test_dashboard_serves_the_bundle(surfaces):
    status, body, headers, _ = http("GET", surfaces.dash_url + "/static/desk.js")
    assert status == 200 and headers["Content-Type"].startswith("application/javascript") and len(body) > 10_000
    assert http("GET", surfaces.hooks_url + "/static/desk.js")[0] == 404, "not on the public port"


def test_the_subscriber_never_writes_html():
    src = (ROOT / "surfaces/spacetime/client/desk.ts").read_text()
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src and "textContent" in src
    assert "doris:changed" in src
