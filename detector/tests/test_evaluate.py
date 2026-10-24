"""The confusion-matrix script prints catch rate and false-positive rate on the test set."""
import json

from detector.evaluate import Report, compute_metrics, load_testset, run_evaluation
from detector.pipeline import FIXTURES

TESTSET = FIXTURES.parent / "testset" / "cases.json"


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
