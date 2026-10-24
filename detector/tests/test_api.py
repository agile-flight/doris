"""The HTTP surface Agent B talks to."""
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from detector.api import make_server
from detector.config import Settings
from detector.pipeline import Doris
from detector.tests.conftest import FIXTURES

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def base(tmp_path):
    settings = Settings(offline=True, db_path=tmp_path / "doris.db", outbox_dir=tmp_path / "outbox")
    doris = Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: NOW)
    server = make_server(doris, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, content_type="application/json"):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": content_type} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), dict(e.headers)


def test_check_with_raw_email(base):
    status, d, headers = call(base, "POST", "/check", (FIXTURES / "emails" / "bank_change_lookalike.eml").read_bytes(), "message/rfc822")
    assert status == 200 and d["decision"] == "CALLBACK" and d["invoice_id"] == "INV-1042"
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_check_with_json_text(base):
    status, d, _ = call(base, "POST", "/check", {"text": (FIXTURES / "emails" / "forwarded_check.txt").read_text()})
    assert status == 200 and d["decision"] == "CALLBACK" and d["sender_domain"] == "acme-supply.co"


def test_check_with_json_pdf_base64(base):
    import base64
    pdf = base64.b64encode((FIXTURES / "invoices" / "inv_1043_blue_horizon_injection.pdf").read_bytes()).decode()
    body = {"text": "From: Acme Supply <billing@acmesupply.com>\nSubject: Invoice INV-1043\n\nAttached is invoice INV-1043.", "pdf_base64": pdf}
    status, d, _ = call(base, "POST", "/check", body)
    assert status == 200 and d["decision"] == "BLOCKED" and d["payee_name"] == "Blue Horizon Automobile LLC"


def test_list_get_and_follow_ups(base):
    _, first, _ = call(base, "POST", "/check", (FIXTURES / "emails" / "bank_change_lookalike.eml").read_bytes(), "message/rfc822")
    status, listed, _ = call(base, "GET", "/decisions")
    assert status == 200 and listed[0]["decision_id"] == first["decision_id"]
    status, got, _ = call(base, "GET", f"/decisions/{first['decision_id']}")
    assert status == 200 and got == first
    status, follow, _ = call(base, "POST", f"/decisions/{first['decision_id']}/callback_result", {"vendor_confirmed": False, "notes": "we never changed our bank"})
    assert status == 200 and follow["decision"] == "BLOCKED" and follow["supersedes"] == first["decision_id"]
    _, held, _ = call(base, "POST", "/check", (FIXTURES / "emails" / "new_vendor_northwind.eml").read_bytes(), "message/rfc822")
    status, reviewed, _ = call(base, "POST", f"/decisions/{held['decision_id']}/review", {"approve": True, "reviewer": "judge", "notes": ""})
    assert status == 200 and reviewed["decision"] == "PAY"
    status, verify, _ = call(base, "GET", "/audit/verify")
    assert status == 200 and verify["ok"] is True and verify["records"] >= 4
    status, vendors, _ = call(base, "GET", "/vendors")
    assert status == 200 and {v["vendor_id"] for v in vendors} >= {"ven_acme_supply"}
    assert vendors[0]["phone_since"] and "bank_last4" in vendors[0]


def test_errors(base):
    assert call(base, "GET", "/decisions/dec_nope")[0] == 404
    assert call(base, "GET", "/nope")[0] == 404
    status, body, _ = call(base, "POST", "/check", b"{not json", "application/json")
    assert status == 400 and "error" in body
    assert call(base, "POST", "/decisions/dec_nope/callback_result", {"vendor_confirmed": False})[0] == 404
    status, body, _ = call(base, "POST", "/check", {"nothing": 1})
    assert status == 400


def test_health_and_cors_preflight(base):
    status, body, _ = call(base, "GET", "/health")
    assert status == 200 and body["ok"] is True and body["offline"] is True
    req = urllib.request.Request(base + "/check", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 204 and "POST" in resp.headers.get("Access-Control-Allow-Methods", "")


def test_evaluation_route(base, tmp_path, monkeypatch):
    import detector.evaluate as evaluate
    monkeypatch.setattr(evaluate, "DEFAULT_OUT", tmp_path / "cm.json")
    assert call(base, "GET", "/evaluation")[0] == 404
    (tmp_path / "cm.json").write_text(json.dumps({"metrics": {"catch_rate": 0.93}, "rows": []}))
    status, body, _ = call(base, "GET", "/evaluation")
    assert status == 200 and body["metrics"]["catch_rate"] == 0.93
