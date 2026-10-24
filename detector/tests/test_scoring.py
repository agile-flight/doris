"""Risk score and decision rules. The white-text invoice must score maximum risk and block."""
from datetime import datetime, timezone

from detector.checks import Signal, run_checks
from detector.config import Settings
from detector.ingest import extract_documents, ingest
from detector.scoring import build_reasons, combine, decide, pretty_phone, score
from detector.tests.test_checks import CT, NOW, SINCE, acme, ctx, invoice, msg

S = Settings(offline=True)


def sig(name, fired=True, weight="high", detail="d", evidence=None):
    return Signal(name, fired, weight, detail, evidence)


def test_combine_is_noisy_or_over_fired_weights():
    assert combine([], None, S) == 0.0
    assert combine([sig("bank_account_changed", weight="highest")], None, S) == 0.6
    assert combine([sig("bank_account_changed", weight="highest"), sig("lookalike_domain", weight="high")], None, S) == 0.72
    assert combine([sig("bank_account_changed", False, "highest")], None, S) == 0.0


def test_combine_ignores_gate_and_residual_tiers():
    assert combine([sig("new_vendor", weight="gate"), sig("llm_judgment", weight="residual")], None, S) == 0.0


def test_combine_blends_llm_risk_into_the_residual():
    # det 0.72, llm 0.5 at weight 0.5 -> 1 - 0.28 * 0.75
    assert combine([sig("bank_account_changed", weight="highest"), sig("lookalike_domain", weight="high")], 0.5, S) == 0.79
    assert combine([], 1.0, S) == 0.5
    assert combine([], 0.04, S) == 0.02


def test_decide_prompt_injection_blocks_even_with_callback_number():
    signals = [sig("prompt_injection", weight="highest"), sig("bank_account_changed", weight="highest")]
    assert decide(signals, 0.5, callback_number="+14155550123", settings=S) == "BLOCKED"


def test_decide_bank_change_calls_back_when_number_is_trusted():
    signals = [sig("bank_account_changed", weight="highest"), sig("lookalike_domain")]
    assert decide(signals, 0.95, callback_number="+14155550123", settings=S) == "CALLBACK"
    assert decide(signals, 0.95, callback_number=None, settings=S) == "BLOCKED"
    assert decide(signals, 0.7, callback_number=None, settings=S) == "HOLD"


def test_decide_thresholds_and_gates():
    assert decide([sig("payee_name_mismatch")], 0.9, callback_number="+14155550123", settings=S) == "BLOCKED"
    assert decide([sig("new_vendor", weight="gate")], 0.05, callback_number=None, settings=S) == "HOLD"
    assert decide([], 0.5, callback_number="+14155550123", settings=S) == "HOLD"
    assert decide([], 0.29, callback_number="+14155550123", settings=S) == "PAY"
    assert decide([], 0.30, callback_number="+14155550123", settings=S) == "HOLD"


def test_white_text_invoice_scores_maximum_risk_and_blocks(fixtures):
    m = ingest((fixtures / "emails" / "prompt_injection.eml").read_bytes())
    docs = extract_documents(m)
    c = ctx(message=m, docs=docs, inv=invoice(payee_name="Blue Horizon Automobile LLC", bank_account_last4="8841", bank_change_requested=True), ct=CT)
    signals = run_checks(c)
    verdict = score(signals, llm_risk=None, callback_number="+14155550123", callback_since=SINCE, vendor=acme(), settings=S)
    assert verdict.decision == "BLOCKED"
    assert verdict.risk_score >= 0.95
    assert verdict.hard_block is True
    assert verdict.reasons[0].startswith("Hidden white text on line 14")
    assert any("Blue Horizon Automobile LLC" in r for r in verdict.reasons)


def test_clean_invoice_pays_with_routine_reasons():
    signals = run_checks(ctx(ct=CT))
    verdict = score(signals, llm_risk=0.04, callback_number="+14155550123", callback_since=SINCE, vendor=acme(), settings=S)
    assert verdict.decision == "PAY"
    assert verdict.risk_score == 0.02
    assert verdict.reasons == ["Routine invoice from a vendor on file", "Bank account unchanged (****1234)", "Amount within normal range"]


def test_bank_change_reasons_end_with_the_callback_line():
    m = msg(sender="billing@acmesupply-billing.com")
    signals = run_checks(ctx(message=m, inv=invoice(bank_account_last4="8841", bank_change_requested=True, urgency_phrases=["before friday's"]), ct=CT))
    verdict = score(signals, llm_risk=0.72, callback_number="+14155550123", callback_since=SINCE, vendor=acme(), settings=S)
    assert verdict.decision == "CALLBACK"
    assert 0.7 <= verdict.risk_score <= 0.9
    assert verdict.reasons[0] == "Bank account change requested: ****1234 to ****8841"
    assert verdict.reasons[-1] == "Doris is calling +1 415 555 0123, on file since March 2021"
    assert any("lookalike" in r for r in verdict.reasons)
    assert any("first certificate" in r for r in verdict.reasons)


def test_new_vendor_holds_with_explanation():
    signals = run_checks(ctx(vendor=None, inv=invoice(payee_name="Northwind Traders Inc.", bank_account_last4="5520")))
    verdict = score(signals, llm_risk=None, callback_number=None, callback_since=None, vendor=None, settings=S)
    assert verdict.decision == "HOLD"
    assert verdict.reasons[0].startswith("First-time vendor")
    assert verdict.reasons[-1] == "Human review required before the first payment"


def test_bank_change_without_trusted_number_holds_and_says_why():
    signals = [sig("bank_account_changed", weight="highest", detail="d", evidence={"new_last4": "8841", "on_file_last4": "1234"})]
    verdict = score(signals, llm_risk=None, callback_number=None, callback_since=None, vendor=acme(), settings=S)
    assert verdict.decision == "HOLD"
    assert "verify" in verdict.reasons[-1].lower() and "90" in verdict.reasons[-1]


def test_pretty_phone():
    assert pretty_phone("+14155550123") == "+1 415 555 0123"
    assert pretty_phone("+442071234567") == "+442071234567"
    assert pretty_phone(None) is None


def test_risk_is_bounded_and_rounded():
    signals = [sig(n, weight="highest") for n in ("bank_account_changed", "prompt_injection")] + [sig("lookalike_domain"), sig("payee_name_mismatch")]
    verdict = score(signals, llm_risk=1.0, callback_number=None, callback_since=None, vendor=acme(), settings=S)
    assert 0.0 <= verdict.risk_score <= 1.0
    assert verdict.risk_score == round(verdict.risk_score, 2)
