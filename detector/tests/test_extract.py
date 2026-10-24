import json
from pathlib import Path

import pytest

from detector.extract import (
    detect_bank_change, detect_contact_change, detect_currency, extract, extract_offline,
    find_account_last4, find_amount, find_invoice_number, find_payee, find_routing_last4,
    find_urgency, mask_last4, parse_amount,
)
from detector.ingest import extract_documents, ingest
from detector.tests.conftest import FIXTURES

FIXTURE_DIFF_FIELDS = [
    "vendor_claimed", "payee_name", "amount", "currency", "invoice_number",
    "bank_account_last4", "routing_number_last4", "bank_change_requested",
    "contact_change_requested", "urgency_phrases",
]
EMAILS = sorted((FIXTURES / "emails").iterdir())


@pytest.mark.parametrize("path", EMAILS, ids=[p.stem for p in EMAILS])
def test_fixture_diff_offline_extraction(path: Path):
    """The five sample invoices extract to the expected fields."""
    raw = path.read_bytes() if path.suffix == ".eml" else path.read_text()
    msg = ingest(raw)
    docs = extract_documents(msg)
    got = extract_offline(msg, docs).model_dump()
    expected = json.loads((FIXTURES / "expected" / f"{path.stem}.json").read_text())
    assert {k: got[k] for k in FIXTURE_DIFF_FIELDS} == expected


def test_extract_falls_back_to_offline_without_llm(fixtures):
    msg = ingest((fixtures / "emails" / "clean_acme.eml").read_bytes())
    invoice, method = extract(msg, extract_documents(msg), llm=None)
    assert method == "offline"
    assert invoice.invoice_number == "INV-1041"


def test_parse_amount():
    assert parse_amount("$12,400.00") == 12400.0
    assert parse_amount("4,820") == 4820.0
    assert parse_amount("USD 1250.50") == 1250.5
    assert parse_amount("n/a") is None


def test_find_amount_prefers_total_due():
    text = "Line item $700.00\nSubtotal: $4,820.00\nTotal Due: $4,820.00\nPrevious balance $9,000.00"
    assert find_amount(text) == 4820.0


def test_find_amount_falls_back_to_largest_money_amount():
    assert find_amount("please pay invoice INV-9 ($12,400.00), late fee was $25.00") == 12400.0
    assert find_amount("no money here") is None


def test_find_account_and_routing_last4():
    text = "Routing Number: 026009593\nAccount Number: 7002 3311 8841"
    assert find_account_last4(text) == "8841"
    assert find_routing_last4(text) == "9593"
    assert find_account_last4("Account: none") is None


def test_mask_last4():
    assert mask_last4("8841") == "****8841"
    assert mask_last4(None) is None


def test_find_payee_variants():
    assert find_payee("Remit to: Acme Supply Co.\nBank: X") == "Acme Supply Co."
    assert find_payee("Beneficiary: Acme Supply Co\n") == "Acme Supply Co"
    assert find_payee("Pay to: Blue Horizon Automobile LLC") == "Blue Horizon Automobile LLC"
    assert find_payee("nothing") is None


def test_find_invoice_number_prefers_strict_form():
    assert find_invoice_number("Subject: Invoice INV-1041 from Acme\nInvoice Number: INV-1041") == "INV-1041"
    assert find_invoice_number("re: INV-1046 payment") == "INV-1046"
    assert find_invoice_number("Invoice # 20261022") == "20261022"
    assert find_invoice_number("hello") is None


def test_detect_bank_change_positive_and_negative():
    for s in [
        "our bank account has changed",
        "Please note our updated banking details",
        "remittance details have changed",
        "Note the new remit-to details on the invoice",
        "we have moved to a new treasury provider",
        "please update the account on file",
        "Updated remittance instructions",
    ]:
        assert detect_bank_change(s), s
    for s in ["Payment terms are net 30 as usual", "our remittance details are on the invoice", "thank you for your business"]:
        assert not detect_bank_change(s), s


def test_detect_contact_change():
    assert detect_contact_change("Please use our new phone number 415-555-0100 going forward")
    assert detect_contact_change("our AP contact has changed, reach us at +1 415 555 0100")
    assert not detect_contact_change("Please find attached invoice INV-1041")


def test_find_urgency_keeps_order_and_dedupes():
    text = "URGENT: pay today. This is urgent - payment must go out today to avoid service interruption."
    assert find_urgency(text) == ["urgent", "today", "avoid service interruption"]
    assert find_urgency("This must be updated before Friday's payment run. Effective immediately.") == ["before friday's", "effective immediately"]
    assert find_urgency("Payment terms are net 30 as usual") == []


def test_detect_currency():
    assert detect_currency("Total Due: $4,820.00") == "USD"
    assert detect_currency("Total €1.200,00") == "EUR"
    assert detect_currency("Total £900.00") == "GBP"
    assert detect_currency("Total 900.00") == "USD"
