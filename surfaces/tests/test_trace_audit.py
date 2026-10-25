"""The live trace of what Doris did (with the call transcript) and the audit packet."""
import json
from pathlib import Path

import pytest

from surfaces import auditview, render
from surfaces.tests.conftest import ROOT, http
from surfaces.tests.test_callback import demo_callback, token_of, wired  # noqa: F401  (fixture)
from surfaces.tests.test_render import balanced

SAMPLES = sorted((ROOT / "contract" / "samples").glob("*.json"))


def state(app, decision_id):
    return json.loads(http("GET", f"{app.dash_url}/d/{decision_id}/state")[1])


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_trace_renders_for_every_sample(path):
    d = json.loads(path.read_text())
    html = render.trace_panel([d])
    assert balanced(html) and html.count("<li ") >= 5 and "Ran " in html and "checks" in html


def test_the_call_is_transcribed_live_and_survives_the_flip(wired):
    first = demo_callback(wired)
    before = state(wired, first)["trace_count"]
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/voice")
    mid = state(wired, first)
    assert mid["trace_count"] > before, "the page learns that new lines exist"
    frag = http("GET", f"{wired.dash_url}/d/{first}/trace")[1]
    assert "the number on file" in frag and "picked up" in frag and "did your company change its bank account" in frag
    assert 'class="doris live"' in frag, "the newest line pulses while the call is live"
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "We never changed our bank."})
    after = state(wired, first)
    blocked = after["latest_id"]
    page = http("GET", f"{wired.dash_url}/d/{blocked}")[1]
    assert "blocked-screen" in page and "What Doris did" in page
    trace = page[page.index('<ol class="trace"'):]
    trace = trace[:trace.index("</ol>")]
    order = [trace.index(s) for s in ("Arrived from", "Ran ", "did your company change", "We never changed our bank.",
                                     "stopping the payment", "Doris recorded the answer: BLOCKED", "Result")]
    assert order == sorted(order), "the story reads top to bottom"


def test_spoken_markup_is_escaped_in_the_transcript(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/call")
    token = token_of(wired)
    http("POST", f"{wired.hooks_url}/twilio/{token}/gather", {"SpeechResult": "<script>alert(1)</script>"})
    frag = http("GET", f"{wired.dash_url}/d/{first}/trace")[1]
    assert "<script>" not in frag and "&lt;script&gt;" in frag


def test_a_manual_callback_is_on_the_record(wired):
    first = demo_callback(wired)
    _, page, _, _ = http("POST", f"{wired.dash_url}/d/{first}/manual_callback", {"confirmed": "no", "notes": "never changed it"})
    assert "The AP clerk called the number on file" in page and "never changed it" in page


def test_audit_packet_downloads_and_recomputes_every_hash(wired):
    first = demo_callback(wired)
    http("POST", f"{wired.dash_url}/d/{first}/manual_callback", {"confirmed": "no"})
    latest = state(wired, first)["latest_id"]
    status, body, headers, _ = http("GET", f"{wired.dash_url}/d/{latest}/audit.json")
    assert status == 200 and headers["Content-Disposition"].startswith('attachment; filename="doris-audit-INV-1042')
    pk = json.loads(body)
    assert pk["outcome"] == "BLOCKED" and len(pk["decisions"]) == 2
    assert all(r["matches"] for r in pk["recomputed_here"]) and pk["detector_log_verification"]["ok"] is True
    assert any("AP clerk" in e["text"] for e in pk["call_transcript"]) and "sha256" in pk["verify_in_python"]
    exec(pk["verify_in_python"], ns := {})
    assert all(ns["check"](d) for d in pk["decisions"]), "the snippet in the packet works as printed"
    status, html, _, _ = http("GET", f"{wired.dash_url}/d/{latest}/audit")
    assert status == 200 and balanced(html) and "recomputed by this dashboard" in html and html.count(">matches<") == 2
    assert "Check it yourself" in html and "window.print()" in html


def test_recompute_catches_a_tampered_record():
    d = json.loads((ROOT / "contract/samples/callback_denied_blocked.json").read_text())
    assert auditview.recompute(d)
    d["decision"] = "PAY"
    assert not auditview.recompute(d)
    assert not auditview.recompute({"decision": "PAY"})
