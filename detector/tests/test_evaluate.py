"""The confusion-matrix script prints catch rate and false-positive rate on the test set."""
import json

from detector.evaluate import Report, compute_metrics, load_testset, run_evaluation
from detector.pipeline import FIXTURES

TESTSET = FIXTURES.parent / "testset" / "cases.json"
DOJ_TESTSET = FIXTURES.parent / "testset" / "cases_doj.json"


def test_testset_loads_and_is_balanced():
    cases = load_testset(TESTSET)
    labels = [c["label"] for c in cases]
    assert labels.count("fraud") >= 12 and labels.count("legit") >= 10
    assert len({c["id"] for c in cases}) == len(cases)


def test_metrics_math():
    rows = [
        {"label": "fraud", "decision": "BLOCKED"}, {"label": "fraud", "decision": "CALLBACK"},
        {"label": "fraud", "decision": "HOLD"}, {"label": "fraud", "decision": "PAY"},
        {"label": "legit", "decision": "PAY"}, {"label": "legit", "decision": "PAY"},
        {"label": "legit", "decision": "HOLD"}, {"label": "legit", "decision": "BLOCKED"},
    ]
    m = compute_metrics(rows)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (3, 1, 2, 2)
    assert m["catch_rate"] == 0.75 and m["false_positive_rate"] == 0.5
    assert m["legit_blocked"] == 1 and m["legit_blocked_rate"] == 0.25
    assert m["legit_by_decision"] == {"PAY": 2, "HOLD": 1, "BLOCKED": 1}


def test_run_evaluation_offline(tmp_path):
    report = run_evaluation(testset=TESTSET, out=tmp_path / "cm.json", offline=True)
    assert isinstance(report, Report)
    assert report.metrics["total"] == len(load_testset(TESTSET))
    assert report.metrics["catch_rate"] >= 0.9
    assert report.metrics["legit_blocked"] == 0
    by_id = {r["id"]: r for r in report.rows}
    assert by_id["fraud_iban_soft_language"]["decision"] == "PAY"  # the documented offline miss
    assert by_id["fraud_prompt_injection_pdf"]["decision"] == "BLOCKED"
    assert by_id["legit_new_vendor_first_invoice"]["decision"] == "HOLD"
    assert by_id["legit_genuine_bank_change"]["decision"] == "CALLBACK"
    assert all(r["decision"] in ("PAY", "HOLD", "CALLBACK", "BLOCKED") and r["risk_score"] is not None for r in report.rows)
    text = report.render()
    assert "Catch rate" in text and "False-positive rate" in text and "fraud_iban_soft_language" in text
    saved = json.loads((tmp_path / "cm.json").read_text())
    assert saved["metrics"] == report.metrics and len(saved["rows"]) == len(report.rows)


# --- the DOJ-derived set (a separate deliverable) -----------------------------------

def test_doj_testset_loads_and_is_balanced():
    cases = load_testset(DOJ_TESTSET)
    labels = [c["label"] for c in cases]
    assert labels.count("fraud") >= 12 and labels.count("legit") >= 8
    assert len({c["id"] for c in cases}) == len(cases)
    assert all(c.get("note") for c in cases), "every DOJ case must cite the typology it comes from"
    raw = json.loads(DOJ_TESTSET.read_text())
    assert raw["_sources"] and any("Rimasauskas" in s for s in raw["_sources"])


def test_doj_set_is_caught_with_no_legitimate_block(tmp_path):
    report = run_evaluation(testset=DOJ_TESTSET, out=tmp_path / "doj.json", offline=True)
    m = report.metrics
    assert m["total"] == 20 and m["fraud_total"] == 12 and m["legit_total"] == 8
    # every reconstructed DOJ typology is stopped before payment
    assert m["catch_rate"] == 1.0
    # nothing legitimate is blocked outright
    assert m["legit_blocked"] == 0
    # the only legitimate non-PAY decisions are the two by-design frictions
    assert m["legit_by_decision"] == {"PAY": 6, "HOLD": 1, "CALLBACK": 1}
    assert all(r["decision"] in ("PAY", "HOLD", "CALLBACK", "BLOCKED") for r in report.rows)
    assert all(0.0 <= r["risk_score"] <= 1.0 for r in report.rows)


def test_doj_cases_are_caught_for_the_documented_reason(tmp_path):
    report = run_evaluation(testset=DOJ_TESTSET, out=tmp_path / "doj.json", offline=True)
    by_id = {r["id"]: r for r in report.rows}
    # every DOJ fraud reconstruction changes the remittance account
    assert all("bank_account_changed" in by_id[f"{c['id']}"]["fired"]
               for c in load_testset(DOJ_TESTSET) if c["label"] == "fraud")
    # vendor email compromise: no narrative at all, so only the account comparison catches it
    vec = by_id["doj_vendor_email_compromise_mailbox"]
    assert vec["decision"] == "CALLBACK" and vec["fired"] == ["bank_account_changed"]
    # a forged invoice in a real supplier's name is caught by the payee, not the domain
    forged = by_id["doj_rimasauskas_forged_supplier_invoice"]
    assert forged["decision"] == "CALLBACK" and "payee_name_mismatch" in forged["fired"]
    assert "lookalike_domain" not in forged["fired"]
    # hidden white text in an HTML body blocks outright
    assert by_id["doj_hidden_text_instruction_html"]["decision"] == "BLOCKED"
    # the two by-design frictions
    assert by_id["control_genuine_bank_change"]["decision"] == "CALLBACK"
    assert by_id["control_first_time_vendor"]["decision"] == "HOLD"
    assert by_id["control_routine_acme"]["decision"] == "PAY"
