"""Slice check: the screens render every file in contract/samples without errors."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from surfaces import render

SAMPLES = sorted((Path(__file__).resolve().parents[2] / "contract" / "samples").glob("*.json"))


def load(name):
    return json.loads(next(p for p in SAMPLES if p.stem == name).read_text())


class Strict(HTMLParser):
    """Fails on unbalanced tags among the ones the renderer emits."""
    TRACK = {"section", "div", "table", "tr", "td", "th", "ol", "ul", "li", "form", "svg", "p", "h1", "h2", "main", "header", "nav"}

    def __init__(self):
        super().__init__()
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in self.TRACK:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.TRACK:
            assert self.stack and self.stack[-1] == tag, f"</{tag}> closes <{self.stack[-1] if self.stack else None}>"
            self.stack.pop()


def balanced(html):
    p = Strict()
    p.feed(html)
    assert p.stack == [], f"unclosed: {p.stack}"
    return True


@pytest.mark.parametrize("path", SAMPLES, ids=[p.stem for p in SAMPLES])
def test_every_sample_renders(path):
    d = json.loads(path.read_text())
    html = render.review_screen(d, chain=[d], audit_ok=True, call_text="", can_call=True)
    assert balanced(html)
    assert render.esc(d["invoice_id"]) in html and d["audit_hash"] in html
    for reason in d["reasons"]:
        assert render.esc(reason) in html
    if d["decision"] == "BLOCKED":
        assert "blocked-screen" in html and ">BLOCKED<" in html
    else:
        assert render.VERDICT[d["decision"]][0] in html


def test_blocked_screen_shows_headline_and_audit_trail():
    d = load("callback_denied_blocked")
    html = render.blocked_screen(d, chain=[load("bank_change_callback"), d], audit_ok=True)
    assert render.esc(d["reasons"][0]) in html
    assert "Chain verified" in html and html.count('class="hash"') == 2


def test_callback_panel_shows_number_on_file_and_actions():
    d = load("bank_change_callback")
    html = render.review_screen(d, call_text="Ringing", can_call=True)
    assert "+1 (415) 555-0123" in html and "on file since" in html
    assert f'/d/{d["decision_id"]}/call' in html and "manual_callback" in html and "Ringing" in html
    assert render.review_screen(d, can_call=False).count("Call the vendor now") == 0


def test_hold_screen_offers_review():
    html = render.review_screen(load("new_vendor_hold"))
    assert "/review" in html and "Approve payment" in html


def test_prompt_injection_screen_quotes_the_hidden_text_as_evidence():
    d = load("prompt_injection_blocked")
    html = render.review_screen(d)
    assert "Hidden text Doris found" in html and "pre-approved" in html


def test_ct_strip_marks_the_young_lookalike():
    d = load("bank_change_callback")
    svg = render.ct_strip(d)
    assert "acmesupply-billing.com" in svg and "acmesupply.com" in svg
    assert re.search(r'class="bad"[^>]*>acmesupply-billing\.com', svg)
    assert "got its first certificate" in svg and "has had certificates since" in svg
    assert re.search(r"Last \d+ days", svg) and "Invoice" in svg


def test_ct_strip_handles_empty_and_single_domain():
    assert render.ct_strip({"ct_timeline": []}) == ""
    one = {"ct_timeline": [{"domain": "x.com", "kind": "invoice_received", "at": "2026-10-22T09:03:00Z", "label": "Invoice"}], "sender_domain": "x.com"}
    assert "<svg" in render.ct_strip(one)


def test_attacker_strings_are_escaped():
    d = load("bank_change_callback")
    evil = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
    d.update(payee_name=evil, vendor_claimed=evil, reasons=[evil], llm_rationale=evil, invoice_id=evil)
    d["signals"][0]["detail"] = evil
    d["ct_timeline"][0]["domain"] = evil
    html = render.review_screen(d)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert balanced(html)


def test_queue_and_samples_screens():
    ds = [json.loads(p.read_text()) for p in SAMPLES]
    html = render.queue_screen(ds, detector_ok=True, stats={"checks": 3, "scam": 1, "suspicious": 1, "up": 2, "down": 0})
    assert balanced(html) and "Detector connected" in html and "3 emails checked" in html
    latest = render._latest_per_invoice(ds)
    assert all(d["decision_id"] in html for d in latest), "one row per invoice: where it stands now"
    assert len(latest) <= len(ds)
    assert "not reachable" in render.queue_screen([], detector_ok=False)
    assert balanced(render.samples_screen([p.stem for p in SAMPLES]))


def test_money_and_phone_formatting():
    assert render.money(4820, "USD") == "$4,820.00"
    assert render.money(None, "USD") == "Amount not found"
    assert render.money(10, "JPY") == "10.00 JPY"
    assert render.pretty_phone("+14155550123") == "+1 (415) 555-0123"
    assert render.pretty_phone("+442071234567") == "+442071234567"



def test_queue_summary_and_vendors_page():
    from surfaces import render
    ds = [{"decision_id": "a", "seq": 1, "invoice_id": "I1", "decision": "CALLBACK", "amount": 4820.0},
          {"decision_id": "b", "seq": 2, "invoice_id": "I1", "decision": "BLOCKED", "amount": 4820.0},
          {"decision_id": "c", "seq": 3, "invoice_id": "I2", "decision": "PAY", "amount": 1000.0},
          {"decision_id": "d", "seq": 4, "invoice_id": "I3", "decision": "HOLD", "amount": None}]
    strip = render.kpi_strip(ds)
    assert "$4,820.00" in strip and "1 payment blocked" in strip and "$1,000.00" in strip and "Verifying now" in strip
    assert ">0<" in strip, "the superseded CALLBACK is not counted as still calling"
    html = render.vendors_screen([{"vendor_id": "ven_x", "name": "Tannerfield Supply Co.", "domain": "tannerfieldsupply.com",
                                   "phone": "+16695551234", "phone_since": "2021-03-10T17:22:00Z", "bank_last4": "1234",
                                   "bank_since": "2021-03-10T17:22:00Z", "payment_history": [4400.0, 4650.0]},
                                  {"vendor_id": "emp_y", "name": "Kayla Brennan", "domain": "marlowerobotics.com",
                                   "phone": "+16695559999", "phone_since": "2099-01-01T00:00:00Z", "bank_last4": "4417",
                                   "payment_history": []}])
    assert balanced(html) and "(•••) •••-1234" in html and "6695551234" not in html and "Trusted for call-backs" in html
    assert "Changed recently: not used" in html and "$4,650.00" in html and "Employee" in html
