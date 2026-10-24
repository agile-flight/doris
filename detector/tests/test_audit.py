"""The hash-chained audit log. Tamper test: edit one record, verification must fail."""
import json
import sqlite3
from pathlib import Path

import pytest

from detector.audit import GENESIS, AuditLog, canonical, compute_hash, verify_decision

SAMPLES = Path(__file__).resolve().parents[2] / "contract" / "samples"


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit.db")


def test_genesis_and_first_record(log):
    rec = log.append("decision", {"invoice_id": "inv_1", "created_at": "2026-10-22T08:41:12Z", "decision": "PAY"})
    assert rec.seq == 1
    assert rec.prev_hash == GENESIS
    assert rec.hash.startswith("sha256:") and len(rec.hash) == 71
    assert rec.payload["seq"] == 1 and rec.payload["prev_hash"] == GENESIS and rec.payload["created_at"] == "2026-10-22T08:41:12Z"


def test_created_at_is_added_when_missing(log):
    rec = log.append("vendor_edit", {"vendor_id": "ven_x", "field": "phone"})
    assert rec.payload["created_at"].endswith("Z")


def test_chain_links_each_record_to_the_previous(log):
    a = log.append("decision", {"invoice_id": "a"})
    b = log.append("vendor_edit", {"vendor_id": "v"})
    c = log.append("decision", {"invoice_id": "c"})
    assert b.prev_hash == a.hash and c.prev_hash == b.hash
    assert log.head() == (3, c.hash)


def test_hash_recomputes_from_stored_payload(log):
    rec = log.append("decision", {"invoice_id": "inv_1", "amount": 4820.0})
    assert compute_hash(rec.prev_hash, rec.payload) == rec.hash
    # canonical form is what the contract documents: sorted keys, compact separators
    assert canonical({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_verify_ok_on_empty_and_populated_log(log):
    assert log.verify().ok and log.verify().records == 0
    for i in range(5):
        log.append("decision", {"invoice_id": f"inv_{i}"})
    result = log.verify()
    assert result.ok and result.records == 5 and result.first_bad_seq is None


def test_tamper_payload_breaks_verification(log, tmp_path):
    for i in range(3):
        log.append("decision", {"invoice_id": f"inv_{i}", "amount": 100.0})
    con = sqlite3.connect(tmp_path / "audit.db")
    row = con.execute("SELECT payload FROM audit_log WHERE seq = 2").fetchone()[0]
    payload = json.loads(row)
    payload["amount"] = 1.0
    con.execute("UPDATE audit_log SET payload = ? WHERE seq = 2", (json.dumps(payload),))
    con.commit(); con.close()
    result = log.verify()
    assert result.ok is False
    assert result.first_bad_seq == 2
    assert "hash" in result.reason.lower()


def test_tamper_hash_breaks_the_next_link(log, tmp_path):
    for i in range(3):
        log.append("decision", {"invoice_id": f"inv_{i}"})
    con = sqlite3.connect(tmp_path / "audit.db")
    con.execute("UPDATE audit_log SET hash = ? WHERE seq = 2", ("sha256:" + "f" * 64,))
    con.commit(); con.close()
    result = log.verify()
    assert result.ok is False and result.first_bad_seq == 2


def test_deleting_a_record_breaks_verification(log, tmp_path):
    for i in range(3):
        log.append("decision", {"invoice_id": f"inv_{i}"})
    con = sqlite3.connect(tmp_path / "audit.db")
    con.execute("DELETE FROM audit_log WHERE seq = 2")
    con.commit(); con.close()
    result = log.verify()
    assert result.ok is False and result.first_bad_seq == 3


def test_persists_across_reopen(tmp_path):
    first = AuditLog(tmp_path / "a.db")
    rec = first.append("decision", {"invoice_id": "x"})
    first.close()
    again = AuditLog(tmp_path / "a.db")
    assert again.head() == (1, rec.hash)
    nxt = again.append("decision", {"invoice_id": "y"})
    assert nxt.prev_hash == rec.hash and again.verify().ok


def test_records_listing_and_kind_filter(log):
    log.append("decision", {"invoice_id": "a"})
    log.append("vendor_edit", {"vendor_id": "v"})
    log.append("decision", {"invoice_id": "b"})
    assert [r.kind for r in log.records()] == ["decision", "vendor_edit", "decision"]
    assert [r.payload["invoice_id"] for r in log.records(kind="decision")] == ["a", "b"]
    assert [r.seq for r in log.records(newest_first=True, limit=2)] == [3, 2]
    assert log.get(2).kind == "vendor_edit" and log.get(99) is None


def test_verify_decision_standalone_matches_contract_samples():
    for path in SAMPLES.glob("*.json"):
        d = json.loads(path.read_text())
        assert verify_decision(d), path.name
        d["decision"] = "TAMPERED"
        assert not verify_decision(d)
