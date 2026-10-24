"""End to end, offline: email in, Decision out, chained, written to the outbox, valid against the contract."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from detector.audit import verify_decision
from detector.config import Settings
from detector.pipeline import Doris
from detector.tests.conftest import FIXTURES

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contract" / "decision.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def valid(decision: dict) -> bool:
    errors = list(VALIDATOR.iter_errors(decision))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors)
    assert verify_decision(decision)
    return True


@pytest.fixture
def doris(tmp_path) -> Doris:
    settings = Settings(offline=True, db_path=tmp_path / "doris.db", outbox_dir=tmp_path / "outbox")
    return Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: NOW)


def raw(name: str):
    path = FIXTURES / "emails" / name
    return path.read_bytes() if path.suffix == ".eml" else path.read_text()


def signal(decision, name):
    return next(s for s in decision["signals"] if s["name"] == name)


def test_clean_invoice_pays(doris, tmp_path):
    d = doris.check(raw("clean_acme.eml"))
    assert valid(d)
    assert d["decision"] == "PAY" and d["risk_score"] < 0.3
    assert d["invoice_id"] == "INV-1041" and d["vendor_id"] == "ven_acme_supply"
    assert d["vendor_claimed"] == "Acme Supply" and d["payee_name"] == "Acme Supply Co." and d["amount"] == 4820.0
    assert d["bank_account_new"] is None and d["bank_account_on_file"] == "****1234"
    assert d["callback_number"] == "+14155550123" and d["callback_number_on_file_since"] == "2021-03-10T17:22:00Z"
    assert d["seq"] >= 1 and d["supersedes"] is None and d["created_at"] == "2026-10-23T12:00:00Z"
    assert d["llm_rationale"] is None
    llm = signal(d, "llm_judgment")
    assert llm["fired"] is False and llm["weight"] == "residual" and llm["detail"].startswith("skipped")
    assert (tmp_path / "outbox" / f"{d['decision_id']}.json").exists()
    assert json.loads((tmp_path / "outbox" / f"{d['decision_id']}.json").read_text()) == d


def test_bank_change_lookalike_calls_back(doris):
    d = doris.check(raw("bank_change_lookalike.eml"))
    assert valid(d)
    assert d["decision"] == "CALLBACK" and d["risk_score"] >= 0.7
    assert d["bank_account_new"] == "****8841" and d["bank_account_on_file"] == "****1234"
    assert d["sender_domain"] == "acmesupply-billing.com" and d["vendor_id"] == "ven_acme_supply"
    fired = {s["name"] for s in d["signals"] if s["fired"]}
    assert {"bank_account_changed", "lookalike_domain", "new_domain_certificate", "urgency_language"} <= fired
    kinds = {(e["domain"], e["kind"]) for e in d["ct_timeline"]}
    assert ("acmesupply-billing.com", "cert_first_seen") in kinds and ("acmesupply.com", "cert_first_seen") in kinds
    assert d["ct_timeline"][-1]["kind"] == "invoice_received"
    assert d["reasons"][0] == "Bank account change requested: ****1234 to ****8841"
    assert d["reasons"][-1] == "Doris is calling +1 415 555 0123, on file since March 2021"


def test_prompt_injection_blocks_at_maximum_risk(doris):
    d = doris.check(raw("prompt_injection.eml"))
    assert valid(d)
    assert d["decision"] == "BLOCKED" and d["risk_score"] >= 0.95
    assert d["payee_name"] == "Blue Horizon Automobile LLC"
    pi = signal(d, "prompt_injection")
    assert pi["fired"] and pi["detail"].startswith("white text, line 14")
    assert signal(d, "payee_name_mismatch")["fired"] and signal(d, "bank_account_changed")["fired"]
    assert d["reasons"][0].startswith("Hidden white text on line 14")


def test_new_vendor_holds(doris):
    d = doris.check(raw("new_vendor_northwind.eml"))
    assert valid(d)
    assert d["decision"] == "HOLD" and d["vendor_id"] is None and d["callback_number"] is None
    assert signal(d, "new_vendor")["fired"] and d["bank_account_on_file"] is None
    assert d["reasons"][-1] == "Human review required before the first payment"
    assert any(e["kind"] == "cert_first_seen" and e["domain"] == "northwindtraders.com" for e in d["ct_timeline"])


def test_forwarded_text_from_the_front_door(doris):
    d = doris.check(raw("forwarded_check.txt"))
    assert valid(d)
    assert d["decision"] == "CALLBACK"
    assert d["sender_domain"] == "acme-supply.co" and d["vendor_id"] == "ven_acme_supply"
    assert d["invoice_id"] == "INV-1046" and d["amount"] == 12400.0 and d["bank_account_new"] == "****9930"
    fired = {s["name"] for s in d["signals"] if s["fired"]}
    assert {"bank_account_changed", "lookalike_domain", "new_domain_certificate", "urgency_language", "amount_anomaly"} <= fired


def test_invoice_id_falls_back_to_a_hash_when_no_number(doris):
    d = doris.check("From: Someone <x@example.com>\n\nplease pay us $500.00 to account number 123456789")
    assert valid(d)
    assert d["invoice_id"].startswith("inv_") and len(d["invoice_id"]) == 16


def test_callback_denied_blocks_and_supersedes(doris):
    first = doris.check(raw("bank_change_lookalike.eml"))
    follow = doris.resolve_callback(first["decision_id"], vendor_confirmed=False, notes="we never changed our bank")
    assert valid(follow)
    assert follow["decision"] == "BLOCKED" and follow["risk_score"] == 0.99
    assert follow["supersedes"] == first["decision_id"] and follow["invoice_id"] == first["invoice_id"]
    assert follow["seq"] == first["seq"] + 1 and follow["prev_hash"] == first["audit_hash"]
    cb = signal(follow, "callback_result")
    assert cb["fired"] and cb["weight"] == "gate" and "we never changed our bank" in cb["detail"]
    assert cb["evidence"]["vendor_confirmed"] is False and cb["evidence"]["called"] == "+14155550123"
    assert follow["reasons"][0].startswith("Vendor AP at the number on file said")
    assert doris.verify().ok
    listed = doris.list_decisions()
    assert [x["decision_id"] for x in listed[:2]] == [follow["decision_id"], first["decision_id"]]
    assert doris.get_decision(first["decision_id"]) == first


def test_callback_confirmed_pays_and_updates_the_record(doris):
    first = doris.check(raw("bank_change_lookalike.eml"))
    follow = doris.resolve_callback(first["decision_id"], vendor_confirmed=True, notes="confirmed by Dana at Acme")
    assert valid(follow)
    assert follow["decision"] == "PAY" and follow["supersedes"] == first["decision_id"]
    vendor = doris.vendors.get("ven_acme_supply")
    assert vendor.bank_last4 == "8841" and vendor.bank_since == NOW
    assert vendor.routing_last4 == "9593"
    edits = doris.audit.records(kind="vendor_edit")
    fields = {e.payload["field"] for e in edits}
    assert "bank_last4" in fields and "routing_last4" in fields
    bank_edit = next(e for e in edits if e.payload["field"] == "bank_last4")
    assert bank_edit.payload["source"].startswith("callback confirmed")
    routing_edit = next(e for e in edits if e.payload["field"] == "routing_last4")
    assert routing_edit.payload["source"].startswith("callback confirmed")
    again = doris.check(raw("bank_change_lookalike.eml"))
    assert not signal(again, "bank_account_changed")["fired"]
    assert doris.verify().ok


def test_human_review_resolves_a_hold(doris):
    held = doris.check(raw("new_vendor_northwind.eml"))
    assert held["decision"] == "HOLD"
    approved = doris.resolve_review(held["decision_id"], approve=True, reviewer="amartya", notes="PO matches")
    assert valid(approved) and approved["decision"] == "PAY" and approved["supersedes"] == held["decision_id"]
    hr = signal(approved, "human_review")
    assert hr["fired"] and hr["weight"] == "gate" and "amartya" in hr["detail"]
    # a decision can only be resolved once: re-resolving the original is rejected
    with pytest.raises(ValueError, match="already resolved"):
        doris.resolve_review(held["decision_id"], approve=False, reviewer="amartya", notes="no PO")


def test_human_review_can_reject_a_hold(doris):
    held = doris.check(raw("new_vendor_northwind.eml"))
    rejected = doris.resolve_review(held["decision_id"], approve=False, reviewer="amartya", notes="no PO")
    assert valid(rejected) and rejected["decision"] == "BLOCKED" and rejected["supersedes"] == held["decision_id"]


def test_resolving_an_unknown_decision_raises(doris):
    with pytest.raises(KeyError):
        doris.resolve_callback("dec_nope", vendor_confirmed=False, notes="")
    with pytest.raises(KeyError):
        doris.resolve_review("dec_nope", approve=True, reviewer="x", notes="")


def test_resolving_a_non_callback_decision_is_rejected(doris):
    paid = doris.check(raw("clean_acme.eml"))
    with pytest.raises(ValueError):
        doris.resolve_callback(paid["decision_id"], vendor_confirmed=True, notes="")


def test_callback_rejects_wrong_called_number(doris):
    first = doris.check(raw("bank_change_lookalike.eml"))
    assert first["decision"] == "CALLBACK"
    trusted = first["callback_number"]
    # calling a different number must be rejected
    with pytest.raises(ValueError, match="called.*on file"):
        doris.resolve_callback(first["decision_id"], vendor_confirmed=False, notes="x", called="+19999999999")
    # calling the trusted number works
    follow = doris.resolve_callback(first["decision_id"], vendor_confirmed=False, notes="we never changed our bank", called=trusted)
    assert follow["decision"] == "BLOCKED"
    # the signal records the trusted number that was actually called
    cb = next(s for s in follow["signals"] if s["name"] == "callback_result")
    assert cb["evidence"]["called"] == trusted


def test_callback_cannot_be_resolved_twice(doris):
    first = doris.check(raw("bank_change_lookalike.eml"))
    doris.resolve_callback(first["decision_id"], vendor_confirmed=False, notes="denied")
    # a second callback resolution on the same decision must be rejected
    with pytest.raises(ValueError, match="already resolved"):
        doris.resolve_callback(first["decision_id"], vendor_confirmed=True, notes="override")


def test_review_cannot_override_prompt_injection_block(doris):
    blocked = doris.check(raw("prompt_injection.eml"))
    assert blocked["decision"] == "BLOCKED"
    fired = {s["name"] for s in blocked["signals"] if s["fired"]}
    assert "prompt_injection" in fired
    # resolve_review must not let a human turn a prompt-injection BLOCKED into PAY
    with pytest.raises(ValueError, match="prompt_injection|hard block"):
        doris.resolve_review(blocked["decision_id"], approve=True, reviewer="amartya", notes="override")
    # but a human can still reject (stay BLOCKED)
    rejected = doris.resolve_review(blocked["decision_id"], approve=False, reviewer="amartya", notes="still fraud")
    assert rejected["decision"] == "BLOCKED"
    assert rejected["supersedes"] == blocked["decision_id"]


def test_email_bodies_are_not_stored(doris):
    doris.check(raw("bank_change_lookalike.eml"))
    for rec in doris.audit.records():
        text = json.dumps(rec.payload)
        assert "treasury provider" not in text and "Reply to this email" not in text
    intake = doris.audit.records(kind="intake")
    assert intake and len(intake[0].payload["input_sha256"]) == 64 and intake[0].payload["extraction"] == "offline"
    assert intake[0].payload["attachments"][0]["name"] == "inv_1042_acme_bank_change.pdf"


def test_chain_continues_across_instances(tmp_path):
    settings = Settings(offline=True, db_path=tmp_path / "doris.db", outbox_dir=tmp_path / "outbox")
    a = Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: NOW)
    first = a.check(raw("clean_acme.eml"))
    a.close()
    b = Doris(settings, seed=FIXTURES / "vendors.json", now=lambda: NOW)
    second = b.check(raw("clean_acme.eml"))
    # each check appends an intake record, then its decision: two records per check
    assert second["seq"] == first["seq"] + 2
    assert b.audit.get(second["seq"] - 1).kind == "intake" and b.audit.get(second["seq"] - 1).prev_hash == first["audit_hash"]
    assert len(b.vendors.list()) == 3 and b.verify().ok


def test_decision_ids_are_deterministic_for_fixed_time(tmp_path):
    def make():
        s = Settings(offline=True, db_path=tmp_path / f"{len(list(tmp_path.iterdir()))}.db", outbox_dir=tmp_path / "out")
        return Doris(s, seed=FIXTURES / "vendors.json", now=lambda: NOW).check(raw("clean_acme.eml"))
    assert make()["decision_id"] == make()["decision_id"]


def test_unreadable_attachment_forces_hold(doris, tmp_path, monkeypatch):
    # A PDF that cannot be read must force HOLD, never be silently dropped.
    from detector import ingest as ingest_mod
    from detector.models import DocumentText

    orig_pdf_text = ingest_mod.pdf_text
    def exploding_pdf(data):
        return DocumentText(visible="", hidden=[], line_count=0, error="PatternColor: unreadable")
    monkeypatch.setattr(ingest_mod, "pdf_text", exploding_pdf)

    d = doris.check(raw("clean_acme.eml"))
    assert d["decision"] == "HOLD"
    assert any("could not be read" in r for r in d["reasons"])
    # the chain still verifies
    assert doris.verify().ok
    ingest_mod.pdf_text = orig_pdf_text
