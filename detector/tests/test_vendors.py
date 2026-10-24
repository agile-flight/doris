"""Versioned vendor records and the 90-day rule.

The headline check: a contact changed under 90 days ago is never used for the callback.
"""
from datetime import datetime, timedelta, timezone

import pytest

from detector.audit import AuditLog
from detector.checks.vendor_contact import check_vendor_contact_changed
from detector.tests.conftest import FIXTURES
from detector.tests.test_checks import ctx
from detector.vendors import VendorStore

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
SINCE = datetime(2021, 3, 10, 17, 22, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    audit = AuditLog(tmp_path / "doris.db")
    return VendorStore(tmp_path / "doris.db", audit=audit)


def create_acme(store, **kw):
    fields = dict(domain="acmesupply.com", phone="+14155550123", email="billing@acmesupply.com",
                  contact_name="Dana Whitfield", bank_last4="1234", since=SINCE)
    fields.update(kw)
    return store.create("ven_acme_supply", "Acme Supply Co.", **fields)


def test_create_and_get(store):
    create_acme(store)
    v = store.get("ven_acme_supply")
    assert v.name == "Acme Supply Co." and v.domain == "acmesupply.com"
    assert v.phone == "+14155550123" and v.phone_since == SINCE
    assert v.bank_last4 == "1234" and v.bank_since == SINCE
    assert v.email_since == SINCE and v.created_at == SINCE
    assert v.last_contact_change_at is None and v.payment_history == []
    assert store.get("nope") is None


def test_create_twice_is_an_error(store):
    create_acme(store)
    with pytest.raises(ValueError):
        create_acme(store)


def test_phone_on_file_for_years_is_trusted(store):
    create_acme(store)
    v = store.get("ven_acme_supply")
    assert v.trusted_phone(NOW, 90) == ("+14155550123", SINCE)


def test_contact_changed_under_90_days_ago_is_never_used_for_the_callback(store):
    create_acme(store)
    changed_at = NOW - timedelta(days=89)
    store.update_field("ven_acme_supply", "phone", "+19995550000", at=changed_at, source="email request")
    v = store.get("ven_acme_supply")
    assert v.phone == "+19995550000" and v.phone_since == changed_at
    assert v.trusted_phone(NOW, 90) == (None, None)
    assert v.last_contact_change_at == changed_at and v.last_contact_change_field == "phone"
    signal = check_vendor_contact_changed(ctx(vendor=v))
    assert signal.fired and "89 days ago" in signal.detail


def test_contact_changed_90_days_ago_is_on_file_again(store):
    create_acme(store)
    store.update_field("ven_acme_supply", "phone", "+19995550000", at=NOW - timedelta(days=90), source="verified by call")
    v = store.get("ven_acme_supply")
    assert v.trusted_phone(NOW, 90) == ("+19995550000", NOW - timedelta(days=90))
    assert not check_vendor_contact_changed(ctx(vendor=v)).fired


def test_old_number_is_not_used_after_a_recent_change(store):
    """The poisoning attack: swap the phone first, then ask for the bank change. Neither the new
    nor the old number is offered for the callback until the new one has aged 90 days."""
    create_acme(store)
    store.update_field("ven_acme_supply", "phone", "+19995550000", at=NOW - timedelta(days=10), source="email request")
    v = store.get("ven_acme_supply")
    number, since = v.trusted_phone(NOW, 90)
    assert number is None and since is None
    assert "+14155550123" not in (v.phone,)


def test_every_edit_is_an_audit_record(store, tmp_path):
    create_acme(store)
    store.update_field("ven_acme_supply", "email", "ar@acmesupply.com", at=NOW - timedelta(days=5), source="phone call")
    records = store.audit.records(kind="vendor_edit")
    assert len(records) == 1
    p = records[0].payload
    assert p["vendor_id"] == "ven_acme_supply" and p["field"] == "email"
    assert p["old"] == "billing@acmesupply.com" and p["new"] == "ar@acmesupply.com" and p["source"] == "phone call"
    assert store.audit.records(kind="vendor_created")[0].payload["vendor_id"] == "ven_acme_supply"
    assert store.audit.verify().ok


def test_bank_edit_updates_bank_since_and_is_logged(store):
    create_acme(store)
    at = NOW - timedelta(days=1)
    store.update_field("ven_acme_supply", "bank_last4", "8841", at=at, source="callback confirmed")
    v = store.get("ven_acme_supply")
    assert v.bank_last4 == "8841" and v.bank_since == at
    assert v.last_contact_change_at is None  # bank is not a contact field
    assert store.audit.records(kind="vendor_edit")[-1].payload["field"] == "bank_last4"


def test_history_keeps_every_version(store):
    create_acme(store)
    store.update_field("ven_acme_supply", "phone", "+19995550000", at=NOW - timedelta(days=10), source="x")
    history = store.history("ven_acme_supply", "phone")
    assert [h["value"] for h in history] == ["+14155550123", "+19995550000"]
    assert history[0]["since"] == SINCE.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_payments_build_history(store):
    create_acme(store)
    for i, amount in enumerate([4650.0, 4820.0, 4400.0]):
        store.record_payment("ven_acme_supply", amount, invoice_id=f"INV-{i}", at=NOW - timedelta(days=90 - 30 * i))
    v = store.get("ven_acme_supply")
    assert v.payment_history == [4650.0, 4820.0, 4400.0]


def test_match_by_domain_name_or_payee(store):
    create_acme(store)
    store.create("ven_northwind", "Northwind Traders Inc.", domain="northwindtraders.com", phone="+13125550199",
                 email="ar@northwindtraders.com", contact_name="Priya Raman", bank_last4="5520", since=NOW - timedelta(days=400))
    m = store.match
    assert m(vendor_claimed="Whatever", sender_domain="acmesupply.com", payee_name=None).vendor_id == "ven_acme_supply"
    assert m(vendor_claimed="Acme Supply", sender_domain="gmail.com", payee_name=None).vendor_id == "ven_acme_supply"
    assert m(vendor_claimed="Acme Supply Billing", sender_domain="acmesupply-billing.com", payee_name="Acme Supply Co.").vendor_id == "ven_acme_supply"
    assert m(vendor_claimed="Acme Supply Accounts", sender_domain="acme-supply.co", payee_name="Acme Supply Co").vendor_id == "ven_acme_supply"
    assert m(vendor_claimed="Northwind Traders AR", sender_domain="northwindtraders.com", payee_name="Northwind Traders Inc.").vendor_id == "ven_northwind"
    assert m(vendor_claimed="Globex Corporation", sender_domain="globex.example", payee_name="Globex Corp") is None


def test_lookalike_domain_does_not_match_by_domain_alone(store):
    create_acme(store)
    # Only the exact domain matches by domain; a lookalike must earn the match by name or payee.
    assert store.match(vendor_claimed="Totally Different", sender_domain="acmesupply-billing.com", payee_name="Someone Else") is None


def test_known_domains_and_listing(store):
    create_acme(store)
    store.create("ven_x", "X Corp", domain="x.example", phone=None, email=None, contact_name=None, bank_last4=None, since=NOW)
    assert store.known_domains() == ["acmesupply.com", "x.example"]
    assert [v.vendor_id for v in store.list()] == ["ven_acme_supply", "ven_x"]


def test_seed_from_fixture(store):
    store.seed(FIXTURES / "vendors.json")
    v = store.get("ven_acme_supply")
    assert v.phone_since == SINCE and len(v.payment_history) == 5
    assert store.known_domains() == ["acmesupply.com", "bayviewjanitorial.com", "lakesideit.com"]
    store.seed(FIXTURES / "vendors.json")  # idempotent
    assert len(store.list()) == 3
    assert store.audit.verify().ok


def test_persists_across_reopen(tmp_path):
    first = VendorStore(tmp_path / "v.db", audit=AuditLog(tmp_path / "v.db"))
    first.create("ven_a", "A Co", domain="a.example", phone="+14155550101", email=None, contact_name=None, bank_last4="0001", since=SINCE)
    first.close()
    again = VendorStore(tmp_path / "v.db", audit=AuditLog(tmp_path / "v.db"))
    assert again.get("ven_a").phone == "+14155550101"
